from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
import tempfile

from tinylm_glass_box_api.config import load_config
from tinylm_glass_box_api.db import connect_db, initialize_db
from tinylm_glass_box_api.ollama_client import OllamaClient
from tinylm_glass_box_api.orchestrator import RunExecutionError, RunOrchestrator


DEFAULT_PRESET_FILES = [
    "default.seats.json",
    "research-baseline.seats.json",
    "diverse-public.seats.json",
    "ultralight-latency.seats.json",
]

DEFAULT_PROMPTS = [
    "What does it mean for intelligence to be a property of a network rather than a node?",
    "Argue whether a tiny model council is a serious reasoning artifact or mostly theater.",
    "Design a public-mode versus private-mode policy for a glass-box tiny model demo.",
]


@dataclass(frozen=True)
class BenchCaseResult:
    preset_name: str
    config_path: str
    prompt_index: int
    prompt: str
    ok: bool
    duration_seconds: float
    run_id: str | None
    final_output: str | None
    final_output_chars: int
    fallback_count: int
    seat_models: dict[str, list[str]]
    event_counts: dict[str, int]
    error: str | None


def _slugify(value: str) -> str:
    characters = []
    for char in value.lower():
        if char.isalnum():
            characters.append(char)
        else:
            characters.append("-")
    slug = "".join(characters).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "case"


def load_prompts(prompts_file: Path | None) -> list[str]:
    if prompts_file is None:
        return DEFAULT_PROMPTS

    raw = json.loads(prompts_file.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError("Prompt file must be a JSON array of non-empty strings.")
    return [item.strip() for item in raw]


def collect_preset_summary(case_results: list[BenchCaseResult]) -> list[dict[str, object]]:
    grouped: dict[str, list[BenchCaseResult]] = {}
    for case_result in case_results:
        grouped.setdefault(case_result.preset_name, []).append(case_result)

    summaries: list[dict[str, object]] = []
    for preset_name, cases in grouped.items():
        successful_cases = [case for case in cases if case.ok]
        average_duration = (
            sum(case.duration_seconds for case in successful_cases) / len(successful_cases)
            if successful_cases
            else None
        )
        average_output_chars = (
            sum(case.final_output_chars for case in successful_cases) / len(successful_cases)
            if successful_cases
            else None
        )
        summaries.append(
            {
                "preset_name": preset_name,
                "cases": len(cases),
                "successful_cases": len(successful_cases),
                "failed_cases": len(cases) - len(successful_cases),
                "success_rate": len(successful_cases) / len(cases),
                "average_duration_seconds": average_duration,
                "average_final_output_chars": average_output_chars,
                "total_fallback_count": sum(case.fallback_count for case in cases),
            }
        )

    summaries.sort(key=lambda item: (-float(item["success_rate"]), float(item["average_duration_seconds"] or 10**9)))
    return summaries


def render_markdown_report(
    suite_started_at: str,
    base_url: str,
    case_results: list[BenchCaseResult],
    preset_summaries: list[dict[str, object]],
) -> str:
    lines = [
        "# TinyLM Glass Box Bench Report",
        "",
        f"- Started at: `{suite_started_at}`",
        f"- Ollama base URL: `{base_url}`",
        f"- Cases: `{len(case_results)}`",
        "",
        "## Preset Summary",
        "",
        "| Preset | Success | Avg Duration (s) | Avg Output Chars | Fallbacks |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]

    for summary in preset_summaries:
        average_duration = summary["average_duration_seconds"]
        average_output_chars = summary["average_final_output_chars"]
        average_duration_text = f"{average_duration:.2f}" if isinstance(average_duration, float) else "n/a"
        average_output_chars_text = f"{average_output_chars:.0f}" if isinstance(average_output_chars, float) else "n/a"
        lines.append(
            "| "
            f"{summary['preset_name']} | "
            f"{summary['successful_cases']}/{summary['cases']} | "
            f"{average_duration_text} | "
            f"{average_output_chars_text} | "
            f"{summary['total_fallback_count']} |"
        )

    lines.extend(
        [
            "",
            "## Case Details",
            "",
        ]
    )

    for case in case_results:
        lines.extend(
            [
                f"### {case.preset_name} / prompt {case.prompt_index + 1}",
                "",
                f"- Status: `{'ok' if case.ok else 'failed'}`",
                f"- Duration: `{case.duration_seconds:.2f}s`",
                f"- Fallback count: `{case.fallback_count}`",
                f"- Seat models: `{json.dumps(case.seat_models, ensure_ascii=True)}`",
                f"- Prompt: `{case.prompt}`",
            ]
        )
        if case.ok:
            preview = (case.final_output or "").replace("\n", " ").strip()
            if len(preview) > 240:
                preview = preview[:237] + "..."
            lines.append(f"- Output preview: `{preview}`")
        else:
            lines.append(f"- Error: `{case.error}`")
        lines.append("")

    return "\n".join(lines)


def run_case(
    config_path: Path,
    preset_name: str,
    prompt: str,
    prompt_index: int,
    output_dir: Path,
    base_url: str,
) -> BenchCaseResult:
    db_path = output_dir / f"{_slugify(preset_name)}-prompt-{prompt_index + 1}.db"
    connection = connect_db(db_path)
    initialize_db(connection)
    config = load_config(config_path, db_path)
    client = OllamaClient(base_url)
    orchestrator = RunOrchestrator(connection, config, client)

    def build_partial_snapshot(run_id: str | None, error: str) -> BenchCaseResult:
        seat_models: dict[str, list[str]] = {}
        fallback_count = 0
        event_counts: dict[str, int] = {}
        if run_id is not None:
            seat_rows = connection.execute(
                """
                SELECT seat_name, model_tag, fallback_used
                FROM seat_executions
                WHERE run_id = ?
                ORDER BY execution_id
                """,
                (run_id,),
            ).fetchall()
            for row in seat_rows:
                seat_models.setdefault(str(row["seat_name"]), []).append(str(row["model_tag"]))
                fallback_count += int(row["fallback_used"])

            event_rows = connection.execute(
                """
                SELECT event_type, COUNT(*) AS event_count
                FROM event_log
                WHERE run_id = ?
                GROUP BY event_type
                """,
                (run_id,),
            ).fetchall()
            event_counts = {str(row["event_type"]): int(row["event_count"]) for row in event_rows}

        return BenchCaseResult(
            preset_name=preset_name,
            config_path=str(config_path),
            prompt_index=prompt_index,
            prompt=prompt,
            ok=False,
            duration_seconds=perf_counter() - start,
            run_id=run_id,
            final_output=None,
            final_output_chars=0,
            fallback_count=fallback_count,
            seat_models=seat_models,
            event_counts=event_counts,
            error=error,
        )

    start = perf_counter()
    try:
        result = orchestrator.create_and_run(prompt)
        duration_seconds = perf_counter() - start
        seat_rows = connection.execute(
            """
            SELECT seat_name, model_tag, fallback_used
            FROM seat_executions
            WHERE run_id = ?
            ORDER BY execution_id
            """,
            (result.run_id,),
        ).fetchall()
        seat_models: dict[str, list[str]] = {}
        fallback_count = 0
        for row in seat_rows:
            seat_models.setdefault(str(row["seat_name"]), []).append(str(row["model_tag"]))
            fallback_count += int(row["fallback_used"])

        return BenchCaseResult(
            preset_name=preset_name,
            config_path=str(config_path),
            prompt_index=prompt_index,
            prompt=prompt,
            ok=True,
            duration_seconds=duration_seconds,
            run_id=result.run_id,
            final_output=result.final_output,
            final_output_chars=len(result.final_output),
            fallback_count=fallback_count,
            seat_models=seat_models,
            event_counts={},
            error=None,
        )
    except RunExecutionError as exc:
        return build_partial_snapshot(exc.run_id, str(exc))
    except Exception as exc:
        return build_partial_snapshot(None, str(exc))
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TinyLM Glass Box bench comparisons.")
    parser.add_argument(
        "--config-dir",
        default=str(Path(__file__).resolve().parents[2] / "config"),
        help="Directory containing seat config JSON files.",
    )
    parser.add_argument(
        "--preset",
        action="append",
        dest="presets",
        help="Preset filename inside the config dir. Repeat to compare multiple presets.",
    )
    parser.add_argument(
        "--prompts-file",
        help="Optional JSON file containing an array of prompt strings.",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional output directory for bench artifacts.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:11434",
        help="Ollama base URL.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    presets = args.presets or DEFAULT_PRESET_FILES
    prompts = load_prompts(Path(args.prompts_file) if args.prompts_file else None)
    suite_started_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    timestamp_slug = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(tempfile.gettempdir()) / "tinylm-glass-box" / "benchmarks" / timestamp_slug
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    case_results: list[BenchCaseResult] = []
    for preset_name in presets:
        config_path = config_dir / preset_name
        if not config_path.exists():
            raise FileNotFoundError(f"Preset config not found: {config_path}")
        for prompt_index, prompt in enumerate(prompts):
            case_results.append(
                run_case(
                    config_path=config_path,
                    preset_name=preset_name,
                    prompt=prompt,
                    prompt_index=prompt_index,
                    output_dir=output_dir,
                    base_url=args.base_url,
                )
            )

    preset_summaries = collect_preset_summary(case_results)
    report_payload = {
        "suite_started_at": suite_started_at,
        "base_url": args.base_url,
        "prompts": prompts,
        "presets": presets,
        "preset_summaries": preset_summaries,
        "cases": [asdict(case_result) for case_result in case_results],
    }
    (output_dir / "bench-report.json").write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    (output_dir / "bench-report.md").write_text(
        render_markdown_report(suite_started_at, args.base_url, case_results, preset_summaries),
        encoding="utf-8",
    )

    print(f"Bench complete. Artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
