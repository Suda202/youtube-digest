import inspect
import unittest
from unittest import mock

import main


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class AihotIntegrationTests(unittest.TestCase):
    def test_aihot_model_selection_uses_configured_summary_llm(self):
        items = [{
            "id": "agent-workflow",
            "title": "Agentic Engineering workflow for coding agents",
            "summary": "A practical workflow for AI product teams.",
            "source": "Example",
            "category": "ai-products",
            "score": 88,
            "url": "https://example.com/agent-workflow",
        }]

        with (
            mock.patch.object(main, "DEEPSEEK_API_KEY", "test-key"),
            mock.patch.object(
                main,
                "call_llm",
                return_value='{"selected_ids":["agent-workflow"]}',
            ) as call_llm,
        ):
            selected = main.select_aihot_items_for_profile(items, take=1)

        self.assertEqual([item["id"] for item in selected], ["agent-workflow"])
        call_llm.assert_called_once()

    def test_fetch_aihot_items_uses_selected_window_and_skill_user_agent(self):
        fetch_aihot_items = getattr(main, "fetch_aihot_items", None)
        if fetch_aihot_items is None:
            self.fail("fetch_aihot_items should exist")

        payload = {
            "items": [
                {
                    "id": "item_1",
                    "title": "OpenAI 发布新模型",
                    "url": "https://example.com/openai",
                    "source": "OpenAI Blog",
                    "publishedAt": "2026-06-18T01:30:00.000Z",
                    "summary": "面向产品和工作流的更新。",
                    "category": "ai-models",
                    "score": 92,
                    "selected": True,
                }
            ]
        }

        with mock.patch.object(main.requests, "get", return_value=FakeResponse(payload)) as get:
            items = fetch_aihot_items(hours=24, take=3)

        self.assertEqual(items[0]["title"], "OpenAI 发布新模型")
        get.assert_called_once()
        _, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["mode"], "selected")
        self.assertEqual(kwargs["params"]["take"], 3)
        self.assertIn("since", kwargs["params"])
        self.assertTrue(kwargs["params"]["since"].endswith("Z"))
        self.assertIn("aihot-skill/0.2.0", kwargs["headers"]["User-Agent"])

    def test_fetch_aihot_items_reranks_for_user_interests_and_geo_research(self):
        payload = {
            "items": [
                {
                    "id": "item_stock",
                    "title": "AI 芯片公司股价大涨，分析师上调估值",
                    "url": "https://example.com/stock",
                    "source": "Market News",
                    "publishedAt": "2026-06-18T01:30:00.000Z",
                    "summary": "主要讨论股票、估值和投资机会。",
                    "category": "industry",
                    "score": 96,
                    "selected": True,
                },
                {
                    "id": "item_geo",
                    "title": "海外 SaaS 团队开始做 GEO：优化 ChatGPT 和 AI 搜索里的品牌可见性",
                    "url": "https://example.com/geo",
                    "source": "Growth Blog",
                    "publishedAt": "2026-06-18T01:10:00.000Z",
                    "summary": "讨论 Generative Engine Optimization、AI 搜索引用和出海内容增长。",
                    "category": "ai-products",
                    "score": 70,
                    "selected": True,
                },
                {
                    "id": "item_code",
                    "title": "从零开始实现向量数据库教程",
                    "url": "https://example.com/code",
                    "source": "Dev Blog",
                    "publishedAt": "2026-06-18T01:00:00.000Z",
                    "summary": "包含代码实现和 API 参数。",
                    "category": "tip",
                    "score": 82,
                    "selected": True,
                },
            ]
        }
        profile = {
            "favorite_content": "海外 SaaS 增长、广告创意智能体、产品策略、海外 GEO 调研",
            "deprioritize_topics": ["投资", "股票", "估值", "代码实现", "API 参数"],
        }

        with mock.patch.object(main.requests, "get", return_value=FakeResponse(payload)):
            items = main.fetch_aihot_items(hours=24, take=2, profile=profile)

        self.assertEqual([item["id"] for item in items], ["item_geo"])
        self.assertIn("GEO", items[0]["match_tags"])
        self.assertIn("海外增长", items[0]["match_tags"])

    def test_fetch_aihot_items_reranks_for_agentic_engineering_and_vibe_coding(self):
        payload = {
            "items": [
                {
                    "id": "item_generic_model",
                    "title": "大模型厂商发布参数更高的新基座模型",
                    "url": "https://example.com/model",
                    "source": "AI News",
                    "publishedAt": "2026-06-18T02:00:00.000Z",
                    "summary": "主要讨论模型参数和榜单成绩。",
                    "category": "ai-models",
                    "score": 95,
                    "selected": True,
                },
                {
                    "id": "item_agentic",
                    "title": "Harness 发布 Agentic Engineering 工作流，支持 Coding Agent 自动修复 CI",
                    "url": "https://example.com/harness",
                    "source": "Harness Blog",
                    "publishedAt": "2026-06-18T01:30:00.000Z",
                    "summary": "讨论 agentic software engineering、loop coding 和 vibe coding 对研发流程的影响。",
                    "category": "ai-products",
                    "score": 71,
                    "selected": True,
                },
            ]
        }
        profile = {
            "favorite_content": "Agentic Engineering、Harness、Loop Coding、Vibe Coding、Coding Agent",
            "deprioritize_topics": ["模型参数", "榜单"],
        }

        with mock.patch.object(main.requests, "get", return_value=FakeResponse(payload)):
            items = main.fetch_aihot_items(hours=24, take=1, profile=profile)

        self.assertEqual(items[0]["id"], "item_agentic")
        self.assertIn("Agentic Engineering", items[0]["match_tags"])
        self.assertIn("Vibe Coding", items[0]["match_tags"])

    def test_aihot_quality_gate_rejects_generic_vertical_ai_news(self):
        items = [
            {
                "id": "gaokao_agent",
                "title": "国内首个高考志愿AI测评出炉，千问多项表现超过资深咨询师",
                "summary": "测试高考志愿填报Agent模块，覆盖院校和专业数据。",
                "source": "公众号：千问APP",
                "category": "ai-products",
                "score": 88,
                "url": "https://example.com/gaokao",
            },
            {
                "id": "cyber_threat",
                "title": "五眼联盟警告：AI网络威胁数月内将影响普通用户",
                "summary": "自动化智能体可扫描漏洞，钓鱼诈骗和勒索软件增长。",
                "source": "AI News",
                "category": "industry",
                "score": 86,
                "url": "https://example.com/cyber",
            },
            {
                "id": "model_release",
                "title": "京东全栈开源JoyAI-VL-Interaction，从一问一答走向边看边说",
                "summary": "发布开源模型，多个基准和盲评胜率表现突出。",
                "source": "公众号：京东JoyAI",
                "category": "ai-models",
                "score": 90,
                "url": "https://example.com/model",
            },
            {
                "id": "coding_agent_eval",
                "title": "Google Labs 提出用洞察策略评估 AI 编码智能体的主动性",
                "summary": "评估 AI coding agent 是否能主动发现开发者真实目标。",
                "source": "Google Developers Blog",
                "category": "paper",
                "score": 61,
                "url": "https://example.com/coding-agent-eval",
            },
        ]

        selected = main.select_aihot_items_for_profile(
            items,
            {"favorite_content": "Agentic Engineering、Coding Agent、产品策略"},
            take=5,
        )

        self.assertEqual([item["id"] for item in selected], ["coding_agent_eval"])
        self.assertEqual(selected[0]["selection_lane"], "agent")

    def test_build_card_content_renders_aihot_as_title_and_summary_only(self):
        signature = inspect.signature(main.build_card_content)
        if "aihot_items" not in signature.parameters:
            self.fail("build_card_content should accept aihot_items")

        card = main.build_card_content(
            [],
            aihot_items=[
                {
                    "title": "Anthropic 发布产品更新",
                    "url": "https://example.com/anthropic",
                    "source": "不应显示的来源",
                    "publishedAt": "2026-06-18T02:30:00.000Z",
                    "summary": "重点影响企业 AI 工作流。",
                    "category": "ai-products",
                    "score": 89,
                }
            ],
            enable_feedback=True,
        )

        self.assertIn("AI HOT", card["header"]["title"]["content"])
        markdown_blocks = [
            element.get("content", "")
            for element in card["elements"]
            if element.get("tag") == "markdown"
        ]
        self.assertEqual(markdown_blocks, [
            "**1. Anthropic 发布产品更新**",
            "重点影响企业 AI 工作流。",
        ])
        rendered_text = "\n".join(markdown_blocks)
        self.assertNotIn("不应显示的来源", rendered_text)
        self.assertNotIn("89 分", rendered_text)
        self.assertNotIn("ai-products", rendered_text)
        self.assertNotIn("今天", rendered_text)
        action_text = [
            action["text"]["content"]
            for element in card["elements"]
            if element.get("tag") == "action"
            for action in element["actions"]
        ]
        self.assertIn("查看原文", action_text)
        self.assertIn("👍 有用", action_text)
        self.assertIn("👎 不想看", action_text)

        feedback_values = [
            action.get("value")
            for element in card["elements"]
            if element.get("tag") == "action"
            for action in element["actions"]
            if action.get("value")
        ]
        self.assertEqual(feedback_values[0]["content_type"], "aihot")
        self.assertTrue(feedback_values[0]["content_id"].startswith("aihot:"))
        self.assertEqual(feedback_values[0]["creator"], "不应显示的来源")

    def test_aihot_items_are_separated_without_repeating_section_heading(self):
        elements = main.build_aihot_card_elements([
            {"title": "第一条", "summary": "第一条摘要", "url": "https://example.com/1"},
            {"title": "第二条", "summary": "第二条摘要", "url": "https://example.com/2"},
        ])

        self.assertEqual(
            [element["tag"] for element in elements],
            ["markdown", "markdown", "action", "hr", "markdown", "markdown", "action"],
        )
        markdown_text = "\n".join(
            element["content"] for element in elements if element["tag"] == "markdown"
        )
        self.assertNotIn("AI HOT 精选", markdown_text)

    def test_aihot_summary_is_split_into_readable_paragraphs(self):
        elements = main.build_aihot_card_elements([{
            "title": "产品更新",
            "summary": "先说核心变化。再解释对用户的影响！最后给出适用场景？",
            "url": "https://example.com/update",
        }])

        summary_blocks = [
            element["content"]
            for element in elements
            if element["tag"] == "markdown" and not element["content"].startswith("**")
        ]
        self.assertEqual(
            summary_blocks,
            ["先说核心变化。\n\n再解释对用户的影响！\n\n最后给出适用场景？"],
        )

    def test_send_combined_digest_uses_aihot_items_when_video_list_is_empty(self):
        send_combined_digest = getattr(main, "send_combined_digest", None)
        if send_combined_digest is None:
            self.fail("send_combined_digest should exist")

        with mock.patch.object(main, "send_card_to_feishu", return_value=True) as send_card_to_feishu, \
             mock.patch.object(main, "send_card_to_webhook") as send_card_to_webhook:
            sent = send_combined_digest(
                [],
                [{"title": "AI 行业动态", "url": "https://example.com", "source": "AI HOT"}],
            )

        self.assertTrue(sent)
        send_card_to_feishu.assert_called_once()
        send_card_to_webhook.assert_not_called()
        sent_card = send_card_to_feishu.call_args.args[0]
        action_text = [
            action["text"]["content"]
            for element in sent_card["elements"]
            if element.get("tag") == "action"
            for action in element["actions"]
        ]
        self.assertIn("👍 有用", action_text)

    def test_webhook_fallback_rebuilds_card_without_feedback_buttons(self):
        with mock.patch.object(main, "send_card_to_feishu", return_value=False), \
             mock.patch.object(main, "send_card_to_webhook", return_value=True) as webhook:
            sent = main.send_combined_digest(
                [],
                [{"id": "one", "title": "AI 动态", "url": "https://example.com/one"}],
            )

        self.assertTrue(sent)
        webhook_card = webhook.call_args.args[0]
        action_text = [
            action["text"]["content"]
            for element in webhook_card["elements"]
            if element.get("tag") == "action"
            for action in element["actions"]
        ]
        self.assertEqual(action_text, ["查看原文"])

    def test_quality_gate_keeps_agent_practice_and_rejects_promotions_and_resale(self):
        items = [
            {
                "id": "deep-agents",
                "title": "Deep Agents 实战教程：构建可持续运行的 Agent",
                "summary": "从规划、记忆和工具调用讲解 Agent 工作流。",
                "source": "LangChain",
                "category": "tip",
                "score": 75,
                "url": "https://example.com/deep-agents",
            },
            {
                "id": "loop",
                "title": "Loop Engineering is replacing one-shot vibe coding",
                "summary": "A Silicon Valley workflow for coding agents and harness design.",
                "source": "Addy Osmani",
                "category": "industry",
                "score": 78,
                "url": "https://example.com/loop",
            },
            {
                "id": "resale",
                "title": "微软双向转售 GPT 与 DeepSeek，成为全球最大 AI 中间商",
                "summary": "讨论模型转售安排。",
                "source": "Business Wire",
                "category": "industry",
                "score": 90,
                "url": "https://example.com/resale",
            },
            {
                "id": "promo",
                "title": "腾讯元宝父亲节活动：上传照片生成与年轻爸爸的合影",
                "summary": "节日营销活动。",
                "source": "Tencent",
                "category": "ai-products",
                "score": 88,
                "url": "https://example.com/promo",
            },
        ]

        selected = main.select_aihot_items_for_profile(
            items,
            {"favorite_content": "Agent、Loop Engineering、硅谷前沿趋势"},
            take=7,
        )

        self.assertEqual([item["id"] for item in selected], ["loop", "deep-agents"])
        self.assertIn("Loop Engineering", selected[0]["match_tags"])
        self.assertIn("Agent", selected[1]["match_tags"])


if __name__ == "__main__":
    unittest.main()
