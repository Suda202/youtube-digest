import unittest

import main


class SummarySafetyTests(unittest.TestCase):
    def test_hides_prompt_leak(self):
        leaked = """根据以下视频描述，生成一份便于快速判断是否值得观看的中文短摘要。

视频标题：Example
频道：Example Channel

格式要求（纯文本，不要 markdown）：
- 第一行用"结论："开头
"""
        self.assertEqual(main.sanitize_summary_text(leaked), main.SUMMARY_PROMPT_LEAK_FALLBACK)
        self.assertEqual(main.trim_summary(leaked), main.SUMMARY_PROMPT_LEAK_FALLBACK)

    def test_keeps_valid_summary(self):
        summary = "结论：这条视频适合快速了解 AI 产品策略。\n（1）聚焦商业化路径\n适合：做产品规划前观看"
        self.assertEqual(main.sanitize_summary_text(summary), summary)


if __name__ == "__main__":
    unittest.main()
