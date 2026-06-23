from pathlib import Path
import unittest


class WorkflowScheduleTests(unittest.TestCase):
    def test_digest_workflow_uses_single_external_trigger(self):
        workflow = Path(".github/workflows/digest.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)


if __name__ == "__main__":
    unittest.main()
