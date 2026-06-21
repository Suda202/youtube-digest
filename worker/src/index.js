/**
 * Feishu card callback worker for YouTube Digest.
 *
 * Records one-click card feedback into GitHub data branch feedback.json.
 */

const DEFAULT_REPO_OWNER = "Suda202";
const DEFAULT_REPO_NAME = "youtube-digest";
const DEFAULT_FEEDBACK_BRANCH = "data";
const DEFAULT_FEEDBACK_FILE = "feedback.json";
const SUMMARY_PROMPT_LEAK_FALLBACK = "⚠️ 摘要生成异常，已隐藏提示词内容。请直接打开视频判断。";
const SUMMARY_PROMPT_LEAK_MARKERS = [
  "根据以下视频",
  "视频标题：",
  "视频字幕：",
  "视频描述：",
  "格式要求",
  "纯文本，不要 markdown",
  "第一行用",
  "最后一行用",
  "全文控制",
  "max_tokens",
  "messages",
];

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function htmlResponse(content, status = 200) {
  return new Response(content, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function feedbackSuccessHtml(feedbackData) {
  const label = feedbackData.reaction === "like" ? "有用" : "不想看";
  const title = escapeHtml(feedbackData.contentMeta.title || feedbackData.contentId);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>反馈已记录</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8fa; color: #1f2329; }
    main { max-width: 520px; margin: 14vh auto 0; padding: 32px 24px; background: white; border: 1px solid #dee0e3; border-radius: 8px; }
    h1 { margin: 0 0 12px; font-size: 22px; }
    p { margin: 8px 0; line-height: 1.6; color: #4e5969; }
    .label { color: #1664ff; font-weight: 600; }
  </style>
</head>
<body>
  <main>
    <h1>反馈已记录</h1>
    <p>这条内容已标记为 <span class="label">${label}</span>。</p>
    <p>${title}</p>
    <p>可以关闭这个页面回到飞书。</p>
  </main>
</body>
</html>`;
}

function decodeBase64(value) {
  const bytes = Uint8Array.from(atob(value.replace(/\n/g, "")), (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function encodeBase64(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
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
      "User-Agent": "youtube-digest-feedback-worker",
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
      "User-Agent": "youtube-digest-feedback-worker",
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

function normalizeSelectionTags(value) {
  if (Array.isArray(value)) return value.map(String).filter(Boolean).slice(0, 5);
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean).slice(0, 5);
  } catch {
    return value.split(",").map((tag) => tag.trim()).filter(Boolean).slice(0, 5);
  }
  return [];
}

function feedbackEventId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function feedbackDataFromValue(value) {
  const contentId = value.content_id || value.contentId || value.video_id || value.videoId;
  const reaction = value.action;
  if (!contentId || !["like", "dislike"].includes(reaction)) return null;

  const contentType = value.content_type || value.contentType || (
    String(contentId).startsWith("aihot:") ? "aihot" : "youtube"
  );
  const creator = value.creator || value.author || "";
  const defaultUrl = contentType === "youtube"
    ? `https://www.youtube.com/watch?v=${contentId}`
    : "";
  const contentMeta = {
    content_id: contentId,
    content_type: contentType,
    title: value.title || "",
    creator,
    url: value.url || defaultUrl,
    category: value.category || "",
    selection_tags: normalizeSelectionTags(value.selection_tags || value.selectionTags),
  };

  return {
    contentId,
    contentType,
    reaction,
    reason: value.reason || null,
    cardState: value.card_state || value.cardState || null,
    feedbackState: value.feedback_state || value.feedbackState || {},
    contentMeta,
    videoId: contentId,
    videoMeta: {
      title: contentMeta.title,
      author: creator,
      url: contentMeta.url,
    },
  };
}

function extractFeedback(payload) {
  const event = payload.event || payload;
  const action = event.action || payload.action || {};
  const value = normalizeActionValue(action.value);

  return feedbackDataFromValue(value);
}

function formatViewCount(count) {
  const value = Number(count || 0);
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

function looksLikeSummaryPromptLeak(summary) {
  const text = String(summary || "").trim();
  if (!text) return false;

  const normalized = text.toLowerCase();
  const markerCount = SUMMARY_PROMPT_LEAK_MARKERS.filter((marker) => (
    normalized.includes(marker.toLowerCase())
  )).length;
  return markerCount >= 2 || (text.includes("格式要求") && text.includes("视频标题"));
}

function sanitizeSummary(summary) {
  const text = String(summary || "").trim();
  if (!text) return "";
  if (looksLikeSummaryPromptLeak(text)) return SUMMARY_PROMPT_LEAK_FALLBACK;
  return text;
}

function sanitizeCardState(cardState) {
  return {
    ...(cardState || {}),
    items: (cardState?.items || []).map((item) => ({
      ...item,
      summary: sanitizeSummary(item.summary),
    })),
  };
}

function buildFeedbackValue(video, action, cardState, feedbackState) {
  return {
    video_id: video.video_id,
    title: video.title,
    author: video.author,
    url: video.url,
    action,
    card_state: cardState,
    feedback_state: feedbackState,
  };
}

function buildUpdatedCard(cardState, feedbackState) {
  const safeCardState = sanitizeCardState(cardState);
  const elements = [];
  for (const [index, item] of (safeCardState.items || []).entries()) {
    const video = item.video || {};
    const selected = feedbackState[video.video_id];
    const likeText = selected === "like" ? "✅ 已选有用" : selected === "dislike" ? "👍 改为有用" : "👍 有用";
    const dislikeText = selected === "dislike" ? "✅ 已选不想看" : selected === "like" ? "👎 改为不想看" : "👎 不想看";

    elements.push({ tag: "hr" });
    elements.push({ tag: "markdown", content: `**#${index + 1} ${video.title || ""}**` });
    elements.push({
      tag: "note",
      elements: [{
        tag: "plain_text",
        content: `📺 ${video.author || ""} · ⏱ ${video.duration_str || ""} · 👀 ${formatViewCount(video.view_count)} views`,
      }],
    });
    if (video.reason) {
      elements.push({ tag: "markdown", content: `💡 ${video.reason}` });
    }
    const summary = sanitizeSummary(item.summary);
    if (summary) {
      elements.push({ tag: "markdown", content: summary });
    }
    if (selected) {
      elements.push({
        tag: "note",
        elements: [{
          tag: "plain_text",
          content: selected === "like" ? "✅ 已反馈：有用" : "✅ 已反馈：不想看",
        }],
      });
    }
    elements.push({
      tag: "action",
      actions: [
        {
          tag: "button",
          text: { tag: "plain_text", content: "▶ 观看视频" },
          type: "primary",
          url: video.url,
        },
        {
          tag: "button",
          text: { tag: "plain_text", content: likeText },
          type: selected === "like" ? "primary" : "secondary",
          name: `feedback_like_${video.video_id}`,
          value: buildFeedbackValue(video, "like", safeCardState, feedbackState),
        },
        {
          tag: "button",
          text: { tag: "plain_text", content: dislikeText },
          type: selected === "dislike" ? "primary" : "secondary",
          name: `feedback_dislike_${video.video_id}`,
          value: buildFeedbackValue(video, "dislike", safeCardState, feedbackState),
        },
      ],
    });
  }

  return {
    config: { wide_screen_mode: true },
    header: {
      title: { tag: "plain_text", content: `📹 YouTube 今日推荐 (${safeCardState.date || new Date().toISOString().slice(0, 10)})` },
      template: "blue",
    },
    elements,
  };
}

function extractFeedbackFromSearchParams(searchParams) {
  const value = Object.fromEntries(searchParams.entries());
  return feedbackDataFromValue(value);
}

async function recordFeedbackOnce(env, feedbackData) {
  const { sha, data } = await readGithubFile(env);
  const current = data[feedbackData.contentId] || {
    content_meta: feedbackData.contentMeta,
    video_meta: feedbackData.videoMeta,
    reactions: [],
  };

  current.content_meta = { ...current.content_meta, ...feedbackData.contentMeta };
  current.video_meta = { ...current.video_meta, ...feedbackData.videoMeta };
  current.reactions = current.reactions || [];
  current.reactions.push({
    event_id: feedbackEventId(),
    reaction: feedbackData.reaction,
    reason: feedbackData.reason,
    timestamp: new Date().toISOString(),
  });
  data[feedbackData.contentId] = current;

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

async function handleRequest(request, env, ctx) {
  if (request.method === "GET") {
    const feedbackData = extractFeedbackFromSearchParams(new URL(request.url).searchParams);
    if (feedbackData) {
      await recordFeedback(env, feedbackData);
      return htmlResponse(feedbackSuccessHtml(feedbackData));
    }
    return jsonResponse({ status: "ok" });
  }

  if (request.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  const payload = await request.json();
  if (env.FEISHU_VERIFICATION_TOKEN && payload.token && payload.token !== env.FEISHU_VERIFICATION_TOKEN) {
    return jsonResponse({ error: "Invalid Feishu verification token" }, 401);
  }
  if (payload.challenge) {
    return jsonResponse({ challenge: payload.challenge });
  }

  const feedbackData = extractFeedback(payload);
  if (!feedbackData) {
    return jsonResponse({});
  }

  const label = feedbackData.reaction === "like" ? "已记录：有用" : "已记录：不想看";
  const pendingRecord = recordFeedback(env, feedbackData).catch((error) => {
    console.error("Failed to record feedback", error);
  });
  if (ctx && typeof ctx.waitUntil === "function") {
    ctx.waitUntil(pendingRecord);
  }
  const responseBody = { toast: { type: "success", content: label } };
  if (feedbackData.cardState && Array.isArray(feedbackData.cardState.items)) {
    const nextFeedbackState = { ...feedbackData.feedbackState, [feedbackData.videoId]: feedbackData.reaction };
    responseBody.card = {
      type: "raw",
      data: buildUpdatedCard(feedbackData.cardState, nextFeedbackState),
    };
  }
  return jsonResponse(responseBody);
}

export default {
  async fetch(request, env, ctx) {
    try {
      return await handleRequest(request, env, ctx);
    } catch (error) {
      console.error(error);
      return jsonResponse({ error: error.message }, 500);
    }
  },
};
