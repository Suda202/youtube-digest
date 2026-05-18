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
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============ 配置 ============
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_USER_ID = os.environ.get("FEISHU_USER_ID", "")  # 目标用户 ID (ou_xxxxx)
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")  # 目标群 ID (oc_xxxxx)
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")  # 群自定义机器人 Webhook（仅兜底，无点击回调）
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_API_BASE = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com/anthropic")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
MIN_DURATION_MINUTES = int(os.environ.get("MIN_DURATION_MINUTES", "3"))  # 过滤 Shorts（<=3min）
TOP_N = int(os.environ.get("TOP_N", "3"))  # 每日推送 Top N 视频
SUMMARY_MAX_TOKENS = int(os.environ.get("SUMMARY_MAX_TOKENS", "700"))
SUMMARY_MAX_CHARS = int(os.environ.get("SUMMARY_MAX_CHARS", "700"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
CHANNELS_FILE = os.environ.get("CHANNELS_FILE", "channels.json")
PROFILE_FILE = os.environ.get("PROFILE_FILE", "profile.json")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "history.json")
HISTORY_MAX_DAYS = int(os.environ.get("HISTORY_MAX_DAYS", "30"))


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


# ============ YouTube RSS ============
def fetch_rss_videos(channel_id: str) -> list[dict]:
    """从 YouTube RSS 获取频道最新视频"""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️ RSS fetch failed for {channel_id}: {e}")
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    root = ET.fromstring(resp.text)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    videos = []

    for entry in root.findall("atom:entry", ns):
        published_str = entry.find("atom:published", ns).text
        published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        if published < cutoff:
            continue

        video_id = entry.find("yt:videoId", ns).text
        title = entry.find("atom:title", ns).text
        author = root.find("atom:title", ns).text

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


# ============ Minimax 摘要 ============
def summarize_with_llm(title: str, author: str, content: str, content_type: str = "字幕") -> dict:
    """基于字幕或描述生成结构化摘要"""
    if not MINIMAX_API_KEY:
        return {"summary": "⚠️ 未配置 MINIMAX_API_KEY，跳过摘要"}

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
        return {"summary": result}
    return {"summary": "摘要生成失败"}


def call_llm(prompt: str, max_tokens: int = 1024) -> str | None:
    """调用 Minimax LLM，返回文本结果"""
    if not MINIMAX_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{MINIMAX_API_BASE}/v1/messages",
            headers={
                "x-api-key": MINIMAX_API_KEY,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "MiniMax-M2.5",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        data = resp.json()
        if data.get("type") == "error":
            print(f"  ⚠️ LLM error: {data.get('error', {}).get('message', str(data))}")
            return None
        # 从 content 数组中提取最后一个 text block
        for block in reversed(data.get("content", [])):
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        # fallback: 尝试直接取第一个 block
        content = data.get("content", [])
        if content and isinstance(content[0], dict):
            return content[0].get("text", str(content[0]))
        return None
    except Exception as e:
        print(f"  ⚠️ LLM call failed: {e}")
        return None


def call_gemini(prompt: str) -> str | None:
    """调用 Gemini 模型，用于排序任务"""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"  ⚠️ Gemini call failed: {e}")
        return None


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

    result = call_gemini(prompt)
    if not result:
        print("  ⚠️ Gemini 排序失败，尝试 MiniMax...")
        result = call_llm(prompt, max_tokens=500)
    if not result:
        print("  ⚠️ LLM 排序全部失败，回退到播放量排序")
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


def trim_summary(summary: str) -> str:
    """Keep Feishu cards readable even when the LLM ignores length guidance."""
    text = (summary or "").strip()
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    trimmed = text[:SUMMARY_MAX_CHARS].rsplit("\n", 1)[0].strip()
    return f"{trimmed}\n...（摘要已截断）"


def build_card_content(videos_with_summaries: list[dict], enable_feedback: bool = False) -> dict:
    """构建飞书卡片消息内容，返回卡片 JSON 结构"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    elements = []

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
            "url": v["url"]
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
                    "value": {**feedback_meta, "action": "like"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👎 不想看"},
                    "type": "secondary",
                    "value": {**feedback_meta, "action": "dislike"}
                },
            ])
        elements.append({"tag": "action", "actions": actions})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📹 YouTube 今日推荐 ({today})"},
            "template": "blue"
        },
        "elements": elements
    }


def send_digest_to_feishu(videos_with_summaries: list[dict]) -> bool:
    """通过飞书应用机器人发送日报；应用卡片支持按钮回调。"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("  ⚠️ 未配置飞书应用凭证 (FEISHU_APP_ID/SECRET)")
        for item in videos_with_summaries:
            print(f"  📝 {item['video']['title']}\n{item['summary']}\n")
        return False

    if FEISHU_CHAT_ID:
        receive_id = FEISHU_CHAT_ID
        receive_id_type = "chat_id"
        target_label = "群聊"
    elif FEISHU_USER_ID:
        receive_id = FEISHU_USER_ID
        receive_id_type = "user_id"
        target_label = "个人"
    else:
        print("  ⚠️ 未配置 FEISHU_CHAT_ID 或 FEISHU_USER_ID")
        return False

    token = get_tenant_access_token()
    if not token:
        print("  ❌ 无法获取飞书 access token")
        return False

    card = build_card_content(videos_with_summaries, enable_feedback=True)

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
            print(f"  ✅ 飞书应用{target_label}推送成功 ({len(videos_with_summaries)} 个视频，已启用反馈按钮)")
            return True
        else:
            print(f"  ❌ 飞书应用{target_label}推送失败: {result}")
            return False
    except Exception as e:
        print(f"  ❌ 飞书应用{target_label}推送异常: {e}")
        return False


def send_digest_to_webhook(videos_with_summaries: list[dict]) -> bool:
    """通过群自定义机器人 Webhook 发送日报；仅兜底，不支持按钮回调。"""
    if not FEISHU_WEBHOOK_URL:
        return False

    body = {
        "msg_type": "interactive",
        "card": build_card_content(videos_with_summaries, enable_feedback=False)
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=body, timeout=10)
        result = resp.json()
        if result.get("StatusCode") == 0:
            print(f"  ✅ 飞书群 Webhook 推送成功 ({len(videos_with_summaries)} 个视频，无反馈回调)")
            return True
        else:
            print(f"  ❌ 飞书群 Webhook 推送失败: {result}")
            return False
    except Exception as e:
        print(f"  ❌ 飞书群 Webhook 推送异常: {e}")
        return False


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

    # 第一阶段：并发拉取所有频道 RSS
    print(f"📡 并发拉取 {len(channels)} 个频道 RSS...")
    all_rss_videos = {}  # channel_id → videos
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ch = {
            executor.submit(fetch_rss_videos, ch["channel_id"]): ch
            for ch in channels
        }
        for future in as_completed(future_to_ch):
            ch = future_to_ch[future]
            try:
                videos = future.result()
                if videos:
                    all_rss_videos[ch["channel_id"]] = videos
            except Exception as e:
                print(f"  ⚠️ {ch.get('name', ch['channel_id'])}: {e}")

    total_rss = sum(len(v) for v in all_rss_videos.values())
    print(f"   共发现 {total_rss} 个新视频（来自 {len(all_rss_videos)} 个频道）\n")

    # 收集候选长视频（带 quota 保护）
    candidates = []
    api_calls = 0
    API_QUOTA_LIMIT = 3000  # 保守限制，留余量（每次调用消耗 3 quota）

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
    if not send_digest_to_feishu(videos_with_summaries):
        send_digest_to_webhook(videos_with_summaries)

    # 未入选的也标记为已处理
    for video in candidates:
        history[video["video_id"]] = now_iso

    save_history(history)
    print(f"\n✅ 完成，共推送 {len(top_videos)} 个视频（候选 {len(candidates)} 个，API 调用 {api_calls} 次）")


if __name__ == "__main__":
    main()
