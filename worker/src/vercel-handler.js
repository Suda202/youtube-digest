const DEFAULT_REPO_OWNER = "Suda202";
const DEFAULT_REPO_NAME = "youtube-digest";
const DEFAULT_FEEDBACK_BRANCH = "data";
const DEFAULT_FEEDBACK_FILE = "feedback.json";

function sendJson(response, data, status = 200) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(data));
}

function decodeBase64(value) {
  return Buffer.from(value.replace(/\n/g, ""), "base64").toString("utf8");
}

function encodeBase64(value) {
  return Buffer.from(value, "utf8").toString("base64");
}

function githubConfig(env) {
  return {
    owner: env.REPO_OWNER || DEFAULT_REPO_OWNER,
    repo: env.REPO_NAME || DEFAULT_REPO_NAME,
    branch: env.FEEDBACK_BRANCH || DEFAULT_FEEDBACK_BRANCH,
    file: env.FEEDBACK_FILE || DEFAULT_FEEDBACK_FILE,
  };
}

async function readGithubFile(env) {
  const config = githubConfig(env);
  const url = `https://api.github.com/repos/${config.owner}/${config.repo}/contents/${config.file}?ref=${config.branch}`;
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      "User-Agent": "youtube-digest-feedback-vercel",
    },
  });

  if (response.status === 404) {
    return { sha: null, data: {} };
  }
  if (!response.ok) {
    throw new Error(`GitHub read failed: ${response.status} ${await response.text()}`);
  }

  const payload = await response.json();
  return {
    sha: payload.sha,
    data: JSON.parse(decodeBase64(payload.content || "e30=")),
  };
}

async function writeGithubFile(env, sha, data) {
  const config = githubConfig(env);
  const url = `https://api.github.com/repos/${config.owner}/${config.repo}/contents/${config.file}`;
  const body = {
    message: "chore: update youtube feedback",
    branch: config.branch,
    content: encodeBase64(JSON.stringify(data, null, 2)),
  };
  if (sha) body.sha = sha;

  const response = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "youtube-digest-feedback-vercel",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`GitHub write failed: ${response.status} ${await response.text()}`);
  }
}

function normalizeActionValue(rawValue) {
  if (!rawValue) return {};
  if (typeof rawValue === "object") return rawValue;
  if (typeof rawValue === "string") {
    try {
      return JSON.parse(rawValue);
    } catch {
      return {};
    }
  }
  return {};
}

function extractFeedback(payload) {
  const event = payload.event || payload;
  const action = event.action || payload.action || {};
  const value = normalizeActionValue(action.value);

  const videoId = value.video_id || value.videoId;
  const reaction = value.action;
  if (!videoId || !["like", "dislike"].includes(reaction)) {
    return null;
  }

  return {
    videoId,
    reaction,
    reason: value.reason || null,
    videoMeta: {
      title: value.title || "",
      author: value.author || "",
      url: value.url || `https://www.youtube.com/watch?v=${videoId}`,
    },
  };
}

async function recordFeedbackOnce(env, feedbackData) {
  const { sha, data } = await readGithubFile(env);
  const current = data[feedbackData.videoId] || {
    video_meta: feedbackData.videoMeta,
    reactions: [],
  };

  current.video_meta = { ...current.video_meta, ...feedbackData.videoMeta };
  current.reactions = current.reactions || [];
  current.reactions.push({
    reaction: feedbackData.reaction,
    reason: feedbackData.reason,
    timestamp: new Date().toISOString(),
  });
  data[feedbackData.videoId] = current;

  await writeGithubFile(env, sha, data);
}

async function recordFeedback(env, feedbackData) {
  if (!env.GH_TOKEN) {
    throw new Error("Missing GH_TOKEN");
  }

  try {
    await recordFeedbackOnce(env, feedbackData);
  } catch (error) {
    if (!String(error.message || "").includes("409")) {
      throw error;
    }
    await recordFeedbackOnce(env, feedbackData);
  }
}

async function readJsonBody(request) {
  if (request.body !== undefined && request.body !== null) {
    if (Buffer.isBuffer(request.body)) {
      return JSON.parse(request.body.toString("utf8") || "{}");
    }
    if (typeof request.body === "string") {
      return JSON.parse(request.body || "{}");
    }
    return request.body;
  }

  let raw = "";
  for await (const chunk of request) {
    raw += chunk;
  }
  return JSON.parse(raw || "{}");
}

async function handler(request, response) {
  try {
    if (request.method === "GET") {
      return sendJson(response, { status: "ok" });
    }

    if (request.method !== "POST") {
      return sendJson(response, { error: "Method not allowed" }, 405);
    }

    let payload;
    try {
      payload = await readJsonBody(request);
    } catch {
      return sendJson(response, { error: "Invalid JSON" }, 400);
    }

    if (
      process.env.FEISHU_VERIFICATION_TOKEN &&
      payload.token &&
      payload.token !== process.env.FEISHU_VERIFICATION_TOKEN
    ) {
      return sendJson(response, { error: "Invalid Feishu verification token" }, 401);
    }
    if (payload.challenge) {
      return sendJson(response, { challenge: payload.challenge });
    }

    const feedbackData = extractFeedback(payload);
    if (!feedbackData) {
      return sendJson(response, {});
    }

    await recordFeedback(process.env, feedbackData);
    const label = feedbackData.reaction === "like" ? "已记录：有用" : "已记录：不想看";
    return sendJson(response, { toast: { type: "success", content: label } });
  } catch (error) {
    console.error(error);
    return sendJson(response, { error: error.message }, 500);
  }
}

module.exports = handler;
module.exports.default = handler;
