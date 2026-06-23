"""
YouTube 订阅长视频摘要 → 飞书推送
- RSS 轮询订阅频道新视频（RSS 天然不含 Shorts）
- LLM 智能筛选最值得深度观看的视频
- 生成摘要，推送到飞书（开放平台应用）
"""

import os
import re
import json
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        value = default
    else:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


# ============ 配置 ============
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_USER_ID = os.environ.get("FEISHU_USER_ID", "")  # 目标用户 ID (ou_xxxxx)
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")  # 目标群 ID (oc_xxxxx)
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")  # 群自定义机器人 Webhook（仅兜底，无点击回调）
FEISHU_SEND_STATUS_CARD = env_bool("FEISHU_SEND_STATUS_CARD")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = (os.environ.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
MIN_DURATION_MINUTES = env_int("MIN_DURATION_MINUTES", 3, min_value=1)  # 过滤 Shorts（<=3min）
TOP_N = env_int("TOP_N", 3, min_value=1)  # 每日推送 Top N 视频
SUMMARY_MAX_TOKENS = env_int("SUMMARY_MAX_TOKENS", 700, min_value=100)
SUMMARY_MAX_CHARS = env_int("SUMMARY_MAX_CHARS", 700, min_value=100)
LOOKBACK_HOURS = env_int("LOOKBACK_HOURS", 24, min_value=1)
AIHOT_ENABLED = env_bool("AIHOT_ENABLED", True)
AIHOT_API_BASE = (os.environ.get("AIHOT_API_BASE") or "https://aihot.virxact.com").rstrip("/")
AIHOT_TAKE = env_int("AIHOT_TAKE", 3, min_value=0, max_value=20)
AIHOT_CANDIDATE_TAKE = env_int("AIHOT_CANDIDATE_TAKE", max(30, AIHOT_TAKE * 6), min_value=1, max_value=100)
AIHOT_MIN_SCORE = env_int("AIHOT_MIN_SCORE", 0, min_value=0, max_value=100)
AIHOT_USER_AGENT = os.environ.get("AIHOT_USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 aihot-skill/0.2.0"
)
CHANNELS_FILE = os.environ.get("CHANNELS_FILE", "channels.json")
PROFILE_FILE = os.environ.get("PROFILE_FILE", "profile.json")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "history.json")
HISTORY_MAX_DAYS = env_int("HISTORY_MAX_DAYS", 30, min_value=1)
YOUTUBE_UPLOADS_PAGE_SIZE = env_int("YOUTUBE_UPLOADS_PAGE_SIZE", 5, min_value=1, max_value=50)
RSS_RETRY_ATTEMPTS = env_int("RSS_RETRY_ATTEMPTS", 2, min_value=1)
RSS_RETRY_DELAY_SECONDS = float(os.environ.get("RSS_RETRY_DELAY_SECONDS", "1"))
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; youtube-digest/1.0; +https://github.com/Suda202/youtube-digest)",
    "Accept": "application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
}
SUMMARY_PROMPT_LEAK_FALLBACK = "⚠️ 摘要生成异常，已隐藏提示词内容。请直接打开视频判断。"
SUMMARY_PROMPT_LEAK_MARKERS = [
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
]
LLM_NON_TEXT_BLOCK_TYPES = {"thinking", "redacted_thinking"}


def digest_date_label() -> str:
    return datetime.now(LOCAL_TIMEZONE).strftime("%Y-%m-%d")


def is_recent(published_str: str) -> bool:
    published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    return published >= cutoff


def chunked(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_channels() -> list[dict]:
    """加载频道列表"""
    path = Path(CHANNELS_FILE)
    if not path.exists():
        print(f"❌ {CHANNELS_FILE} not found")
        return []
    with open(path) as f:
        return json.load(f)


def load_profile() -> dict:
    """加载用户画像配置"""
    path = Path(PROFILE_FILE)
    if not path.exists():
        print(f"⚠️ {PROFILE_FILE} not found, using defaults")
        return {
            "description": "科技行业从业者",
            "favorite_content": "深度访谈、技术分享",
            "preferred_channels": [],
            "exclude_title_patterns": ["full course", "tutorial for beginners"],
        }
    with open(path) as f:
        return json.load(f)


def get_digest_top_n(profile: dict) -> int:
    """TOP_N env has priority; otherwise profile can lower the daily volume."""
    raw_value = os.environ.get("TOP_N", profile.get("max_daily_videos", TOP_N))
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return TOP_N


def load_history() -> dict:
    """加载已处理视频 ID → 时间戳映射，避免重复推送"""
    path = Path(HISTORY_FILE)
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    # 兼容旧格式（纯列表）
    if isinstance(data, list):
        now = datetime.now(timezone.utc).isoformat()
        return {vid: now for vid in data}
    return data


def save_history(history: dict):
    """保存历史记录，自动清理超过 HISTORY_MAX_DAYS 的条目"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_MAX_DAYS)).isoformat()
    cleaned = {vid: ts for vid, ts in history.items() if ts > cutoff}
    if len(cleaned) < len(history):
        print(f"  🧹 清理历史记录: {len(history)} → {len(cleaned)} 条")
    path = Path(HISTORY_FILE)
    with open(path, "w") as f:
        json.dump(cleaned, f)


# ============ AI HOT ============
AIHOT_INTEREST_KEYWORDS = [
    ("Loop Engineering", 32, [
        "loop engineering",
        "agent loop",
        "循环工程",
    ]),
    ("Agent", 24, [
        "agentic",
        "deep agents",
        "coding agent",
        "code agent",
        "software agent",
        "ai agent workflow",
        "multi-agent orchestration",
        "多智能体编排",
        "编程智能体",
        "编码智能体",
        "代码智能体",
        "软件工程智能体",
        "智能体",
    ]),
    ("GEO", 30, [
        "geo",
        "generative engine optimization",
        "llmo",
        "aeo",
        "生成式引擎优化",
        "生成式搜索优化",
        "答案引擎优化",
    ]),
    ("AI搜索", 18, [
        "ai search",
        "ai 搜索",
        "chatgpt search",
        "google ai overviews",
        "ai overviews",
        "perplexity",
        "搜索可见性",
        "品牌可见性",
    ]),
    ("Agentic Engineering", 24, [
        "agentic engineering",
        "agentic software engineering",
        "ai engineering",
        "engineering agent",
        "coding agent",
        "code agent",
        "software agent",
        "ai coding",
        "agentic coding",
        "devin",
        "cursor",
        "claude code",
        "codex",
        "harness",
        "研发智能体",
        "编程智能体",
        "软件工程智能体",
        "工程智能体",
        "代码智能体",
    ]),
    ("Vibe Coding", 20, [
        "vibe coding",
        "loop coding",
        "engineering coding",
        "氛围编程",
        " vibe 编程",
        "循环编程",
    ]),
    ("海外增长", 14, [
        "overseas",
        "global",
        "international",
        "go-to-market",
        "gtm",
        "出海",
        "海外",
        "海外市场",
        "海外客户",
    ]),
    ("广告营销", 10, [
        "广告",
        "创意",
        "marketing",
        "ads",
        "ad creative",
        "campaign",
        "品牌",
        "内容分发",
        "seo",
    ]),
    ("AI产品", 8, [
        "ai 产品",
        "产品",
        "product",
        "工作流",
        "workflow",
        "saas",
    ]),
]

AIHOT_DOWNRANK_KEYWORDS = [
    "股价",
    "股票",
    "估值",
    "融资",
    "投资",
    "venture capital",
    "vc",
    "ipo",
    "基金",
    "从零开始",
    "教程",
    "代码实现",
    "api 参数",
    "模型架构",
    "向量数据库",
    "rag 调参",
]

AIHOT_HARD_REJECT_KEYWORDS = [
    "父亲节",
    "母亲节",
    "节日活动",
    "上传照片",
    "生成合影",
    "抽奖",
    "促销活动",
    "双向转售",
    "转售模型",
    "ai 中间商",
]

AIHOT_AGENT_KEYWORDS = [
    "agentic", "deep agents", "coding agent", "code agent",
    "software agent", "ai agent workflow", "multi-agent orchestration",
    "harness", "loop engineering", "claude code", "codex", "cursor", "devin",
    "研发智能体", "编程智能体", "编码智能体", "代码智能体", "软件工程智能体", "多智能体编排",
]

AIHOT_BUSINESS_KEYWORDS = [
    "geo", "generative engine optimization", "go-to-market", "gtm",
    "海外增长", "出海", "ai 搜索", "product strategy", "产品策略",
    "商业化", "pricing", "定价", "营销", "广告",
]

AIHOT_FRONTIER_KEYWORDS = [
    "silicon valley", "硅谷", "frontier", "前沿", "趋势",
    "openai", "anthropic", "deepmind", "a16z", "sequoia",
    "new model", "新模型", "research breakthrough", "研究突破",
]

AIHOT_LOW_VALUE_VERTICAL_KEYWORDS = [
    "高考", "志愿填报", "高考志愿", "红包", "朋友圈",
    "语音克隆", "tts", "音频生成", "ocr",
    "自动驾驶", "纽北", "车牌", "前女友",
    "网络威胁", "勒索软件", "钓鱼诈骗", "安全威胁",
]

AIHOT_GENERIC_MODEL_RELEASE_KEYWORDS = [
    "开源模型", "模型发布", "正式发布", "新模型", "基座模型",
    "参数", "榜单", "sota", "盲评", "胜率",
]


def utc_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_aihot_items(
    hours: int | None = None,
    take: int | None = None,
    profile: dict | None = None,
    ranking_hints: str = "",
) -> list[dict]:
    """Fetch recent AI HOT selected items. Failure should not block the YouTube digest."""
    if not AIHOT_ENABLED:
        return []

    item_limit = AIHOT_TAKE if take is None else take
    item_limit = max(0, min(100, item_limit))
    if item_limit == 0:
        return []

    window_hours = max(1, hours or LOOKBACK_HOURS)
    since = utc_iso_z(datetime.now(timezone.utc) - timedelta(hours=window_hours))
    request_limit = item_limit
    if profile:
        request_limit = max(item_limit, AIHOT_CANDIDATE_TAKE)
    try:
        resp = requests.get(
            f"{AIHOT_API_BASE}/api/public/items",
            headers={"User-Agent": AIHOT_USER_AGENT},
            params={"mode": "selected", "since": since, "take": request_limit},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️ AI HOT 拉取失败: {e}")
        return []

    items = []
    for raw_item in data.get("items", []):
        if not isinstance(raw_item, dict):
            continue

        title = (raw_item.get("title") or "").strip()
        url = (raw_item.get("url") or "").strip()
        if not title or not url:
            continue

        score = raw_item.get("score")
        if AIHOT_MIN_SCORE and isinstance(score, (int, float)) and score < AIHOT_MIN_SCORE:
            continue

        items.append({
            "id": raw_item.get("id", ""),
            "title": title,
            "url": url,
            "source": (raw_item.get("source") or "AI HOT").strip(),
            "publishedAt": raw_item.get("publishedAt") or "",
            "summary": (raw_item.get("summary") or "").strip(),
            "category": raw_item.get("category") or "",
            "score": score,
            "selected": raw_item.get("selected", True),
        })
        if not profile and len(items) >= item_limit:
            break

    if profile:
        return select_aihot_items_for_profile(
            items,
            profile,
            ranking_hints=ranking_hints,
            take=item_limit,
        )
    return items[:item_limit]


def keyword_matches(text: str, keyword: str) -> bool:
    keyword_lower = keyword.lower().strip()
    if not keyword_lower:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 ._+/-]*", keyword_lower):
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword_lower)}(?![a-z0-9])", text) is not None
    return keyword_lower in text


def aihot_item_text(item: dict) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "summary", "source", "category")
    ).lower()


def is_agent_focused_aihot_item(item: dict) -> bool:
    text = aihot_item_text(item)
    return any(keyword_matches(text, keyword) for keyword in AIHOT_AGENT_KEYWORDS)


def is_business_focused_aihot_item(item: dict) -> bool:
    text = aihot_item_text(item)
    return any(keyword_matches(text, keyword) for keyword in AIHOT_BUSINESS_KEYWORDS)


def score_aihot_item_for_profile(
    item: dict,
    profile: dict | None = None,
    ranking_hints: str = "",
) -> tuple[float, list[str]]:
    text = aihot_item_text(item)
    score = float(item.get("score") or 0)
    match_tags = []

    category = item.get("category")
    if category == "ai-products":
        score += 4
    elif category == "industry":
        score += 2
    elif category == "paper":
        score -= 3

    for tag, weight, keywords in AIHOT_INTEREST_KEYWORDS:
        if any(keyword_matches(text, keyword) for keyword in keywords):
            score += weight
            match_tags.append(tag)

    profile = profile or {}
    for keyword in profile.get("aihot_boost_keywords", []):
        if keyword_matches(text, str(keyword)):
            score += 8
            match_tags.append(str(keyword))

    downrank_keywords = AIHOT_DOWNRANK_KEYWORDS + [
        str(keyword) for keyword in profile.get("deprioritize_topics", [])
    ]
    agent_focused = is_agent_focused_aihot_item(item)
    for keyword in downrank_keywords:
        if agent_focused and keyword in {"从零开始", "教程", "代码实现", "api 参数"}:
            continue
        if keyword_matches(text, keyword):
            score -= 16

    for line in ranking_hints.splitlines():
        label_text = line.split("：", 1)[-1] if "：" in line else ""
        for label in (part.strip() for part in label_text.split("、")):
            if not label or not keyword_matches(text, label):
                continue
            if "回避" in line:
                score -= 12
            elif "偏好" in line:
                score += 8
                match_tags.append(label)

    return score, list(dict.fromkeys(match_tags))


def rank_aihot_items_for_profile(
    items: list[dict],
    profile: dict | None = None,
    ranking_hints: str = "",
) -> list[dict]:
    ranked = []
    for item in items:
        preference_score, match_tags = score_aihot_item_for_profile(item, profile, ranking_hints)
        ranked_item = {**item, "preference_score": preference_score, "match_tags": match_tags}
        ranked.append(ranked_item)

    return sorted(
        ranked,
        key=lambda item: (item.get("preference_score", 0), item.get("publishedAt") or ""),
        reverse=True,
    )


def aihot_item_lane(item: dict) -> str | None:
    text = aihot_item_text(item)
    if any(keyword_matches(text, keyword) for keyword in AIHOT_AGENT_KEYWORDS):
        return "agent"
    if any(keyword_matches(text, keyword) for keyword in AIHOT_FRONTIER_KEYWORDS):
        return "frontier"
    if any(keyword_matches(text, keyword) for keyword in AIHOT_BUSINESS_KEYWORDS):
        return "business"
    score = float(item.get("score") or 0)
    if score >= 85 and any(keyword_matches(text, keyword) for keyword in ("ai", "llm", "大模型")):
        return "exploration"
    return None


def passes_aihot_quality_gate(item: dict, profile: dict | None = None) -> bool:
    text = aihot_item_text(item)
    if any(keyword_matches(text, keyword) for keyword in AIHOT_HARD_REJECT_KEYWORDS):
        return False

    agent_focused = is_agent_focused_aihot_item(item)
    business_focused = is_business_focused_aihot_item(item)
    if any(keyword_matches(text, keyword) for keyword in AIHOT_LOW_VALUE_VERTICAL_KEYWORDS):
        return False
    if not agent_focused and not business_focused:
        if any(keyword_matches(text, keyword) for keyword in AIHOT_GENERIC_MODEL_RELEASE_KEYWORDS):
            return False

    generic_tutorial = any(keyword_matches(text, keyword) for keyword in (
        "从零开始", "getting started", "beginner tutorial", "安装教程",
        "api 参数", "向量数据库教程", "rag 调参",
    ))
    if generic_tutorial and not agent_focused:
        return False

    profile = profile or {}
    if not agent_focused:
        for keyword in profile.get("deprioritize_topics", []):
            if keyword_matches(text, str(keyword)):
                return False
    if any(keyword_matches(text, keyword) for keyword in ("股票", "股价", "估值", "投资机会")):
        return False
    return aihot_item_lane(item) is not None


def parse_aihot_selection_response(raw: str | None, candidate_ids: set[str]) -> list[str] | None:
    if not raw:
        return None
    text = str(raw).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("selected_ids"), list):
        return None
    return [
        str(item_id) for item_id in payload["selected_ids"]
        if str(item_id) in candidate_ids
    ]


def select_aihot_items_for_profile(
    items: list[dict],
    profile: dict | None = None,
    *,
    ranking_hints: str = "",
    take: int | None = None,
) -> list[dict]:
    """Select only genuinely useful AI HOT items; returning zero is valid."""
    item_limit = AIHOT_TAKE if take is None else max(0, take)
    ranked = rank_aihot_items_for_profile(items, profile, ranking_hints)
    candidates = [item for item in ranked if passes_aihot_quality_gate(item, profile)]

    exploration_count = 0
    deterministic = []
    for item in candidates:
        lane = aihot_item_lane(item)
        if lane == "exploration":
            if exploration_count >= 2:
                continue
            exploration_count += 1
        deterministic.append({**item, "selection_lane": lane})
        if len(deterministic) >= item_limit:
            break

    if not DEEPSEEK_API_KEY or not deterministic:
        return deterministic

    prompt_items = [{
        key: item.get(key)
        for key in ("id", "title", "summary", "source", "score", "match_tags", "selection_lane")
    } for item in deterministic]
    prompt = f"""从 AI HOT 候选中选出真正值得给该用户看的内容。宁缺毋滥，可以返回空数组。
优先：Agent/Agentic Engineering/Loop Engineering、硅谷正在流行的前沿趋势、有实际产品或商业价值的深度内容。
保留 Agent 实战教程；排除普通 API/安装教程、节日营销、软广、转售拼接新闻和低信息量内容。
用户画像：{json.dumps(profile or {}, ensure_ascii=False)}
动态偏好：{ranking_hints}
候选：{json.dumps(prompt_items, ensure_ascii=False)}
只返回 JSON：{{"selected_ids":["id"]}}
"""
    selected_ids = parse_aihot_selection_response(
        call_llm(prompt),
        {str(item.get("id") or "") for item in deterministic},
    )
    if selected_ids is None:
        return deterministic
    by_id = {str(item.get("id") or ""): item for item in deterministic}
    return [by_id[item_id] for item_id in selected_ids][:item_limit]


def format_aihot_summary(summary: str) -> str:
    """Add paragraph breaks to AI HOT's single-paragraph Chinese summaries."""
    text = (summary or "").strip()
    return re.sub(r"([。！？][”’」』）》】]?)\s*(?=\S)", r"\1\n\n", text)


def aihot_content_id(item: dict) -> str:
    raw_id = str(item.get("id") or "").strip()
    if not raw_id:
        raw_id = hashlib.sha256(str(item.get("url") or "").encode("utf-8")).hexdigest()[:16]
    return f"aihot:{raw_id}"


def build_aihot_card_elements(aihot_items: list[dict], enable_feedback: bool = False) -> list[dict]:
    if not aihot_items:
        return []

    elements = []
    for i, item in enumerate(aihot_items, 1):
        if i > 1:
            elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"**{i}. {item['title']}**"})
        summary = format_aihot_summary(item.get("summary") or "")
        if summary:
            elements.append({"tag": "markdown", "content": summary})
        actions = [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看原文"},
            "type": "default",
            "url": item["url"],
        }]
        if enable_feedback:
            content_id = aihot_content_id(item)
            feedback_meta = {
                "content_id": content_id,
                "content_type": "aihot",
                "title": item["title"],
                "creator": item.get("source") or "AI HOT",
                "url": item["url"],
                "category": item.get("category") or "",
                "selection_tags": (item.get("match_tags") or [])[:5],
            }
            action_suffix = hashlib.sha256(content_id.encode("utf-8")).hexdigest()[:12]
            actions.extend([
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👍 有用"},
                    "type": "primary",
                    "name": f"feedback_aihot_like_{action_suffix}",
                    "value": {**feedback_meta, "action": "like"},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👎 不想看"},
                    "type": "secondary",
                    "name": f"feedback_aihot_dislike_{action_suffix}",
                    "value": {**feedback_meta, "action": "dislike"},
                },
            ])
        elements.append({"tag": "action", "actions": actions})
    return elements


# ============ YouTube RSS ============
def uploads_playlist_id_from_channel_id(channel_id: str) -> str:
    """YouTube uploads playlist id is usually UU + channel id without the UC prefix."""
    if channel_id.startswith("UC") and len(channel_id) > 2:
        return f"UU{channel_id[2:]}"
    return ""


def rss_urls_for_channel(channel_id: str) -> list[tuple[str, str]]:
    urls = [("channel", f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")]
    uploads_playlist_id = uploads_playlist_id_from_channel_id(channel_id)
    if uploads_playlist_id:
        urls.append(("uploads_playlist", f"https://www.youtube.com/feeds/videos.xml?playlist_id={uploads_playlist_id}"))
    return urls


def parse_rss_videos(feed_text: str) -> list[dict]:
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    root = ET.fromstring(feed_text)
    author = root.find("atom:title", ns).text
    videos = []

    for entry in root.findall("atom:entry", ns):
        published_str = entry.find("atom:published", ns).text
        if not is_recent(published_str):
            continue

        video_id = entry.find("yt:videoId", ns).text
        title = entry.find("atom:title", ns).text

        videos.append({
            "video_id": video_id,
            "title": title,
            "author": author,
            "published": published_str,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    return videos


def fetch_rss_videos(channel_id: str) -> tuple[list[dict], bool]:
    """从 YouTube RSS 获取频道最新视频，返回 (videos, rss_ok)。"""
    max_attempts = max(1, RSS_RETRY_ATTEMPTS)
    last_error = ""

    for source, url in rss_urls_for_channel(channel_id):
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.get(url, headers=RSS_HEADERS, timeout=15)
                resp.raise_for_status()
                return parse_rss_videos(resp.text), True
            except ET.ParseError as e:
                last_error = f"{source} parse error: {e}"
            except Exception as e:
                last_error = f"{source} fetch error: {e}"

            if attempt < max_attempts:
                time.sleep(RSS_RETRY_DELAY_SECONDS)

        if source == "channel":
            print(f"  ⚠️ RSS channel feed failed for {channel_id}, trying uploads playlist RSS")

    print(f"  ⚠️ RSS fetch failed for {channel_id}: {last_error}")
    return [], False


def fetch_channel_upload_playlists(channel_ids: list[str]) -> dict[str, str]:
    """通过 YouTube Data API 获取频道 uploads playlist，作为 RSS 失败兜底。"""
    if not YOUTUBE_API_KEY:
        return {}

    playlists = {}
    url = "https://www.googleapis.com/youtube/v3/channels"
    for batch in chunked(channel_ids, 50):
        try:
            resp = requests.get(
                url,
                params={
                    "part": "contentDetails",
                    "id": ",".join(batch),
                    "key": YOUTUBE_API_KEY,
                },
                timeout=15,
            )
            data = resp.json()
            if resp.status_code != 200 or data.get("error"):
                message = data.get("error", {}).get("message", data)
                print(f"  ⚠️ YouTube API 频道查询失败: {message}")
                continue

            for item in data.get("items", []):
                uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                if uploads:
                    playlists[item["id"]] = uploads
        except Exception as e:
            print(f"  ⚠️ YouTube API 频道查询异常: {e}")

    missing_count = len(set(channel_ids) - set(playlists))
    if missing_count:
        print(f"  ⚠️ {missing_count} 个频道未找到 uploads playlist")
    return playlists


def fetch_upload_playlist_videos(channel_id: str, playlist_id: str) -> list[dict]:
    """从 uploads playlist 获取最近视频，只作为 RSS 请求失败时的稳定兜底。"""
    if not YOUTUBE_API_KEY:
        return []

    max_results = max(1, min(50, YOUTUBE_UPLOADS_PAGE_SIZE))
    url = "https://www.googleapis.com/youtube/v3/playlistItems"
    try:
        resp = requests.get(
            url,
            params={
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": max_results,
                "key": YOUTUBE_API_KEY,
            },
            timeout=15,
        )
        data = resp.json()
        if resp.status_code != 200 or data.get("error"):
            message = data.get("error", {}).get("message", data)
            print(f"  ⚠️ YouTube API uploads fetch failed for {channel_id}: {message}")
            return []
    except Exception as e:
        print(f"  ⚠️ YouTube API uploads fetch failed for {channel_id}: {e}")
        return []

    videos = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        published_str = content.get("videoPublishedAt") or snippet.get("publishedAt")
        if not published_str or not is_recent(published_str):
            continue

        video_id = content.get("videoId") or snippet.get("resourceId", {}).get("videoId")
        title = snippet.get("title") or ""
        if not video_id or title in {"Private video", "Deleted video"}:
            continue

        author = snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle") or channel_id
        videos.append({
            "video_id": video_id,
            "title": title,
            "author": author,
            "published": published_str,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    return videos


# ============ YouTube Data API (视频时长) ============
def parse_duration(iso_duration: str) -> int:
    """ISO 8601 duration → 秒数，例如 PT1H23M45S"""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


def get_video_details(video_id: str) -> dict:
    """通过 YouTube Data API 获取视频时长、描述、播放量"""
    if not YOUTUBE_API_KEY:
        print("  ⚠️ No YOUTUBE_API_KEY, skipping details fetch")
        return {"duration": 9999, "description": "", "view_count": 0}
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "contentDetails,snippet,statistics", "id": video_id, "key": YOUTUBE_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        item = data["items"][0]
        duration_str = item["contentDetails"]["duration"]
        description = item["snippet"].get("description", "")
        view_count = int(item["statistics"].get("viewCount", 0))
        return {
            "duration": parse_duration(duration_str),
            "description": description,
            "view_count": view_count,
        }
    except Exception as e:
        print(f"  ⚠️ Details fetch failed: {e}")
        return {"duration": 0, "description": "", "view_count": 0}


def format_duration(seconds: int) -> str:
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


_yt_cookies_file = os.environ.get("YT_COOKIES_FILE", "")


def get_transcript(video_id: str) -> str | None:
    """通过 yt-dlp 获取视频字幕文本（优先手动字幕，其次自动生成）"""
    try:
        import yt_dlp
        ydl_opts = {
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'quiet': True,
            'no_warnings': True,
            'ignore_no_formats_error': True,
            'remote_components': {'ejs': 'github'},
        }
        # 支持 cookies：环境变量指定文件路径，或本地自动读 Chrome
        if _yt_cookies_file and os.path.exists(_yt_cookies_file):
            ydl_opts['cookiefile'] = _yt_cookies_file
        else:
            ydl_opts['cookiesfrombrowser'] = ('chrome',)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}', download=False
            )
            subs = info.get('subtitles', {})
            auto_subs = info.get('automatic_captions', {})
            en_subs = subs.get('en') or auto_subs.get('en')
            if not en_subs:
                return None
            for fmt in en_subs:
                if fmt.get('ext') == 'json3':
                    resp = requests.get(fmt['url'], timeout=15)
                    data = resp.json()
                    texts = []
                    for e in data.get('events', []):
                        for s in e.get('segs', []):
                            t = s.get('utf8', '').strip()
                            if t and t != '\n':
                                texts.append(t)
                    text = ' '.join(texts)
                    if len(text) > 80000:
                        text = text[:80000] + ' ...[truncated]'
                    return text if len(text) > 100 else None
        return None
    except Exception as e:
        print(f"      ⚠️ 字幕获取失败: {e}")
        return None


# ============ 摘要 LLM ============
def summarize_with_llm(title: str, author: str, content: str, content_type: str = "字幕") -> dict:
    """基于字幕或描述生成结构化摘要"""
    if not DEEPSEEK_API_KEY:
        return {"summary": "⚠️ 未配置 DEEPSEEK_API_KEY，跳过摘要"}

    if len(content) > 80000:
        content = content[:80000] + "\n...[truncated]"

    prompt = f"""根据以下视频{content_type}，生成一份便于快速判断是否值得观看的中文短摘要。

视频标题：{title}
频道：{author}

视频{content_type}：
{content}

格式要求（纯文本，不要 markdown）：
- 第一行用"结论："开头，用一句话说明这条视频最值得看的观点
- 用（1）（2）（3）编号列出最多 3 个要点，每条不超过 45 个中文字符
- 优先提炼产品策略、用户洞察、商业化、AI 应用趋势、创意/广告智能体相关内容
- 不展开融资、估值、股票、基金、代码实现、模型架构、API 参数等细节；如果无法避开，只用一句话带过
- 最后一行用"适合："开头，说明适合什么场景下观看
- 全文控制在 350 个中文字符以内，不要出现"一句话总结"、"关键要点"、"总结"等格式标签"""

    result = call_llm(prompt, max_tokens=SUMMARY_MAX_TOKENS)
    if result:
        summary = sanitize_summary_text(result)
        if summary == SUMMARY_PROMPT_LEAK_FALLBACK:
            print("  ⚠️ LLM 摘要疑似泄露提示词，已隐藏")
        return {"summary": summary}
    return {"summary": "摘要生成失败"}


def llm_chat_completions_url() -> str:
    if DEEPSEEK_API_BASE.endswith("/chat/completions"):
        return DEEPSEEK_API_BASE
    return f"{DEEPSEEK_API_BASE}/chat/completions"


def call_llm(prompt: str, max_tokens: int = 1024) -> str | None:
    """调用 OpenAI 兼容摘要 LLM，返回文本结果"""
    if not DEEPSEEK_API_KEY:
        return None
    try:
        resp = requests.post(
            llm_chat_completions_url(),
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        data = resp.json()
        if data.get("error"):
            error = data.get("error", {})
            print(f"  ⚠️ LLM error: {error.get('message', str(error))}")
            return None
        status_code = getattr(resp, "status_code", 200)
        if isinstance(status_code, int) and status_code >= 400:
            print(f"  ⚠️ LLM HTTP error: {status_code}")
            return None

        choices = data.get("choices", [])
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message", {})
                if isinstance(message, dict):
                    text = extract_llm_content_text(message.get("content"))
                    if text:
                        return text
                text = choice.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()

        content = data.get("content", [])
        text = extract_llm_content_text(content)
        if text:
            return text

        for key in ("text", "completion"):
            text = data.get(key)
            if isinstance(text, str) and text.strip():
                return text
        return None
    except Exception as e:
        print(f"  ⚠️ LLM call failed: {e}")
        return None


def extract_llm_content_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for block in reversed(content):
            text = extract_llm_text_block(block)
            if text:
                return text
    return ""


def extract_llm_text_block(block) -> str:
    """Return model-visible text only; never stringify reasoning/tool blocks."""
    if isinstance(block, str):
        return block.strip()
    if not isinstance(block, dict):
        return ""

    block_type = block.get("type")
    if isinstance(block_type, str) and block_type in LLM_NON_TEXT_BLOCK_TYPES:
        return ""

    text = block.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def rank_candidates(candidates: list[dict], top_n: int, profile: dict) -> list[dict]:
    """用 LLM 从候选视频中挑选最值得深度观看的 Top N，返回 [{index, reason}]"""
    video_list = []
    for i, v in enumerate(candidates):
        desc_snippet = (v.get("description") or "")[:300].replace("\n", " ").strip()
        if desc_snippet:
            desc_snippet = f"\n   描述: {desc_snippet}"
        video_list.append(
            f"{i+1}. [{v['author']}] {v['title']} ({v['duration_str']}, {format_view_count(v['view_count'])} views){desc_snippet}"
        )

    preferred = ", ".join(profile.get("preferred_channels", []))
    deprioritize = profile.get("deprioritize_topics", [])
    deprioritize_channels = profile.get("deprioritize_channels", [])
    deprioritize_section = ""
    if deprioritize:
        topics_str = "、".join(deprioritize)
        deprioritize_section = f"""
降低优先级（除非内容特别有深度，否则尽量不选）：
- 涉及以下话题的内容：{topics_str}
"""
    if deprioritize_channels:
        channels_str = "、".join(deprioritize_channels)
        deprioritize_section += f"""- 来自以下偏投资/偏技术频道的内容：{channels_str}
"""

    channel_notes = profile.get("channel_notes", {})
    channel_notes_section = ""
    if channel_notes:
        lines = "\n".join(f"- {ch}：{note}" for ch, note in channel_notes.items())
        channel_notes_section = f"\n特定频道偏好：\n{lines}\n"

    ranking_hints = ""
    hints_file = Path("ranking_hints.txt")
    if hints_file.exists():
        ranking_hints = hints_file.read_text().strip()
        if ranking_hints:
            ranking_hints = f"\n\n动态偏好（基于近期反馈）：\n{ranking_hints}\n"

    prompt = f"""你是一个视频筛选助手。请严格按照以下标准筛选。

用户画像：
- {profile.get("description", "科技行业从业者")}
- 常看频道：{preferred}
- 最喜欢的内容类型：{profile.get("favorite_content", "深度访谈、技术分享")}

以下是今天的 {len(candidates)} 个候选视频：

{chr(10).join(video_list)}

请从中选出最多 {top_n} 个最值得深度观看的视频。宁缺毋滥：如果达不到标准，可以少选。

必须优先选择：
1. AI 产品设计、用户体验设计、产品增长与商业化案例、产品策略与竞品分析（优先级最高）
2. 广告创意智能体、AI 工作流、面向海外客户的 SaaS/增长案例
3. 技术团队管理实践、工程师文化，但必须是管理和协作视角
4. 有深度的一对一访谈或圆桌讨论（创始人、研究者、产品负责人一手观点）
5. 行业大会中面向产品、应用、战略的主题演讲
6. 来自用户常看频道的高质量内容

必须排除（即使播放量高也不选）：
- 纯新闻汇总/速报类（"AI News", "XX is HERE", "XX is INSANE" 等标题党）
- 入门教程/全课程（"Full Course", "Tutorial For Beginners", "从零开始"）
- 纯投资、融资、估值、股票、基金、宏观市场、VC 观点输出
- 纯技术细节：论文精读、代码实现、模型架构、框架/API 教程、RAG/向量库调参
- 与 AI/科技行业无关的内容（情感、健身、烹饪等）
- 播放量极低（<200）且频道不在用户常看列表中的视频
- 低信息密度内容：纯开场致辞（welcome, opening）、纯 announcements、纯回顾/ recap、无实质观点的访谈预热
{deprioritize_section}
播放量参考规则：同类深度内容中播放量明显更高的优先，但绝不因为播放量高就选新闻速报。
{channel_notes_section}
{ranking_hints}

请按推荐度从高到低输出，每行一个，格式为：
编号|一句话推荐理由

例如：
3|一线产品负责人复盘 AI 功能商业化，和你的产品方向直接相关
7|创始人分享广告创意工作流变化，适合提炼智能体机会
1|Claude Code 团队讨论开发者工作流，但重点在产品体验而非代码细节

最多输出 {top_n} 行，不要其他文字。"""

    result = call_llm(prompt, max_tokens=500)
    if not result:
        print("  ⚠️ DeepSeek 排序失败，回退到播放量排序")
        candidates.sort(key=lambda v: v["view_count"], reverse=True)
        return [{"index": i, "reason": ""} for i in range(min(top_n, len(candidates)))]

    # 解析 LLM 返回的 "编号|理由" 格式
    results = []
    for line in result.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        nums = re.findall(r'\d+', parts[0])
        if not nums:
            continue
        idx = int(nums[0]) - 1
        reason = parts[1].strip() if len(parts) > 1 else ""
        if 0 <= idx < len(candidates) and idx not in [r["index"] for r in results]:
            results.append({"index": idx, "reason": reason})
        if len(results) >= top_n:
            break

    if not results:
        print("  ⚠️ LLM 返回解析失败，回退到播放量排序")
        candidates.sort(key=lambda v: v["view_count"], reverse=True)
        return [{"index": i, "reason": ""} for i in range(min(top_n, len(candidates)))]

    return results


# ============ 飞书推送 ============
def get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return ""

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            return data["tenant_access_token"]
        else:
            print(f"  ⚠️ 获取 token 失败: {data}")
            return ""
    except Exception as e:
        print(f"  ⚠️ 获取 token 异常: {e}")
        return ""


def format_view_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def looks_like_summary_prompt_leak(summary: str) -> bool:
    """Detect cases where the LLM echoes the prompt/input instead of the summary."""
    text = (summary or "").strip()
    if not text:
        return False

    normalized = text.lower()
    marker_count = sum(1 for marker in SUMMARY_PROMPT_LEAK_MARKERS if marker.lower() in normalized)
    return marker_count >= 2 or ("格式要求" in text and "视频标题" in text)


def looks_like_internal_reasoning_leak(summary: str) -> bool:
    """Detect thinking/reasoning blocks that should never appear in cards."""
    text = (summary or "").strip()
    if not text:
        return False

    head = text[:1000]
    structured_reasoning = re.match(r"^\s*(?:```(?:json|python)?\s*)?[\{\[]", head)
    if structured_reasoning and re.search(r"['\"](?:thinking|signature)['\"]\s*:", head):
        return True

    reasoning_phrases = [
        "The user asks me",
        "The task is to",
        "We need answer",
        "I need craft",
    ]
    return "结论：" not in head and any(phrase in head for phrase in reasoning_phrases)


def sanitize_summary_text(summary: str) -> str:
    text = (summary or "").strip()
    if not text:
        return ""
    if looks_like_summary_prompt_leak(text) or looks_like_internal_reasoning_leak(text):
        return SUMMARY_PROMPT_LEAK_FALLBACK
    return text


def trim_summary(summary: str) -> str:
    """Keep Feishu cards readable even when the LLM ignores length guidance."""
    text = sanitize_summary_text(summary)
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    trimmed = text[:SUMMARY_MAX_CHARS].rsplit("\n", 1)[0].strip()
    return f"{trimmed}\n...（摘要已截断）"


def build_feedback_card_state(videos_with_summaries: list[dict], card_date: str) -> dict:
    """Embed enough card state for Feishu callback responses to redraw the card."""
    items = []
    for item in videos_with_summaries:
        v = item["video"]
        items.append({
            "video": {
                "video_id": v["video_id"],
                "title": v["title"],
                "author": v["author"],
                "url": v["url"],
                "duration_str": v["duration_str"],
                "view_count": v["view_count"],
                "reason": v.get("reason", ""),
            },
            "summary": trim_summary(item["summary"]),
        })
    return {"date": card_date, "items": items}


def build_card_content(
    videos_with_summaries: list[dict],
    aihot_items: list[dict] | None = None,
    enable_feedback: bool = False,
) -> dict:
    """构建飞书卡片消息内容，返回卡片 JSON 结构"""
    today = digest_date_label()
    aihot_items = aihot_items or []
    elements = []
    card_state = build_feedback_card_state(videos_with_summaries, today) if enable_feedback and videos_with_summaries else None
    feedback_state = {}

    for i, item in enumerate(videos_with_summaries, 1):
        v = item["video"]
        summary = trim_summary(item["summary"])
        view_str = format_view_count(v["view_count"])

        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"**#{i} {v['title']}**"})
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": f"📺 {v['author']} · ⏱ {v['duration_str']} · 👀 {view_str} views"}
        ]})
        reason = v.get("reason", "")
        if reason:
            elements.append({"tag": "markdown", "content": f"💡 {reason}"})
        elements.append({"tag": "markdown", "content": summary})
        actions = [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "▶ 观看视频"},
            "type": "primary",
            "url": v["url"],
        }]
        if enable_feedback:
            feedback_meta = {
                "video_id": v["video_id"],
                "title": v["title"],
                "author": v["author"],
                "url": v["url"],
            }
            actions.extend([
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👍 有用"},
                    "type": "primary",
                    "name": f"feedback_like_{v['video_id']}",
                    "value": {
                        **feedback_meta,
                        "action": "like",
                        "card_state": card_state,
                        "feedback_state": feedback_state,
                    },
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👎 不想看"},
                    "type": "secondary",
                    "name": f"feedback_dislike_{v['video_id']}",
                    "value": {
                        **feedback_meta,
                        "action": "dislike",
                        "card_state": card_state,
                        "feedback_state": feedback_state,
                    },
                },
            ])
        elements.append({"tag": "action", "actions": actions})

    if aihot_items:
        if elements:
            elements.append({"tag": "hr"})
        elements.extend(build_aihot_card_elements(aihot_items, enable_feedback=enable_feedback))

    if videos_with_summaries and aihot_items:
        title = f"📹 YouTube + AI HOT 今日推荐 ({today})"
    elif aihot_items:
        title = f"🔥 AI HOT 今日精选 ({today})"
    else:
        title = f"📹 YouTube 今日推荐 ({today})"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue"
        },
        "elements": elements
    }


def build_status_card_content(title: str, message: str, template: str = "grey") -> dict:
    """构建轻量状态卡片，用于无候选或数据源异常时避免静默失败。"""
    today = digest_date_label()
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📹 YouTube 今日推荐 ({today})"},
            "template": template,
        },
        "elements": [
            {"tag": "markdown", "content": f"**{title}**"},
            {"tag": "markdown", "content": message},
        ],
    }


def get_feishu_target() -> tuple[str, str, str] | None:
    if FEISHU_CHAT_ID:
        return FEISHU_CHAT_ID, "chat_id", "群聊"
    if FEISHU_USER_ID:
        return FEISHU_USER_ID, "user_id", "个人"
    print("  ⚠️ 未配置 FEISHU_CHAT_ID 或 FEISHU_USER_ID")
    return None


def send_card_to_feishu(card: dict, success_message: str) -> bool:
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("  ⚠️ 未配置飞书应用凭证 (FEISHU_APP_ID/SECRET)")
        return False

    target = get_feishu_target()
    if not target:
        return False
    receive_id, receive_id_type, target_label = target

    token = get_tenant_access_token()
    if not token:
        print("  ❌ 无法获取飞书 access token")
        return False

    body = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card)
    }

    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            print(f"  ✅ 飞书应用{target_label}{success_message}")
            return True
        else:
            print(f"  ❌ 飞书应用{target_label}推送失败: {result}")
            return False
    except Exception as e:
        print(f"  ❌ 飞书应用{target_label}推送异常: {e}")
        return False


def send_card_to_webhook(card: dict, success_message: str) -> bool:
    if not FEISHU_WEBHOOK_URL:
        return False

    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=10)
        result = resp.json()
        if result.get("StatusCode") == 0:
            print(f"  ✅ 飞书群 Webhook {success_message}")
            return True
        else:
            print(f"  ❌ 飞书群 Webhook 推送失败: {result}")
            return False
    except Exception as e:
        print(f"  ❌ 飞书群 Webhook 推送异常: {e}")
        return False


def send_digest_to_feishu(videos_with_summaries: list[dict], aihot_items: list[dict] | None = None) -> bool:
    """通过飞书应用机器人发送日报；应用卡片支持按钮回调。"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("  ⚠️ 未配置飞书应用凭证 (FEISHU_APP_ID/SECRET)")
        for item in videos_with_summaries:
            print(f"  📝 {item['video']['title']}\n{item['summary']}\n")
        return False

    card = build_card_content(videos_with_summaries, aihot_items=aihot_items, enable_feedback=True)
    aihot_count = len(aihot_items or [])
    extra = f"，AI HOT {aihot_count} 条" if aihot_count else ""
    return send_card_to_feishu(card, f"推送成功 ({len(videos_with_summaries)} 个视频{extra}，已启用反馈按钮)")


def send_digest_to_webhook(videos_with_summaries: list[dict], aihot_items: list[dict] | None = None) -> bool:
    """通过群自定义机器人 Webhook 发送日报；仅兜底，不支持按钮回调。"""
    card = build_card_content(videos_with_summaries, aihot_items=aihot_items, enable_feedback=False)
    aihot_count = len(aihot_items or [])
    extra = f"，AI HOT {aihot_count} 条" if aihot_count else ""
    return send_card_to_webhook(card, f"推送成功 ({len(videos_with_summaries)} 个视频{extra}，无反馈回调)")


def send_combined_digest(videos_with_summaries: list[dict], aihot_items: list[dict] | None = None) -> bool:
    """Send one daily card containing YouTube recommendations and optional AI HOT items."""
    aihot_items = aihot_items or []
    if not videos_with_summaries and not aihot_items:
        return False

    app_card = build_card_content(
        videos_with_summaries,
        aihot_items=aihot_items,
        enable_feedback=True,
    )
    parts = []
    if videos_with_summaries:
        parts.append(f"{len(videos_with_summaries)} 个视频")
    if aihot_items:
        parts.append(f"AI HOT {len(aihot_items)} 条")
    success_message = f"推送成功 ({'，'.join(parts)})"
    if send_card_to_feishu(app_card, success_message):
        return True
    webhook_card = build_card_content(
        videos_with_summaries,
        aihot_items=aihot_items,
        enable_feedback=False,
    )
    return send_card_to_webhook(webhook_card, success_message)


def load_ranking_hints() -> str:
    path = Path("ranking_hints.txt")
    return path.read_text().strip() if path.exists() else ""


def send_status_to_feishu(title: str, message: str, template: str = "grey") -> bool:
    if not FEISHU_SEND_STATUS_CARD:
        print(f"  ℹ️ 状态卡推送已关闭: {title}")
        return False

    card = build_status_card_content(title, message, template)
    if send_card_to_feishu(card, "状态推送成功"):
        return True
    return send_card_to_webhook(card, "状态推送成功")


# ============ 主流程 ============
def main():
    print(f"🚀 YouTube Digest 启动 - {datetime.now(timezone.utc).isoformat()}")

    channels = load_channels()
    if not channels:
        print("❌ 无频道配置，退出")
        return

    profile = load_profile()
    top_n = get_digest_top_n(profile)
    print(f"   过滤: 非 Shorts (>{MIN_DURATION_MINUTES}min), 最近 {LOOKBACK_HOURS}h, Top {top_n}\n")
    history = load_history()
    now_iso = datetime.now(timezone.utc).isoformat()
    aihot_items = fetch_aihot_items(
        LOOKBACK_HOURS,
        profile=profile,
        ranking_hints=load_ranking_hints(),
    )
    if aihot_items:
        print(f"🔥 AI HOT 精选 {len(aihot_items)} 条，将合并到今日推送")

    # 第一阶段：并发拉取所有频道 RSS
    print(f"📡 并发拉取 {len(channels)} 个频道 RSS...")
    all_rss_videos = {}  # channel_id → videos
    rss_failed_channel_ids = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ch = {
            executor.submit(fetch_rss_videos, ch["channel_id"]): ch
            for ch in channels
        }
        for future in as_completed(future_to_ch):
            ch = future_to_ch[future]
            try:
                videos, rss_ok = future.result()
                if not rss_ok:
                    rss_failed_channel_ids.append(ch["channel_id"])
                if videos:
                    all_rss_videos[ch["channel_id"]] = videos
            except Exception as e:
                print(f"  ⚠️ {ch.get('name', ch['channel_id'])}: {e}")
                rss_failed_channel_ids.append(ch["channel_id"])

    total_rss = sum(len(v) for v in all_rss_videos.values())
    print(f"   RSS 共发现 {total_rss} 个新视频（来自 {len(all_rss_videos)} 个频道，失败 {len(rss_failed_channel_ids)} 个）")

    fallback_channel_ids = rss_failed_channel_ids
    if fallback_channel_ids and YOUTUBE_API_KEY:
        print(f"🔁 RSS 失败的 {len(fallback_channel_ids)} 个频道，使用 YouTube Data API 兜底...")
        upload_playlists = fetch_channel_upload_playlists(fallback_channel_ids)
        api_fallback_videos = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_ch = {
                executor.submit(fetch_upload_playlist_videos, channel_id, playlist_id): channel_id
                for channel_id, playlist_id in upload_playlists.items()
            }
            for future in as_completed(future_to_ch):
                channel_id = future_to_ch[future]
                try:
                    videos = future.result()
                    if videos:
                        api_fallback_videos[channel_id] = videos
                except Exception as e:
                    print(f"  ⚠️ YouTube API fallback error for {channel_id}: {e}")
        all_rss_videos.update(api_fallback_videos)
        total_api_fallback = sum(len(v) for v in api_fallback_videos.values())
        print(f"   API 兜底发现 {total_api_fallback} 个新视频（来自 {len(api_fallback_videos)} 个频道）")
    elif fallback_channel_ids:
        print("  ⚠️ 未配置 YOUTUBE_API_KEY，无法在 RSS 失败时兜底")

    total_rss = sum(len(v) for v in all_rss_videos.values())
    print(f"   共发现 {total_rss} 个新视频（来自 {len(all_rss_videos)} 个频道）\n")

    # 收集候选长视频（带 quota 保护）
    candidates = []
    api_calls = 0
    API_QUOTA_LIMIT = 3000  # 保守限制，留余量

    for ch in channels:
        channel_id = ch["channel_id"]
        videos = all_rss_videos.get(channel_id, [])
        if not videos:
            continue

        for video in videos:
            vid = video["video_id"]
            if vid in history:
                continue

            if api_calls >= API_QUOTA_LIMIT:
                print(f"  ⚠️ YouTube API quota 接近上限 ({api_calls} calls)，停止获取详情")
                break

            details = get_video_details(vid)
            api_calls += 1
            duration_sec = details["duration"]
            if duration_sec < MIN_DURATION_MINUTES * 60:
                history[vid] = now_iso
                continue

            video["duration_sec"] = duration_sec
            video["duration_str"] = format_duration(duration_sec)
            video["description"] = details["description"]
            video["view_count"] = details["view_count"]
            candidates.append(video)
            print(f"   🎬 候选: {video['title']} ({video['duration_str']}, {format_view_count(video['view_count'])} views)")

    if not candidates:
        print("\n📭 没有新的长视频候选")
        if not send_combined_digest([], aihot_items):
            send_status_to_feishu(
                "今天没有符合条件的新长视频",
                f"已扫描 {len(channels)} 个频道，最近 {LOOKBACK_HOURS} 小时没有发现满足时长和去重条件的视频。",
            )
        save_history(history)
        return

    # 第二阶段：预过滤 + LLM 智能筛选
    # 硬规则预过滤：剔除明显不符合的候选
    preferred_channels = set(profile.get("preferred_channels", []))
    exclude_patterns = profile.get("exclude_title_patterns", [])
    exclude_re = re.compile(
        r"(?i)(" + "|".join(re.escape(p) for p in exclude_patterns) + ")"
    ) if exclude_patterns else None
    exclude_content_patterns = profile.get("exclude_content_patterns", [])
    exclude_content_re = re.compile(
        r"(?i)(" + "|".join(re.escape(p) for p in exclude_content_patterns) + ")"
    ) if exclude_content_patterns else None

    channel_filters = profile.get("channel_filters", {})

    filtered = []
    for v in candidates:
        # 排除教程、投资金融、纯技术实现等明确不感兴趣的标题
        if exclude_re and exclude_re.search(v["title"]):
            print(f"   ⛔ 预过滤（标题排除）: {v['title']}")
            continue
        # 排除标题/描述中明显偏投资或偏纯技术的内容
        content_text = f"{v['title']}\n{v.get('description') or ''}"
        if exclude_content_re and exclude_content_re.search(content_text):
            print(f"   ⛔ 预过滤（不感兴趣主题）: {v['title']}")
            continue
        # 播放量极低且不是常看频道 → 排除
        is_preferred = any(pc.lower() in v["author"].lower() for pc in preferred_channels)
        if v["view_count"] < 200 and not is_preferred:
            print(f"   ⛔ 预过滤（低播放量非常看频道）: {v['title']} ({format_view_count(v['view_count'])} views)")
            continue
        # 频道专属过滤规则
        channel_skipped = False
        for ch_name, ch_rule in channel_filters.items():
            if ch_name.lower() not in v["author"].lower():
                continue
            min_duration = ch_rule.get("min_duration_seconds", 0)
            duration_value = v.get("duration_sec", v.get("duration_seconds", 0))
            if min_duration and duration_value < min_duration:
                print(f"   ⛔ 预过滤（{ch_name} 时长过短）: {v['title']}")
                channel_skipped = True
                break
            exclude_keywords = ch_rule.get("exclude_title_keywords", [])
            if exclude_keywords:
                kw_re = re.compile(r"(?i)(" + "|".join(re.escape(k) for k in exclude_keywords) + ")")
                if kw_re.search(v["title"]):
                    print(f"   ⛔ 预过滤（{ch_name} 排除主题）: {v['title']}")
                    channel_skipped = True
                    break
            exclude_description_keywords = ch_rule.get("exclude_description_keywords", [])
            if exclude_description_keywords:
                kw_re = re.compile(r"(?i)(" + "|".join(re.escape(k) for k in exclude_description_keywords) + ")")
                if kw_re.search(v.get("description") or ""):
                    print(f"   ⛔ 预过滤（{ch_name} 描述排除主题）: {v['title']}")
                    channel_skipped = True
                    break
            require_keywords = ch_rule.get("require_title_keywords", [])
            if require_keywords:
                kw_re = re.compile(r"(?i)(" + "|".join(re.escape(k) for k in require_keywords) + ")")
                if not kw_re.search(v["title"]):
                    print(f"   ⛔ 预过滤（{ch_name} 非目标内容）: {v['title']}")
                    channel_skipped = True
                    break
        if channel_skipped:
            continue
        filtered.append(v)

    if not filtered:
        print("\n📭 预过滤后没有候选视频")
        if not send_combined_digest([], aihot_items):
            send_status_to_feishu(
                "今天没有符合偏好的推荐",
                f"已发现 {len(candidates)} 个新长视频，但都被偏好规则过滤掉了。主要会过滤投资、纯技术细节、入门教程和低信息密度内容。",
            )
        save_history(history)
        return

    if len(filtered) < len(candidates):
        print(f"   📋 预过滤: {len(candidates)} → {len(filtered)} 个候选")

    print(f"\n🤖 LLM 正在从 {len(filtered)} 个候选中筛选 Top {top_n}...")
    ranked = rank_candidates(filtered, top_n, profile)
    top_videos = [filtered[r["index"]] for r in ranked]
    # 把推荐理由挂到 video 上
    for r, v in zip(ranked, top_videos):
        v["reason"] = r["reason"]
    print(f"\n🏆 LLM 推荐 Top {len(top_videos)}:")
    for i, v in enumerate(top_videos, 1):
        reason = f" → {v['reason']}" if v.get("reason") else ""
        print(f"   {i}. [{v['author']}] {v['title']} ({v['duration_str']}, {format_view_count(v['view_count'])} views){reason}")

    # 第三阶段：生成摘要 + 合并推送
    videos_with_summaries = []
    for video in top_videos:
        # 摘要优先用字幕（内容最完整），fallback 到 description
        print(f"   📝 生成摘要: {video['title']}")
        transcript = get_transcript(video["video_id"])
        if transcript:
            result = summarize_with_llm(video["title"], video["author"], transcript, "字幕")
            summary_text = result["summary"]
        elif video["description"] and len(video["description"]) > 50:
            print(f"      ⚠️ 无字幕，使用 description")
            result = summarize_with_llm(video["title"], video["author"], video["description"], "描述")
            summary_text = result["summary"]
        else:
            summary_text = "⚠️ 无字幕且描述信息不足，请直接观看"

        videos_with_summaries.append({"video": video, "summary": summary_text})
        history[video["video_id"]] = now_iso
        time.sleep(1)

    # 合并为一条日报推送。优先用飞书应用机器人，才支持卡片点击反馈；Webhook 仅兜底。
    send_combined_digest(videos_with_summaries, aihot_items)

    # 未入选的也标记为已处理
    for video in candidates:
        history[video["video_id"]] = now_iso

    save_history(history)
    aihot_note = f"，AI HOT {len(aihot_items)} 条" if aihot_items else ""
    print(f"\n✅ 完成，共推送 {len(top_videos)} 个视频{aihot_note}（候选 {len(candidates)} 个，API 调用 {api_calls} 次）")


if __name__ == "__main__":
    main()
