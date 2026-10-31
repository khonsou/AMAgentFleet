"""Task 群聊只读投影契约（ADR-0031）。"""

import json
import unittest
from pathlib import Path

from app.conversation import build_conversation, normalize_event


class TaskConversationTests(unittest.TestCase):
    def test_protocol_exposes_shared_conversation_projection(self):
        protocol = json.loads((Path(__file__).resolve().parents[2] / "fleet-ops-protocol.json").read_text(encoding="utf-8"))
        operation = next(op for op in protocol["operations"] if op["id"] == "tasks.conversation")
        self.assertEqual(operation["method"], "GET")
        self.assertEqual(operation["path"], "/api/tasks/{task_id}/conversation")
        self.assertNotIn("write", operation)

    def test_multiple_loops_agents_comments_and_outputs_form_one_chat(self):
        detail = {
            "task": {"id": 11, "input": "复盘", "created_at": "2026-10-31T08:00:00Z"},
            "loops": [
                {"id": 21, "claimant_type": "agent", "claimant": "writer", "thread_id": "thread-a", "status": "delivered", "deliverable": "初稿"},
                {"id": 22, "claimant_type": "agent", "claimant": "reviewer", "thread_id": "thread-b", "status": "delivered", "deliverable": "复核"},
            ],
            "comments": [{"id": 5, "author": "human", "content": "请继续", "loop_id": 21, "created_at": "2026-10-31T08:04:00Z"}],
            "children": [],
        }
        events = [
            {"thread_id": "thread-a", "run_id": "run-a", "seq": 1, "event_type": "llm.ai.response", "content": {"type": "ai", "content": "我先整理"}, "created_at": "2026-10-31T08:02:00Z"},
            {"thread_id": "thread-b", "run_id": "run-b", "seq": 1, "event_type": "llm.ai.response", "content": {"type": "ai", "content": "我来复核"}, "created_at": "2026-10-31T08:03:00Z"},
            {"thread_id": "thread-a", "run_id": "run-a", "seq": 2, "event_type": "task_completed", "content": {"description": "subagent 核对数据", "result": "口径一致"}, "created_at": "2026-10-31T08:03:30Z"},
        ]
        chat = build_conversation(detail, events)
        self.assertEqual(len(chat["participants"]), 2)
        self.assertEqual([message["kind"] for message in chat["messages"]], ["message", "message", "subagent", "comment"])
        self.assertEqual({output["loop_id"] for output in chat["outputs"]}, {21, 22})
        self.assertEqual(chat["messages"][2]["actor"], "subagent")
        self.assertEqual(chat["task"]["id"], 11)

    def test_event_normalization_bounds_json_and_text(self):
        raw = {"thread_id": "thread-a", "run_id": "run-a", "seq": 8, "event_type": "llm.ai.response", "content": json.dumps({"type": "ai", "content": [{"text": "你好"}, {"text": " Fleet"}]}), "created_at": "2026-10-31T08:00:00Z"}
        message = normalize_event(raw, {"id": 21, "claimant": "writer"})
        self.assertEqual(message["content"], "你好 Fleet")
        self.assertEqual(message["actor"], "writer")
        self.assertEqual(message["loop_id"], 21)
        self.assertEqual(message["thread_id"], "thread-a")


if __name__ == "__main__":
    unittest.main()
