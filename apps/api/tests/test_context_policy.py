import unittest

from tinylm_glass_box_api.context_policy import EventRecord, build_context


class ContextPolicyTests(unittest.TestCase):
    def test_build_context_keeps_prompt_and_milestones_but_drops_old_ephemeral_items(self):
        events = [
            EventRecord(event_type="seat_completed", seat_name="proposer", payload={"text": "old proposer draft", "milestone": False}),
            EventRecord(event_type="critic_payload", seat_name="critic", payload={"summary": "first critique", "milestone": True}),
            EventRecord(event_type="seat_completed", seat_name="proposer", payload={"text": "recent proposer draft", "milestone": False}),
            EventRecord(event_type="seat_completed", seat_name="critic", payload={"text": "recent critic scratch", "milestone": False}),
        ]

        context = build_context(
            system_prompt="system",
            user_prompt="user",
            events=events,
            max_recent_items=2,
        )

        self.assertEqual(context[0]["content"], "system")
        self.assertEqual(context[1]["content"], "user")
        self.assertTrue(any("first critique" in item["content"] for item in context))
        self.assertTrue(any("recent proposer draft" in item["content"] for item in context))
        self.assertTrue(any("recent critic scratch" in item["content"] for item in context))
        self.assertTrue(all("old proposer draft" not in item["content"] for item in context))
