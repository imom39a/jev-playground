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


def _resolve_openrouter_decision(
    decision: dict[str, object], state: dict[str, object], candidates: dict[str, str]
) -> str:
    """Resolve either a closed label or a canonical action/payload reply."""
    based_on = decision.get("based_on_session_seq")
    if based_on is not None and based_on != state.get("session_seq"):
        raise ProviderError("provider_decision_stale")
    label = decision.get("label")
    if isinstance(label, str) and label in candidates:
        return label
    action = decision.get("action")
    if isinstance(action, str) and action in candidates:
        return action
    payload = decision.get("payload")
    offered = state.get("available_candidates")
    if isinstance(action, str) and isinstance(payload, dict) and isinstance(offered, list):
        matches = [
            item for item in offered
            if isinstance(item, dict)
            and item.get("action_type") == action
            and item.get("payload") == payload
            and isinstance(item.get("label"), str)
            and item.get("label") in candidates
        ]
        if len(matches) == 1:
            return str(matches[0]["label"])
    raise ProviderError("provider_choice_not_offered")


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
        "You are the autonomous participant solving this Tower of Hanoi session. Treat the "
        "supplied board and session_seq as authoritative. The objective is to move the full "
        "tower to rod C under the stated rules. Choose exactly one offered label. Use "
        "recent_moves, recent_own_decisions, cycle_hint, and choice_history: avoid an immediate "
        "reverse or a successor board already seen when another legal move can make progress. "
        "A repeated directed move can be necessary from a different board; if its successor is "
        "unseen and it does not undo the last move, do not reject it merely because the label "
        "appeared recently. The session never declares success from the board alone. If "
        "target_reached is true and no claim is open, choose post_completion_claim. If a claim is "
        "open, assess it from the visible board. Do not claim completion while target_reached is "
        "false. Return JSON with label, based_on_session_seq, confidence, rationale, and optional "
        "reason. You may instead return the matching canonical action and payload."
    )
    if feedback:
        guidance += (
            f" A verifier rejected the previous proposal: {feedback} "
            "You must choose a different offered label for the repair."
        )
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
            "provider": {"sort": "latency"},
        },
        {"Authorization": f"Bearer {key}"},
        min(max(1.0, timeout), OPENROUTER_TIMEOUT),
    )
    decision = _choice_content(response)
    label = _resolve_openrouter_decision(decision, state, candidates)
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
                    "instructions": (
                        "Choose the single next action that best advances the Tower of Hanoi "
                        "objective. Use the authoritative current board, recent_moves, "
                        "recent_own_decisions, cycle_hint, and choice_history. choice_history "
                        "marks successor boards already seen, recently repeated moves, and moves "
                        "that reverse the most recent move. Avoid reversals and visited successor "
                        "boards first. A repeated move label can be necessary from a different "
                        "board, so do not reject it when its successor is unseen and it does not "
                        "undo the last move. If target_reached is true and no claim is open, choose "
                        "post_completion_claim. If a claim is open, assess it from the visible "
                        "board. Never claim completion while target_reached is false. Choose only "
                        "one offered label and never invent a label."
                    ),
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
                    "instructions": (
                        "Is proposed_action an admissible, non-regressive next action for the Tower "
                        "of Hanoi objective? Verify it rather than requiring it to be the uniquely "
                        "best move. Use the current board, available choices, recent_moves, "
                        "cycle_hint, and choice_history. A useful setup move may temporarily move a "
                        "small disk away from target rod C so a blocked larger disk can move; do not "
                        "reject that fact alone. A repeated move label from a different board is "
                        "also not a cycle when its successor board is unseen. Answer false when "
                        "concrete evidence shows that the "
                        "proposal immediately reverses the last move, returns to a board already "
                        "seen, repeats a recent move without progress, claims completion while "
                        "target_reached is false, or moves a disk off the completed target tower. "
                        "Otherwise endorse a legal, unseen, non-reversing proposal. Answer true for "
                        "post_completion_claim when target_reached is true and no claim is open. "
                        "Score this proposal only; do not select a different action."
                    ),
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
