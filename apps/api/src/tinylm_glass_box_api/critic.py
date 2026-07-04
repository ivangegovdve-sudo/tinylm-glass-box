from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CriticPayload:
    summary: str
    key_objections: list[str]
    confidence: float
    failure_modes: list[str]
    recommended_revision_direction: str


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _coerce_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    normalized = str(value).strip().lower()
    qualitative_map = {
        "very low": 0.1,
        "low": 0.25,
        "medium": 0.5,
        "moderate": 0.5,
        "high": 0.75,
        "very high": 0.9,
    }
    if normalized in qualitative_map:
        return qualitative_map[normalized]

    return float(normalized)


def parse_critic_payload(raw_payload: str) -> CriticPayload:
    parsed = json.loads(raw_payload)
    return CriticPayload(
        summary=str(parsed["summary"]),
        key_objections=_coerce_string_list(parsed.get("key_objections")),
        confidence=_coerce_confidence(parsed["confidence"]),
        failure_modes=_coerce_string_list(parsed.get("failure_modes")),
        recommended_revision_direction=str(parsed.get("recommended_revision_direction", "")),
    )
