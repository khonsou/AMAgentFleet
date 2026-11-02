"""Agent YAML 目录的结构与平台字段边界。"""

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "control-plane" / "agents"
PLATFORM_FIELDS = {"egress", "triggers", "identity", "budget", "persistence"}


class AgentYamlCatalogTests(unittest.TestCase):
    def test_every_agent_has_matching_identity_and_platform_fields_at_root(self):
        paths = sorted(AGENTS.glob("*.yaml"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(agent=path.name):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertEqual(doc["apiVersion"], "agentfleet/v1")
                self.assertEqual(doc["kind"], "Agent")
                self.assertEqual(doc["metadata"]["name"], path.stem)
                spec = doc["spec"]
                self.assertEqual(spec["agent"]["name"], path.stem)
                self.assertTrue(PLATFORM_FIELDS.isdisjoint(spec))
                self.assertIsInstance(doc.get("egress", []), list)
                self.assertIsInstance(doc.get("triggers", {}), dict)
                self.assertIsInstance(doc.get("identity", {}), dict)
                self.assertIsInstance(doc.get("budget", {}), dict)


if __name__ == "__main__":
    unittest.main()
