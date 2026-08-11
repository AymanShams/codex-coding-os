#!/usr/bin/env python3
"""Small JSON IO helpers for optional Codex hooks."""

from __future__ import annotations

import json
import sys


def load_input() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_stdin": raw}
    return value if isinstance(value, dict) else {"value": value}


def emit_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    raise SystemExit(0)


def no_output() -> None:
    raise SystemExit(0)


def emit_additional_context(event_name: str, text: str) -> None:
    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            }
        }
    )


def emit_system_message(message: str) -> None:
    emit_json({"systemMessage": message})


def fail_open(hook_name: str, exc: BaseException) -> None:
    emit_system_message(f"{hook_name} failed open: {type(exc).__name__}: {exc}")


def text_value(value: object, max_chars: int = 60000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_chars]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:max_chars]
    except TypeError:
        return str(value)[:max_chars]


def get_prompt(data: dict) -> str:
    prompt = data.get("prompt")
    if isinstance(prompt, str):
        return prompt
    return text_value(prompt)
