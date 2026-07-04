from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class SeatConfig:
    seat_name: str
    model_tag: str
    fallback_model_tag: str
    system_prompt: str
    timeout_seconds: int
    max_retries: int
    max_output_tokens: int = 256
    temperature: float = 0.2


@dataclass(frozen=True)
class AppConfig:
    database_path: Path
    max_concurrent_runs: int
    seats: dict[str, SeatConfig]


def load_config(config_path: Path, database_path: Path) -> AppConfig:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    seats = {
        seat_name: SeatConfig(**seat_config)
        for seat_name, seat_config in raw["seats"].items()
    }
    return AppConfig(
        database_path=database_path,
        max_concurrent_runs=int(raw["max_concurrent_runs"]),
        seats=seats,
    )
