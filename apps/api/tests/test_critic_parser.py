import unittest

from tinylm_glass_box_api.critic import CriticPayload, parse_critic_payload


class CriticParserTests(unittest.TestCase):
    def test_parse_critic_payload_returns_typed_payload_for_valid_json(self):
        payload = parse_critic_payload(
            """
            {
              "summary": "Main issue is unsupported claim.",
              "key_objections": ["Unsupported claim", "Missing evidence"],
              "confidence": 0.62,
              "failure_modes": ["hallucination"],
              "recommended_revision_direction": "Ground the answer and narrow scope."
            }
            """
        )

        self.assertIsInstance(payload, CriticPayload)
        self.assertEqual(payload.summary, "Main issue is unsupported claim.")
        self.assertEqual(payload.key_objections, ["Unsupported claim", "Missing evidence"])

    def test_parse_critic_payload_coerces_null_lists_to_empty_lists(self):
        payload = parse_critic_payload(
            """
            {
              "summary": "The sky is green.",
              "key_objections": null,
              "confidence": 1,
              "failure_modes": null,
              "recommended_revision_direction": ""
            }
            """
        )

        self.assertEqual(payload.key_objections, [])
        self.assertEqual(payload.failure_modes, [])

    def test_parse_critic_payload_coerces_qualitative_confidence(self):
        payload = parse_critic_payload(
            """
            {
              "summary": "Usable",
              "key_objections": ["Needs tightening"],
              "confidence": "High",
              "failure_modes": [],
              "recommended_revision_direction": "Be more specific"
            }
            """
        )

        self.assertEqual(payload.confidence, 0.75)
