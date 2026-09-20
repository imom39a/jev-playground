"""Small HTTP clients for the two provider paths used by the demos.

Access values are read in this process only.  Provider replies are reduced to a
closed decision and bounded telemetry before they reach the session or UI.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "openai/gpt-oss-20b"
DEFAULT_JEV_MODEL = "jev-latest"
MAX_RESPONSE_BYTES = 1_000_000
OPENROUTER_TIMEOUT = 30.0
JEV_TIMEOUT = 20.0


class ProviderError(Exception):
    """A bounded provider failure."""


def load_dotenv(root: Path) -> None:
    """Load the existing repository file without printing or copying secrets."""
    try:
        lines = (root / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name, value = name.strip(), value.strip().strip("'\"")
        if name and value and name not in os.environ:
            os.environ[name] = value
        if name == "openouterkey" and value and "OPENROUTER_API_KEY" not in os.environ:
            os.environ["OPENROUTER_API_KEY"] = value


def openrouter_key() -> str | None:
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "openouterkey"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def jev_key() -> str | None:
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def _http_json(url: str, body: dict[str, object], headers: dict[str, str], timeout: float) -> dict[str, object]:
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Accept": "application/json", "Content-Type": "application/json", **headers},
        method="POST",
    )
    outcome: dict[str, object] = {}

    def call() -> None:
        try:
            with urllib.request.urlopen(request, timeout=max(1.0, timeout)) as response:
                outcome["raw"] = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            outcome["http"] = error.code
        except (OSError, TimeoutError):
            outcome["failed"] = True

    worker = threading.Thread(target=call, daemon=True, name="hanoi-provider-call")
    worker.start()
    worker.join(max(1.0, timeout))
    if worker.is_alive():
        raise ProviderError("provider_request_timeout")
    if "http" in outcome:
        raise ProviderError(f"provider_request_failed_{outcome['http']}")
    if outcome.get("failed"):
        raise ProviderError("provider_request_failed")
    raw = outcome.get("raw")
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("provider_response_oversize")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProviderError("provider_response_invalid") from error
    if not isinstance(value, dict):
        raise ProviderError("provider_response_invalid")
    return value


def _bounded_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return max(0.0, min(1.0, float(value)))


def _text(value: object, limit: int = 240) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    lowered = value.lower()
    if any(secret in lowered for secret in ("bearer", "api_key", "authorization", "token")):
        return None
    return value[:limit]


def _choice_content(response: dict[str, object]) -> dict[str, object]:
    choices = response.get("choices")
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str))
    if not isinstance(content, str):
        raise ProviderError("provider_choice_missing")
    try:
        decision = json.loads(content)
    except json.JSONDecodeError as error:
        raise ProviderError("provider_decision_invalid") from error
    if not isinstance(decision, dict):
        raise ProviderError("provider_decision_invalid")
    return decision


def openrouter_choose(
    state: dict[str, object],
    candidates: dict[str, str],
    model: str,
    key: str | None = None,
    timeout: float = OPENROUTER_TIMEOUT,
    feedback: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Ask the selected model for one label from the closed candidate set."""
    key = key or openrouter_key()
    if not key:
        raise ProviderError("provider_key_missing")
    guidance = (
        "You are solving a bounded Tower of Hanoi session. Choose exactly one label from "
        "available_choices. Legal work and completion claims are participant actions; a solved "
        "board still needs an explicit claim. Use cycle_hint and recent_history to avoid loops. "
        "Return JSON with label, confidence, rationale, and optional reason."
    )
    if feedback:
        guidance += f" A verifier rejected the previous proposal: {feedback}. Repair it once."
    response = _http_json(
        OPENROUTER_URL,
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return only one JSON decision object."},
                {"role": "user", "content": guidance + "\n\n" + json.dumps({"state": state, "available_choices": candidates}, separators=(",", ":"))},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        {"Authorization": f"Bearer {key}"},
        min(max(1.0, timeout), OPENROUTER_TIMEOUT),
    )
    decision = _choice_content(response)
    label = decision.get("label", decision.get("action"))
    if not isinstance(label, str) or label not in candidates:
        raise ProviderError("provider_choice_not_offered")
    metadata: dict[str, object] = {
        "provider_model": response.get("model") if isinstance(response.get("model"), str) else model,
        "confidence": _bounded_number(decision.get("confidence")),
        "rationale": _text(decision.get("rationale")),
        "reason": _text(decision.get("reason"), 64),
    }
    return label, metadata


def jev_choose(
    state: dict[str, object],
    candidates: dict[str, str],
    model: str = DEFAULT_JEV_MODEL,
    key: str | None = None,
    timeout: float = JEV_TIMEOUT,
) -> tuple[str, dict[str, object]]:
    """Ask System One to choose one closed candidate label."""
    key = key or jev_key()
    if not key:
        raise ProviderError("provider_key_missing")
    response = _http_json(
        JEV_URL,
        {
            "state": state,
            "model": model,
            "questions": {
                "choice": {
                    "type": "choice",
                    "instructions": "Choose the single best next action from the offered labels. Never invent a label.",
                    "criteria": candidates,
                }
            },
        },
        {"Authorization": f"Bearer {key}"},
        min(max(1.0, timeout), JEV_TIMEOUT),
    )
    answers = response.get("answers")
    answer = answers.get("choice") if isinstance(answers, dict) else None
    label = answer.get("choice") if isinstance(answer, dict) else None
    if not isinstance(label, str) or label not in candidates:
        raise ProviderError("provider_choice_not_offered")
    return label, {
        "provider_model": response.get("model") if isinstance(response.get("model"), str) else model,
        "confidence": _bounded_number(answer.get("confidence")) if isinstance(answer, dict) else None,
        "probabilities": answer.get("probabilities") if isinstance(answer, dict) and isinstance(answer.get("probabilities"), dict) else None,
    }


def jev_score_proposal(
    state: dict[str, object],
    candidates: dict[str, str],
    proposed: str,
    model: str = DEFAULT_JEV_MODEL,
    key: str | None = None,
    threshold: float = 0.5,
    timeout: float = JEV_TIMEOUT,
) -> tuple[bool, float, dict[str, object]]:
    """Score one proposed label; this function never selects a move."""
    key = key or jev_key()
    if not key:
        raise ProviderError("provider_key_missing")
    if proposed not in candidates:
        raise ProviderError("provider_choice_not_offered")
    judged = dict(state)
    judged["proposed_action"] = proposed
    judged["proposed_description"] = candidates[proposed]
    response = _http_json(
        JEV_URL,
        {
            "state": judged,
            "model": model,
            "questions": {
                "judge": {
                    "type": "noul",
                    "instructions": "Would you endorse this proposed action as a sound next step for the objective? Score the proposal only; do not choose a different action.",
                }
            },
        },
        {"Authorization": f"Bearer {key}"},
        min(max(1.0, timeout), JEV_TIMEOUT),
    )
    answers = response.get("answers")
    answer = answers.get("judge") if isinstance(answers, dict) else None
    probability: float | None = None
    if isinstance(answer, dict):
        probability = _bounded_number(answer.get("noul"))
        if probability is None:
            probability = _bounded_number(answer.get("probability"))
    if probability is None:
        raise ProviderError("provider_judge_missing")
    metadata = {
        "engine": "jev",
        "model": response.get("model") if isinstance(response.get("model"), str) else model,
        "proposed": proposed,
        "probability": probability,
        "threshold": max(0.0, min(1.0, threshold)),
        "approved": probability >= threshold,
    }
    return bool(metadata["approved"]), probability, metadata


def model_catalog() -> dict[str, list[dict[str, str]]]:
    """Return a stable catalog without requiring a network call at page load."""
    return {
        "openrouter": [
            {"id": "openai/gpt-oss-20b", "label": "GPT OSS 20B"},
            {"id": "mistralai/mistral-small-24b-instruct-2501", "label": "Mistral Small"},
            {"id": "meta-llama/llama-3.1-8b-instruct", "label": "Llama 3.1 8B"},
        ],
        "jev": [{"id": DEFAULT_JEV_MODEL, "label": "JEV latest"}],
    }


def sanitize_metadata(value: object) -> object:
    """Recursively retain small JSON telemetry and discard secret-like fields."""
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in {"key", "token", "authorization", "headers", "prompt", "response"}:
                continue
            clean = sanitize_metadata(item)
            if clean is not None:
                result[key[:64]] = clean
        return result
    if isinstance(value, list):
        return [item for item in (sanitize_metadata(item) for item in value[:16]) if item is not None]
    if isinstance(value, str):
        return _text(value, 256)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return None
