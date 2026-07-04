from __future__ import annotations

import json
from urllib import error, request


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = base_url.rstrip("/")

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
        payload = {
            "model": model_tag,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_output_tokens,
                "temperature": temperature,
            },
        }
        if system_prompt:
            payload["system"] = system_prompt
        if json_mode:
            payload["format"] = "json"
        http_request = request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {exc.code} for {seat_name}/{model_tag}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Ollama unavailable for {seat_name}/{model_tag}: {exc.reason}") from exc

        if "response" not in body:
            raise RuntimeError(f"Ollama returned no response field for {seat_name}/{model_tag}")
        return str(body["response"])
