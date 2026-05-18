"""
Analyze Feishu feedback and turn it into ranking hints for main.py.

The callback worker writes feedback.json on the data branch. CI restores it,
runs this script, and saves updated profile.json + ranking_hints.txt back.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


FEEDBACK_FILE = os.environ.get("FEEDBACK_FILE", "feedback.json")
PROFILE_FILE = os.environ.get("PROFILE_FILE", "profile.json")
RANKING_HINTS_FILE = os.environ.get("RANKING_HINTS_FILE", "ranking_hints.txt")

CHANNEL_TOPICS = {
    "a16z": ["vc投资", "创业", "商业模式"],
    "sequoia": ["vc投资", "创业", "商业模式"],
    "20vc": ["vc投资", "创业", "增长策略"],
    "invest like the best": ["投资分析", "商业案例"],
    "acquired": ["投资分析", "商业案例", "战略分析"],
    "lenny": ["产品增长", "产品策略"],
    "peter yang": ["ai产品", "产品策略"],
    "hamel": ["ai产品", "llm应用", "开发者工具"],
    "latent space": ["ai产品", "llm应用", "ai工程"],
    "ai engineer": ["ai产品", "llm应用", "开发者工具"],
    "figma": ["产品设计", "用户体验"],
    "openai": ["openai", "llm", "ai产品"],
    "anthropic": ["claude", "llm", "ai产品"],
    "langchain": ["ai工程", "llm框架", "开发者工具"],
    "llamaindex": ["ai工程", "rag", "开发者工具"],
    "weaviate": ["ai工程", "向量数据库"],
    "stanford": ["ai学术", "课程", "技术教程"],
}

TITLE_TOPIC_KEYWORDS = {
    "ai产品": ["product", "pm", "ux", "user", "用户", "产品"],
    "广告创意智能体": ["creative", "ads", "advertising", "marketing", "agent", "广告", "创意", "智能体"],
    "增长/商业化": ["growth", "gtm", "go-to-market", "sales", "pricing", "商业化", "增长"],
    "投资/金融内容": ["stock", "investing", "investment", "portfolio", "valuation", "fundraising", "ipo", "融资", "估值", "股票", "基金"],
    "纯技术细节": ["implementation", "code", "api", "rag", "vector", "paper", "arxiv", "代码", "论文", "架构", "调参"],
}


def load_json(path: str) -> dict:
    if not Path(path).exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def infer_topics(title: str, author: str) -> set[str]:
    text = f"{title} {author}".lower()
    topics = set()

    for channel_pattern, channel_topics in CHANNEL_TOPICS.items():
        if channel_pattern in text:
            topics.update(channel_topics)

    for topic, keywords in TITLE_TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            topics.add(topic)

    return topics


def get_topic_preferences(feedback: dict) -> tuple[dict, dict]:
    topic_signals: dict[str, dict[str, float]] = {}
    author_signals: dict[str, dict[str, float]] = {}

    for data in feedback.values():
        meta = data.get("video_meta", {})
        title = meta.get("title") or ""
        author = meta.get("author") or ""
        author_key = author.strip()
        reactions = data.get("reactions", [])[-3:]
        if not reactions:
            continue

        topics = infer_topics(title, author)

        for reaction_data in reactions:
            reaction = reaction_data.get("reaction")
            reason = reaction_data.get("reason")
            reason_topics = set()

            if reason == "too_investment":
                reason_topics.add("投资/金融内容")
            elif reason == "too_technical":
                reason_topics.add("纯技术细节")
            elif reason == "too_much_info":
                reason_topics.add("信息过载/过长内容")
            elif reason == "too_shallow":
                reason_topics.add("浅内容/入门教程")
            elif isinstance(reason, str) and reason.startswith("custom:"):
                reason_topics.add(f"不喜欢: {reason[7:].strip()}")

            scoped_topics = set(topics)
            if reaction == "dislike" and reason == "too_investment":
                scoped_topics = {topic for topic in topics if "投资" in topic or "金融" in topic or topic == "vc投资"}
            elif reaction == "dislike" and reason == "too_technical":
                scoped_topics = {topic for topic in topics if topic in {"纯技术细节", "ai工程", "llm框架", "开发者工具", "技术教程", "ai学术"}}

            scoped_topics.update(reason_topics)

            for topic in scoped_topics:
                topic_signals.setdefault(topic, {"like": 0, "dislike": 0})
                if reaction == "like":
                    topic_signals[topic]["like"] += 1
                elif reaction == "dislike":
                    topic_signals[topic]["dislike"] += 1

            if author_key:
                author_signals.setdefault(author_key, {"like": 0, "dislike": 0})
                if reaction == "like":
                    author_signals[author_key]["like"] += 1
                elif reaction == "dislike":
                    author_signals[author_key]["dislike"] += 1

    return topic_signals, author_signals


def update_profile(profile: dict, topic_signals: dict):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inferred = profile.setdefault("inferred_preferences", {
        "topic_signals": {},
        "last_updated": None,
        "history": [],
    })

    existing = inferred.get("topic_signals", {})
    for topic, signals in topic_signals.items():
        previous = existing.get(topic, {"like": 0, "dislike": 0})
        existing[topic] = {
            "like": previous.get("like", 0) * 0.7 + signals.get("like", 0),
            "dislike": previous.get("dislike", 0) * 0.7 + signals.get("dislike", 0),
        }

    inferred["topic_signals"] = existing
    inferred["last_updated"] = now
    inferred.setdefault("history", []).append({
        "date": now,
        "signals": topic_signals,
    })
    inferred["history"] = inferred["history"][-7:]


def update_author_preferences(profile: dict, author_signals: dict):
    inferred = profile.setdefault("inferred_preferences", {
        "topic_signals": {},
        "last_updated": None,
        "history": [],
    })
    existing = inferred.get("author_signals", {})
    for author, signals in author_signals.items():
        previous = existing.get(author, {"like": 0, "dislike": 0})
        existing[author] = {
            "like": previous.get("like", 0) * 0.7 + signals.get("like", 0),
            "dislike": previous.get("dislike", 0) * 0.7 + signals.get("dislike", 0),
        }
    inferred["author_signals"] = existing


def build_ranking_hints(profile: dict) -> str:
    signals = profile.get("inferred_preferences", {}).get("topic_signals", {})
    author_signals = profile.get("inferred_preferences", {}).get("author_signals", {})
    if not signals and not author_signals:
        return ""

    hints = ["基于你的近期点击反馈，额外调整："]
    for topic, data in sorted(signals.items(), key=lambda item: item[1].get("like", 0) - item[1].get("dislike", 0)):
        delta = data.get("like", 0) - data.get("dislike", 0)
        if delta <= -2:
            hints.append(f"- 强烈回避: {topic}")
        elif delta < 0:
            hints.append(f"- 回避: {topic}")
        elif delta >= 2:
            hints.append(f"+ 强烈偏好: {topic}")
        elif delta > 0:
            hints.append(f"+ 偏好: {topic}")

    for author, data in sorted(author_signals.items(), key=lambda item: item[1].get("dislike", 0) - item[1].get("like", 0), reverse=True):
        like = data.get("like", 0)
        dislike = data.get("dislike", 0)
        if dislike >= 4 and dislike - like >= 3:
            hints.append(f"- 频道级回避: {author}（多次点踩后触发，除非标题明显命中强偏好主题，否则不要选）")

    return "\n".join(hints) if len(hints) > 1 else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="use local files; kept for compatibility")
    parser.parse_args()

    feedback = load_json(FEEDBACK_FILE)
    if not feedback:
        Path(RANKING_HINTS_FILE).write_text("")
        print("📊 没有反馈数据，已清空 ranking_hints.txt")
        return

    profile = load_json(PROFILE_FILE)
    topic_signals, author_signals = get_topic_preferences(feedback)
    update_profile(profile, topic_signals)
    update_author_preferences(profile, author_signals)
    ranking_hints = build_ranking_hints(profile)

    save_json(PROFILE_FILE, profile)
    Path(RANKING_HINTS_FILE).write_text(ranking_hints)

    print(f"📊 已分析 {len(feedback)} 条反馈")
    if ranking_hints:
        print(ranking_hints)
    else:
        print("暂无足够强的动态偏好信号")


if __name__ == "__main__":
    main()
