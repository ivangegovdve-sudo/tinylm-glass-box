# TinyLM Glass Box

Local-first glass-box council for tiny models.

## Structure

- `apps/api` — FastAPI API, FSM orchestration, SQLite persistence
- `apps/web` — React/Vite glass-box frontend

## Local setup

1. Install `Ollama` on the Windows host.
2. Pull the seat models configured in `apps/api/config/default.seats.json`.
3. Install frontend dependencies in `apps/web` with `npm install`.
4. Start the API with `scripts/run-api.ps1`.
5. Start the frontend with `scripts/run-web.ps1`.
6. Run preset comparisons with `scripts/run-bench.ps1`.
7. If you need a side-by-side local instance, use an isolated port and database:
   - `scripts/run-api.ps1 -ConfigName ultralight-latency.seats.json -Port 8010 -DatabasePath "$env:TEMP\tinylm-glass-box\glass-box-8010.db"`
   - `scripts/run-web.ps1 -ApiBaseUrl http://127.0.0.1:8010/api`

## Runtime notes

- The backend persists its SQLite runtime database under `%TEMP%\\tinylm-glass-box\\glass-box.db` unless `TINYLM_GLASS_BOX_DB_PATH` is set.
- The local demo now defaults to `apps/api/config/diverse-public.seats.json`.
- Current local model set:
  - `smollm2:135m`
  - `qwen3:0.6b`
  - `qwen2.5:0.5b`
  - `gemma3:270m`
  - `llama3.2:1b`
  - `gemma3:1b`
- Local preset configs:
  - `diverse-public.seats.json` -> current local-demo and public-default preset
  - `default.seats.json` -> alternate tuned preset, but less stable in the latest cold bench
  - `research-baseline.seats.json` -> closest to the paper's proposer / critic / chairman split
  - `ultralight-latency.seats.json` -> lower-latency local iteration preset
  - `private-experimental.seats.json` -> private uncensored / custom tiny-model lane
- You can launch a preset explicitly with `scripts/run-api.ps1 diverse-public.seats.json`.

## Research-driven model choice

- Primary recommendation from the current research pass:
  - proposer: `smollm2:135m`
  - critic: `qwen2.5:0.5b`
  - synthesizer: `gemma3:1b`
- `qwen3:0.6b` remains the fallback critic for the public preset.
- `llama3.2:1b` remains the fallback synthesizer.
- `llama3.2:3b` has been removed from the local machine and from the council presets. It was too expensive for the local-demo target on this machine.
- `gemma3:270m` is the best extra tiny swap-in we have right now for a wider bench. It is `292 MB` in Ollama and gives a second small family besides SmolLM and Qwen.
- `smollm2:360m` remains optional but was not pulled into the default local bench because the current Ollama artifact is much heavier than the paper-level intuition suggests.

## Hugging Face expansion lane

- Public-mode candidates worth considering later:
  - `HuggingFaceTB/SmolLM2-135M-Instruct`
  - `HuggingFaceTB/SmolLM2-360M-Instruct`
  - `Qwen/Qwen3-0.6B`
  - `google/gemma-3-1b-it`
- Private / experimental candidates worth isolating in a separate branch or preset, not public mode:
  - `gigbh/Qwen3-0.6B-Instruct-Uncensored`
  - `Andycurrent/Qwen3-0.6B-heretic-abliterated-uncensored-GGUF`
  - `qualifire-oss/mcp-tool-use-quality-ranger-0.6b-GGUF`
  - `Andycurrent/Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_GGUF`
- Those experimental Hugging Face variants are useful for private-mode probing, but they are not good defaults for the public glass-box. The reliability, licensing, gating, or behavioral stability story is weaker.
- `private-experimental.seats.json` assumes you will create local Ollama aliases such as `qwen3-uncensored:0.6b` and `gemma3-experimental:1b` from imported GGUFs before using that preset.

## Current v1 behavior

- Public glass-box mode only
- Three-seat council flow
- Local-only fallback models
- SQLite trace persistence
- Async run creation with immediate `queued` responses
- Frontend polling against `/api/runs/{run_id}` and `/api/runs/{run_id}/trace`

## Bench Command

- `scripts/run-bench.ps1` runs the same prompt set against multiple seat presets and writes a JSON plus Markdown report.
- Default behavior compares:
  - `default.seats.json`
  - `research-baseline.seats.json`
  - `diverse-public.seats.json`
  - `ultralight-latency.seats.json`
- Default output goes under `%TEMP%\tinylm-glass-box\benchmarks\<timestamp>`.
- Example:
  - `scripts/run-bench.ps1 --preset default.seats.json --preset diverse-public.seats.json`
  - `scripts/run-bench.ps1 --prompts-file D:\projects\tinylm-glass-box\bench-prompts.json`
- The bench currently measures what we need for iteration:
  - success / failure per preset and prompt
  - total wall-clock duration
  - fallback count
  - actual seat model path used
  - final output size and preview
- It does not yet score answer quality automatically. That is deliberate. First we need stable execution and comparable artifacts.

## Verification used during bring-up

- Backend unit tests pass with `python -m unittest discover -s tests -v`
- `GET /api/health` returns `200`
- `POST /api/runs` on the patched local server now returns a real `run_id` immediately (`0.66s` in the latest smoke test)
- Polling `GET /api/runs/{run_id}` and `GET /api/runs/{run_id}/trace` shows live progress and terminal failure state on real Ollama runs
- Bench artifacts at `C:\Users\ivang\AppData\Local\Temp\tinylm-glass-box\benchmarks\20260704-093046` show:
  - `diverse-public.seats.json`: `3/3` success, `28.58s` average
  - `default.seats.json`: `1/3` success, `26.04s` average on successful run
  - `ultralight-latency.seats.json`: `1/3` success, `23.41s` average on successful run
