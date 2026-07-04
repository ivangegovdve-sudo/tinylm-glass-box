from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tinylm_glass_box_api.config import load_config
from tinylm_glass_box_api.db import connect_db, initialize_db
from tinylm_glass_box_api.ollama_client import OllamaClient
from tinylm_glass_box_api.orchestrator import RunOrchestrator


class RunCreateRequest(BaseModel):
    prompt: str


def create_app(database_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="TinyLM Glass Box API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api_root = Path(__file__).resolve().parents[2]
    runtime_data_root = Path(tempfile.gettempdir()) / "tinylm-glass-box"
    resolved_database_path = database_path or Path(
        os.environ.get("TINYLM_GLASS_BOX_DB_PATH", runtime_data_root / "glass-box.db")
    )
    config_path = Path(
        os.environ.get("TINYLM_GLASS_BOX_CONFIG", api_root / "config" / "diverse-public.seats.json")
    )
    connection = connect_db(resolved_database_path)
    initialize_db(connection)
    config = load_config(config_path, resolved_database_path)
    client = OllamaClient(os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    orchestrator = RunOrchestrator(connection, config, client)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/runs")
    def create_run(request: RunCreateRequest) -> dict[str, str]:
        run_id = orchestrator.create_run(request.prompt)

        def worker() -> None:
            try:
                orchestrator.execute_run(run_id)
            except Exception:
                # Failure state is already persisted by the orchestrator.
                return

        threading.Thread(target=worker, daemon=True, name=f"tinylm-run-{run_id[:8]}").start()
        return {
            "run_id": run_id,
            "status": "queued",
            "run_url": f"/runs/{run_id}",
            "events_url": f"/api/runs/{run_id}/events",
        }

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        row = connection.execute(
            "SELECT run_id, user_prompt, status, current_state, final_output FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return dict(row)

    @app.get("/api/runs/{run_id}/trace")
    def get_trace(run_id: str) -> dict[str, list[dict]]:
        rows = connection.execute(
            "SELECT event_type, payload_json, created_at FROM event_log WHERE run_id = ? ORDER BY event_id",
            (run_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "events": [
                {"event_type": row["event_type"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
                for row in rows
            ]
        }

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str) -> StreamingResponse:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM event_log WHERE run_id = ? ORDER BY event_id",
            (run_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Run not found")

        def iterator():
            for row in rows:
                event_type = row["event_type"]
                payload = row["payload_json"]
                yield f"event: {event_type}\ndata: {payload}\n\n"

        return StreamingResponse(iterator(), media_type="text/event-stream")

    return app


app = create_app()
