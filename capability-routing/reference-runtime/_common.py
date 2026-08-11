import json
import os
import re
import subprocess
import sys
from pathlib import Path


def load_input():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_stdin": raw}


def emit_json(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    raise SystemExit(0)


def no_output():
    raise SystemExit(0)


def emit_system_message(message):
    emit_json({"systemMessage": message})


def emit_additional_context(event_name, text):
    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            }
        }
    )


def emit_user_block(reason):
    emit_json({"decision": "block", "reason": reason})


def emit_stop_continue(reason):
    emit_json({"decision": "block", "reason": reason})


def emit_pre_tool_deny(reason):
    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def emit_post_tool_block(reason, context=None):
    payload = {"decision": "block", "reason": reason}
    if context:
        payload["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    emit_json(payload)


def fail_open(hook_name, exc):
    emit_system_message(f"{hook_name} failed open: {type(exc).__name__}: {exc}")


def text_value(value, max_chars=60000):
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_chars]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:max_chars]
    except TypeError:
        return str(value)[:max_chars]


def lower_text(value, max_chars=60000):
    return text_value(value, max_chars=max_chars).lower()


def get_prompt(data):
    prompt = data.get("prompt")
    if isinstance(prompt, str):
        return prompt
    return text_value(prompt)


def get_tool_name(data):
    value = data.get("tool_name")
    return value if isinstance(value, str) else ""


def get_tool_input(data):
    value = data.get("tool_input")
    return value if isinstance(value, dict) else {}


def get_tool_command(data):
    tool_input = get_tool_input(data)
    command = tool_input.get("command")
    if isinstance(command, str):
        return command
    return ""


def get_last_assistant_message(data):
    value = data.get("last_assistant_message")
    return value if isinstance(value, str) else ""


def recent_transcript_text(path, max_bytes=524288):
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return ""
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def latest_user_prompt_from_transcript(path):
    text = recent_transcript_text(path)
    if not text:
        return ""
    latest = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload", {})
        if record.get("type") == "event_msg" and payload.get("type") == "user_message":
            msg = payload.get("message")
            if isinstance(msg, str):
                latest = msg
        if record.get("type") == "response_item":
            if payload.get("type") == "message" and payload.get("role") == "user":
                parts = []
                for item in payload.get("content", []):
                    if isinstance(item, dict):
                        value = item.get("text") or item.get("input_text")
                        if isinstance(value, str):
                            parts.append(value)
                if parts:
                    latest = "\n".join(parts)
    return latest


def git_status_porcelain(cwd):
    if not cwd:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def contains_any(text, terms):
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def regex_any(text, patterns, flags=re.IGNORECASE):
    for pattern in patterns:
        if re.search(pattern, text, flags):
            return pattern
    return None


PLACEHOLDER_TERMS = (
    "example",
    "placeholder",
    "dummy",
    "redacted",
    "replace_me",
    "changeme",
    "insert",
    "your_",
    "xxxx",
    "xxxxx",
    "<",
    ">",
    "${",
)


def looks_placeholder(value):
    lowered = value.strip().strip("'\"").lower()
    if len(lowered) < 12:
        return True
    return any(term in lowered for term in PLACEHOLDER_TERMS)


SECRET_PATTERNS = [
    ("OpenAI API key", re.compile(r"\bsk-(?:proj|svcacct|admin)?-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    (
        "private key block",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    ),
    (
        "database URL with password",
        re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^/\s:@]+:[^@\s]{8,}@", re.IGNORECASE),
    ),
]


GENERIC_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY|CLIENT_SECRET|DATABASE_URL)[A-Z0-9_]*)\s*[:=]\s*[\"']?([^\"'\s#]{12,})"
)


def detect_secrets(text):
    findings = []
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(label)
    for match in GENERIC_SECRET_ASSIGNMENT.finditer(text):
        key_name = match.group(1)
        value = match.group(2)
        if not looks_placeholder(value):
            findings.append(f"secret assignment: {key_name}")
    seen = set()
    deduped = []
    for item in findings:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped
