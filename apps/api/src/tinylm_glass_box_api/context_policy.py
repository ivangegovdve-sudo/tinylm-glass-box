from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventRecord:
    event_type: str
    seat_name: str
    payload: dict


def _render_event_content(event: EventRecord) -> str:
    if "text" in event.payload:
        return str(event.payload["text"])
    if "summary" in event.payload:
        return str(event.payload["summary"])
    return str(event.payload)


def build_context(
    system_prompt: str,
    user_prompt: str,
    events: list[EventRecord],
    max_recent_items: int = 2,
) -> list[dict[str, str]]:
    context: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    milestone_events = [event for event in events if event.payload.get("milestone")]
    recent_events = events[-max_recent_items:] if max_recent_items > 0 else []

    seen: set[tuple[str, str]] = set()
    for event in milestone_events + recent_events:
        key = (event.event_type, _render_event_content(event))
        if key in seen:
            continue
        seen.add(key)
        context.append(
            {
                "role": "assistant",
                "content": _render_event_content(event),
            }
        )

    return context

