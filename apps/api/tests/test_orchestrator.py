from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tinylm_glass_box_api.config import AppConfig, SeatConfig
from tinylm_glass_box_api.db import connect_db, initialize_db
from tinylm_glass_box_api.orchestrator import InMemoryOllamaClient, RunExecutionError, RunOrchestrator


def _build_config() -> AppConfig:
    return AppConfig(
        database_path=Path("unused.db"),
        max_concurrent_runs=1,
        seats={
            "proposer": SeatConfig(
                seat_name="proposer",
                model_tag="smollm2:135m",
                fallback_model_tag="qwen2.5:0.5b",
                system_prompt="Propose an answer.",
                timeout_seconds=10,
                max_retries=1,
            ),
            "critic": SeatConfig(
                seat_name="critic",
                model_tag="qwen2.5:0.5b",
                fallback_model_tag="llama3.2:1b",
                system_prompt="Return JSON critique.",
                timeout_seconds=10,
                max_retries=1,
            ),
            "synthesizer": SeatConfig(
                seat_name="synthesizer",
                model_tag="llama3.2:1b",
                fallback_model_tag="llama3.2:3b",
                system_prompt="Synthesize the result.",
                timeout_seconds=10,
                max_retries=1,
            ),
        },
    )


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_can_create_then_execute_run(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "queued.db"
            connection = connect_db(db_path)
            initialize_db(connection)

            client = InMemoryOllamaClient(
                scripted_responses={
                    ("smollm2:135m", "proposer"): "Initial answer",
                    ("qwen2.5:0.5b", "critic"): """
                    {
                      "summary": "Need more nuance.",
                      "key_objections": ["Too broad"],
                      "confidence": 0.55,
                      "failure_modes": ["overgeneralization"],
                      "recommended_revision_direction": "Narrow the claim."
                    }
                    """,
                    ("llama3.2:1b", "synthesizer"): "Final synthesis",
                }
            )

            orchestrator = RunOrchestrator(connection, _build_config(), client)
            run_id = orchestrator.create_run("Why use three tiny models?")
            queued_row = connection.execute(
                "SELECT status, current_state FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            result = orchestrator.execute_run(run_id)

            self.assertEqual(tuple(queued_row), ("queued", "draft"))
            self.assertEqual(result.run_id, run_id)
            self.assertEqual(result.status, "completed")
            connection.close()

    def test_orchestrator_runs_three_seat_flow_and_persists_final_state(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "orchestrator.db"
            connection = connect_db(db_path)
            initialize_db(connection)

            client = InMemoryOllamaClient(
                scripted_responses={
                    ("smollm2:135m", "proposer"): "Initial answer",
                    ("qwen2.5:0.5b", "critic"): """
                    {
                      "summary": "Need more nuance.",
                      "key_objections": ["Too broad"],
                      "confidence": 0.55,
                      "failure_modes": ["overgeneralization"],
                      "recommended_revision_direction": "Narrow the claim."
                    }
                    """,
                    ("llama3.2:1b", "synthesizer"): "Final synthesis",
                }
            )

            orchestrator = RunOrchestrator(connection, _build_config(), client)
            result = orchestrator.create_and_run("Why use three tiny models?")

            run_row = connection.execute(
                "SELECT status, final_output FROM runs WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()
            seat_count = connection.execute(
                "SELECT COUNT(*) FROM seat_executions WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()[0]

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.final_output, "Final synthesis")
            self.assertEqual(tuple(run_row), ("completed", "Final synthesis"))
            self.assertEqual(seat_count, 3)
            connection.close()

    def test_orchestrator_uses_fallback_when_primary_critic_output_is_invalid(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fallback.db"
            connection = connect_db(db_path)
            initialize_db(connection)

            client = InMemoryOllamaClient(
                scripted_responses={
                    ("smollm2:135m", "proposer"): "Initial answer",
                    ("qwen2.5:0.5b", "critic"): "not-json",
                    ("llama3.2:1b", "critic"): """
                    {
                      "summary": "Fallback critique.",
                      "key_objections": ["Primary critic broke schema"],
                      "confidence": 0.8,
                      "failure_modes": ["schema_error"],
                      "recommended_revision_direction": "Use the fallback critique."
                    }
                    """,
                    ("llama3.2:1b", "synthesizer"): "Final synthesis after fallback",
                }
            )

            orchestrator = RunOrchestrator(connection, _build_config(), client)
            result = orchestrator.create_and_run("What happens on fallback?")

            fallback_rows = connection.execute(
                "SELECT seat_name, model_tag, fallback_used FROM seat_executions WHERE run_id = ? ORDER BY execution_id",
                (result.run_id,),
            ).fetchall()
            fallback_events = connection.execute(
                "SELECT event_type FROM event_log WHERE run_id = ?",
                (result.run_id,),
            ).fetchall()

            self.assertEqual(result.status, "completed")
            self.assertIn(("critic", "llama3.2:1b", 1), [tuple(row) for row in fallback_rows])
            self.assertIn(("fallback_activated",), [tuple(row) for row in fallback_events])
            connection.close()

    def test_orchestrator_sends_system_prompt_and_json_mode_for_critic(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "transport.db"
            connection = connect_db(db_path)
            initialize_db(connection)

            client = InMemoryOllamaClient(
                scripted_responses={
                    ("smollm2:135m", "proposer"): "Initial answer",
                    ("qwen2.5:0.5b", "critic"): """
                    {
                      "summary": "Need more nuance.",
                      "key_objections": ["Too broad"],
                      "confidence": 0.55,
                      "failure_modes": ["overgeneralization"],
                      "recommended_revision_direction": "Narrow the claim."
                    }
                    """,
                    ("llama3.2:1b", "synthesizer"): "Final synthesis",
                }
            )

            orchestrator = RunOrchestrator(connection, _build_config(), client)
            orchestrator.create_and_run("Why use three tiny models?")

            critic_request = next(
                request for request in client.requests if request["seat_name"] == "critic"
            )

            self.assertTrue(critic_request["json_mode"])
            self.assertIn("Return JSON critique.", critic_request["system_prompt"])
            self.assertEqual(critic_request["timeout_seconds"], 10)
            connection.close()

    def test_orchestrator_marks_run_failed_and_persists_failure_events(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "failed.db"
            connection = connect_db(db_path)
            initialize_db(connection)

            client = InMemoryOllamaClient(
                scripted_responses={
                    ("smollm2:135m", "proposer"): "Initial answer",
                    ("qwen2.5:0.5b", "critic"): "not-json",
                    ("llama3.2:1b", "critic"): "still-not-json",
                }
            )

            orchestrator = RunOrchestrator(connection, _build_config(), client)

            with self.assertRaises(RunExecutionError) as raised:
                orchestrator.create_and_run("Force a critic failure")

            run_row = connection.execute(
                "SELECT status, current_state FROM runs WHERE run_id = ?",
                (raised.exception.run_id,),
            ).fetchone()
            event_types = connection.execute(
                "SELECT event_type FROM event_log WHERE run_id = ? ORDER BY event_id",
                (raised.exception.run_id,),
            ).fetchall()

            self.assertEqual(tuple(run_row), ("failed", "failed"))
            self.assertIn(("seat_failed",), [tuple(row) for row in event_types])
            self.assertIn(("run_failed",), [tuple(row) for row in event_types])
            connection.close()
