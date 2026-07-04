import unittest

from tinylm_glass_box_api.bench import BenchCaseResult, collect_preset_summary, load_prompts, render_markdown_report


class BenchTests(unittest.TestCase):
    def test_collect_preset_summary_aggregates_success_rate_duration_and_fallbacks(self):
        summaries = collect_preset_summary(
            [
                BenchCaseResult(
                    preset_name="a.json",
                    config_path="a.json",
                    prompt_index=0,
                    prompt="one",
                    ok=True,
                    duration_seconds=2.0,
                    run_id="1",
                    final_output="alpha",
                    final_output_chars=5,
                    fallback_count=1,
                    seat_models={"critic": ["qwen3:0.6b"]},
                    event_counts={},
                    error=None,
                ),
                BenchCaseResult(
                    preset_name="a.json",
                    config_path="a.json",
                    prompt_index=1,
                    prompt="two",
                    ok=False,
                    duration_seconds=3.0,
                    run_id=None,
                    final_output=None,
                    final_output_chars=0,
                    fallback_count=0,
                    seat_models={},
                    event_counts={},
                    error="boom",
                ),
            ]
        )

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["successful_cases"], 1)
        self.assertEqual(summaries[0]["failed_cases"], 1)
        self.assertEqual(summaries[0]["total_fallback_count"], 1)
        self.assertEqual(summaries[0]["average_duration_seconds"], 2.0)

    def test_render_markdown_report_includes_summary_and_case_details(self):
        case_result = BenchCaseResult(
            preset_name="a.json",
            config_path="a.json",
            prompt_index=0,
            prompt="one",
            ok=True,
            duration_seconds=1.5,
            run_id="1",
            final_output="final answer",
            final_output_chars=12,
            fallback_count=0,
            seat_models={"synthesizer": ["gemma3:1b"]},
            event_counts={},
            error=None,
        )
        report = render_markdown_report(
            "2026-07-04T00:00:00+00:00",
            "http://127.0.0.1:11434",
            [case_result],
            [
                {
                    "preset_name": "a.json",
                    "cases": 1,
                    "successful_cases": 1,
                    "failed_cases": 0,
                    "success_rate": 1.0,
                    "average_duration_seconds": 1.5,
                    "average_final_output_chars": 12.0,
                    "total_fallback_count": 0,
                }
            ],
        )

        self.assertIn("# TinyLM Glass Box Bench Report", report)
        self.assertIn("a.json", report)
        self.assertIn("final answer", report)

    def test_load_prompts_returns_defaults_when_no_file(self):
        prompts = load_prompts(None)

        self.assertGreaterEqual(len(prompts), 3)
