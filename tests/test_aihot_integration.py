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

        self.assertEqual([item["id"] for item in items], ["item_geo", "item_stock"])
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
        self.assertNotIn("👍 有用", action_text)

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


if __name__ == "__main__":
    unittest.main()
