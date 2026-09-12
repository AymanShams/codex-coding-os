#!/usr/bin/env python3
"""Complete native Windows directory-read setup before the first validation.

Uses one public windowsSandbox/setupStart request and a bounded native log
completion barrier. It creates no model thread, turn, or validation command.
The previously provisioned elevated backend and exact native binary are required.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent.campaign_engine.host import AppServerTransport


def digest(data):
    return hashlib.sha256(data).hexdigest()


def log_snapshot(paths):
    result = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("rb") as handle:
            data = handle.read(1024 * 1024 + 1)
            stat = os.fstat(handle.fileno())
        if len(data) > 1024 * 1024:
            raise RuntimeError("Native setup log exceeds the bounded capture size")
        result[str(path)] = {"device": stat.st_dev, "inode": stat.st_ino, "data": data}
    return result


def new_log_text(paths, before):
    after = log_snapshot(paths)
    if not set(before).issubset(after):
        raise RuntimeError("Native setup log disappeared during setup")
    appended = []
    for path, current in after.items():
        prior = before.get(path)
        if prior is not None:
            if ((current["device"], current["inode"]) != (prior["device"], prior["inode"])
                    or not current["data"].startswith(prior["data"])):
                raise RuntimeError("Native setup log generation changed during setup")
        appended.append(current["data"][len(prior["data"]) if prior else 0:].decode("utf-8", errors="strict"))
    return "\n".join(appended)


def native_read_setup_complete(text, cwd, setup_executable):
    started = False
    reading = False
    completed = False
    normalize = lambda path: os.path.normcase(os.path.normpath(path.removeprefix("\\\\?\\")))
    for line in text.splitlines():
        spawned = re.search(r"setup refresh: spawning (.+) \(cwd=(.+), payload_len=", line)
        if spawned:
            if (started or normalize(spawned[1]) != normalize(str(setup_executable))
                    or normalize(spawned[2]) != normalize(str(cwd))):
                raise RuntimeError("Native setup log does not bind one exact source and cwd")
            started = True
        if "read ACL helper already running" in line or "read ACL run completed with errors" in line:
            raise RuntimeError("Native directory-read setup was skipped or failed")
        if "read-acl-only mode: applying read ACLs" in line:
            if not started or reading:
                raise RuntimeError("Native read helper is not bound to the setup request")
            reading = True
        if line.endswith("read ACL run completed"):
            if not reading or completed:
                raise RuntimeError("Native read completion is not bound to the setup request")
            completed = True
    return completed


def prepare(executable, cwd, expected_sha256, output, *, timeout=60):
    report = {"protocol": "ccos-windows-read-setup-v1", "status": "failed",
              "started_at_utc": datetime.now(timezone.utc).isoformat()}
    transport = None
    try:
        if sys.platform != "win32":
            raise RuntimeError("Native Windows setup requires Windows")
        executable = Path(executable).resolve(strict=True)
        cwd = Path(cwd).resolve(strict=True)
        if not cwd.is_dir() or not 0 < timeout <= 120:
            raise RuntimeError("Existing setup cwd and a deadline of at most 120 seconds are required")
        setup_executable = executable.parent.parent / "codex-resources" / "codex-windows-sandbox-setup.exe"
        sources = {str(path): digest(path.read_bytes()) for path in (executable, setup_executable)}
        if sources[str(executable)] != expected_sha256:
            raise RuntimeError("Native executable differs from the expected package identity")
        profile = Path(os.environ["USERPROFILE"]).resolve(strict=True)
        codex_home = profile / ".codex"
        if Path(os.environ.get("CODEX_HOME", str(codex_home))).resolve() != codex_home:
            raise RuntimeError("Setup requires the account's canonical Codex home")
        log_directory = codex_home / ".sandbox"
        today = datetime.now(timezone.utc).date()
        # Upstream names these files by UTC date. Include a possible midnight
        # rollover without consuming historical logs as completion evidence.
        log_paths = [log_directory / f"sandbox.{today + timedelta(days=day)}.log" for day in (0, 1)]
        report.update(cwd=str(cwd), codex_home=str(codex_home), sources=sources)
        deadline = time.monotonic() + timeout
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise RuntimeError("Native read setup did not complete within its deadline")
            return value
        transport = AppServerTransport(executable, cwd=cwd, timeout=timeout)
        transport.command[1:1] = ["-P", ":read-only", "-c", 'windows.sandbox="elevated"']
        transport.start()
        transport.request("initialize", {"clientInfo": {"name": "coding-os-windows-setup", "version": "1.0"},
                                          "capabilities": {"experimentalApi": True}}, timeout=remaining())
        transport.notify("initialized", {})
        report["backend_readiness"] = transport.request("windowsSandbox/readiness", timeout=remaining())
        if report["backend_readiness"].get("status") != "ready":
            raise RuntimeError("Provision the elevated native backend before directory-read setup")
        before = log_snapshot(log_paths)
        report["log_generation"] = {path: {"device": value["device"], "inode": value["inode"],
            "bytes": len(value["data"]), "sha256": digest(value["data"])} for path, value in before.items()}
        event_offset = len(transport.events)
        report["setup_request"] = {"mode": "elevated", "cwd": str(cwd)}
        response = transport.request("windowsSandbox/setupStart", report["setup_request"],
                                     timeout=remaining())
        if response.get("started") is not True:
            raise RuntimeError("Native setup did not acknowledge the request")
        while time.monotonic() < deadline:
            report["native_log"] = new_log_text(log_paths, before)
            complete = native_read_setup_complete(report["native_log"], cwd, setup_executable)
            notifications = [event.get("params", {}) for event in transport.events[event_offset:]
                             if event.get("method") == "windowsSandbox/setupCompleted"]
            if len(notifications) > 1:
                raise RuntimeError("Unexpected additional native setup completion")
            if notifications:
                report["setup_completed"] = notifications[0]
                if notifications[0].get("success") is not True or notifications[0].get("mode") != "elevated":
                    raise RuntimeError("Native elevated setup reported failure")
            if complete and notifications:
                if any(digest(Path(path).read_bytes()) != value for path, value in sources.items()):
                    raise RuntimeError("Native setup executable changed during setup")
                report["status"] = "ready"
                return report
            transport._pump(min(.25, remaining()))
        raise RuntimeError("Native read setup did not complete within its deadline")
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        close_failed_after_ready = False
        if transport is not None:
            try:
                report["transport_diagnostics"] = transport.diagnostic_snapshot()
            except Exception as exc:
                report["diagnostic_error"] = str(exc)
            try:
                transport.close()
            except Exception as exc:
                report["close_error"] = str(exc)
                close_failed_after_ready = report["status"] == "ready"
                report["status"] = "failed"
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if close_failed_after_ready:
            raise RuntimeError("Native setup transport did not close: " + report["close_error"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-executable", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--expected-codex-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.codex_executable, args.cwd, args.expected_codex_sha256, args.output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "receipt": str(args.output)}))
        return 1
    print(json.dumps({"status": result["status"], "receipt": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
