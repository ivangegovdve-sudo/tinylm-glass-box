from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from tinylm_glass_box_api.config import AppConfig, SeatConfig
from tinylm_glass_box_api.critic import CriticPayload, parse_critic_payload


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    final_output: str


class RunExecutionError(RuntimeError):
    def __init__(self, run_id: str, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id


class InMemoryOllamaClient:
    def __init__(self, scripted_responses: dict[tuple[str, str], str]) -> None:
        self.scripted_responses = scripted_responses
        self.requests: list[dict[str, object]] = []

    def generate(
        self,
        model_tag: str,
        seat_name: str,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
        timeout_seconds: int = 60,
        max_output_tokens: int = 256,
        temperature: float = 0.2,
    ) -> str:
        self.requests.append(
            {
                "model_tag": model_tag,
                "seat_name": seat_name,
                "prompt": prompt,
                "system_prompt": system_prompt,
                "json_mode": json_mode,
                "timeout_seconds": timeout_seconds,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            }
        )
        key = (model_tag, seat_name)
        if key not in self.scripted_responses:
            raise RuntimeError(f"Missing scripted response for {key}")
        return self.scripted_responses[key]


class RunOrchestrator:
    def __init__(self, connection: sqlite3.Connection, config: AppConfig, client: InMemoryOllamaClient) -> None:
        self.connection = connection
        self.config = config
        self.client = client

    def create_and_run(self, user_prompt: str) -> RunResult:
        run_id = self.create_run(user_prompt)
        return self.execute_run(run_id)

    def create_run(self, user_prompt: str) -> str:
        run_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO runs (run_id, user_prompt, status, current_state)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, user_prompt, "queued", "draft"),
        )
        self._append_event(run_id, "run_created", {"run_id": run_id})
        self.connection.commit()
        return run_id

    def execute_run(self, run_id: str) -> RunResult:
        row = self.connection.execute(
            "SELECT user_prompt FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Run {run_id} not found")

        user_prompt = str(row["user_prompt"])
        self._transition(run_id, "draft", "queued")
        try:
            proposer_text = self._run_text_seat(run_id, self.config.seats["proposer"], user_prompt)
            critic_payload = self._run_critic_seat(run_id, self.config.seats["critic"], user_prompt, proposer_text)
            synthesis_prompt = (
                f"User prompt:\n{user_prompt}\n\n"
                f"Proposal:\n{proposer_text}\n\n"
                f"Critique summary:\n{critic_payload.summary}\n"
                f"Revision direction:\n{critic_payload.recommended_revision_direction}"
            )
            final_output = self._run_text_seat(run_id, self.config.seats["synthesizer"], synthesis_prompt)

            self.connection.execute(
                """
                UPDATE runs
                SET status = ?, current_state = ?, final_output = ?, updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                """,
                ("completed", "completed", final_output, run_id),
            )
            self._append_event(run_id, "run_completed", {"final_output": final_output})
            self.connection.commit()
            return RunResult(run_id=run_id, status="completed", final_output=final_output)
        except Exception as exc:
            error_message = str(exc)
            self.connection.execute(
                """
                UPDATE runs
                SET status = ?, current_state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                """,
                ("failed", "failed", run_id),
            )
            self._append_event(run_id, "run_failed", {"error": error_message})
            self.connection.commit()
            raise RunExecutionError(run_id, error_message) from exc

    def _run_text_seat(self, run_id: str, seat: SeatConfig, prompt: str) -> str:
        state_name = f"{seat.seat_name}_running"
        prior_state = self._current_state(run_id)
        self._transition(run_id, prior_state, state_name)
        self._append_event(run_id, "seat_started", {"seat_name": seat.seat_name, "model_tag": seat.model_tag})

        primary_error: Exception | None = None
        for model_tag, fallback_used in ((seat.model_tag, 0), (seat.fallback_model_tag, 1)):
            try:
                response = self.client.generate(
                    model_tag,
                    seat.seat_name,
                    prompt,
                    system_prompt=seat.system_prompt,
                    json_mode=False,
                    timeout_seconds=seat.timeout_seconds,
                    max_output_tokens=seat.max_output_tokens,
                    temperature=seat.temperature,
                )
                self.connection.execute(
                    """
                    INSERT INTO seat_executions (run_id, seat_name, model_tag, output_text, fallback_used)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, seat.seat_name, model_tag, response, fallback_used),
                )
                if fallback_used:
                    self._append_event(run_id, "fallback_activated", {"seat_name": seat.seat_name})
                    self._append_event(run_id, "seat_model_switched", {"seat_name": seat.seat_name, "model_tag": model_tag})
                self._append_event(run_id, "seat_completed", {"seat_name": seat.seat_name, "text": response})
                self.connection.commit()
                return response
            except Exception as exc:
                self._append_event(
                    run_id,
                    "seat_model_error",
                    {
                        "seat_name": seat.seat_name,
                        "model_tag": model_tag,
                        "fallback_used": bool(fallback_used),
                        "error": str(exc),
                    },
                )
                if primary_error is None:
                    primary_error = exc
                if not fallback_used:
                    continue
                self._append_event(run_id, "seat_failed", {"seat_name": seat.seat_name, "error": str(primary_error)})
                raise primary_error

        raise RuntimeError(f"No response available for seat {seat.seat_name}")

    def _run_critic_seat(self, run_id: str, seat: SeatConfig, user_prompt: str, proposer_text: str) -> CriticPayload:
        state_name = f"{seat.seat_name}_running"
        prior_state = self._current_state(run_id)
        self._transition(run_id, prior_state, state_name)
        self._append_event(run_id, "seat_started", {"seat_name": seat.seat_name, "model_tag": seat.model_tag})

        prompt = f"Prompt:\n{user_prompt}\n\nProposal:\n{proposer_text}"
        primary_raw = self.client.generate(
            seat.model_tag,
            seat.seat_name,
            prompt,
            system_prompt=seat.system_prompt,
            json_mode=True,
            timeout_seconds=seat.timeout_seconds,
            max_output_tokens=seat.max_output_tokens,
            temperature=seat.temperature,
        )
        try:
            payload = parse_critic_payload(primary_raw)
            self.connection.execute(
                """
                INSERT INTO seat_executions (run_id, seat_name, model_tag, output_text, fallback_used)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, seat.seat_name, seat.model_tag, primary_raw, 0),
            )
            self._append_event(run_id, "critic_payload", payload.__dict__)
            self.connection.commit()
            return payload
        except Exception as primary_exc:
            self._append_event(
                run_id,
                "seat_model_error",
                {
                    "seat_name": seat.seat_name,
                    "model_tag": seat.model_tag,
                    "fallback_used": False,
                    "error": str(primary_exc),
                },
            )
            try:
                fallback_raw = self.client.generate(
                    seat.fallback_model_tag,
                    seat.seat_name,
                    prompt,
                    system_prompt=seat.system_prompt,
                    json_mode=True,
                    timeout_seconds=seat.timeout_seconds,
                    max_output_tokens=seat.max_output_tokens,
                    temperature=seat.temperature,
                )
                payload = parse_critic_payload(fallback_raw)
                self.connection.execute(
                    """
                    INSERT INTO seat_executions (run_id, seat_name, model_tag, output_text, fallback_used)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, seat.seat_name, seat.fallback_model_tag, fallback_raw, 1),
                )
                self._append_event(run_id, "fallback_activated", {"seat_name": seat.seat_name})
                self._append_event(run_id, "seat_model_switched", {"seat_name": seat.seat_name, "model_tag": seat.fallback_model_tag})
                self._append_event(run_id, "critic_payload", payload.__dict__)
                self.connection.commit()
                return payload
            except Exception as fallback_exc:
                self._append_event(
                    run_id,
                    "seat_model_error",
                    {
                        "seat_name": seat.seat_name,
                        "model_tag": seat.fallback_model_tag,
                        "fallback_used": True,
                        "error": str(fallback_exc),
                    },
                )
                self._append_event(run_id, "seat_failed", {"seat_name": seat.seat_name, "error": str(primary_exc)})
                raise primary_exc

    def _current_state(self, run_id: str) -> str:
        row = self.connection.execute(
            "SELECT current_state FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return str(row[0])

    def _transition(self, run_id: str, prior_state: str, subsequent_state: str) -> None:
        self.connection.execute(
            """
            INSERT INTO state_history (run_id, prior_state, subsequent_state)
            VALUES (?, ?, ?)
            """,
            (run_id, prior_state, subsequent_state),
        )
        self.connection.execute(
            """
            UPDATE runs
            SET current_state = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (subsequent_state, "running", run_id),
        )

    def _append_event(self, run_id: str, event_type: str, payload: dict) -> None:
        self.connection.execute(
            """
            INSERT INTO event_log (run_id, event_type, payload_json)
            VALUES (?, ?, ?)
            """,
            (run_id, event_type, json.dumps(payload)),
        )
