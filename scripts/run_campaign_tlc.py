#!/usr/bin/env python3
"""Run the bounded Campaign.tla model with one pinned TLC release."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


TLA2TOOLS_VERSION = "1.7.4"
TLA2TOOLS_URL = (
    "https://github.com/tlaplus/tlaplus/releases/download/"
    f"v{TLA2TOOLS_VERSION}/tla2tools.jar"
)
TLA2TOOLS_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
TLC_SUCCESS_MARKER = "Model checking completed. No error has been found."
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_REPORTED_OUTPUT_BYTES = 512 * 1024


class TlcRunnerError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_jar(path: Path) -> str:
    try:
        actual = sha256_file(path)
    except OSError as exc:
        raise TlcRunnerError(f"TLC jar is unavailable: {path}") from exc
    if actual.casefold() != TLA2TOOLS_SHA256:
        raise TlcRunnerError(
            f"TLC jar digest mismatch: expected {TLA2TOOLS_SHA256}, found {actual}"
        )
    return actual


def download_pinned_jar(destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        TLA2TOOLS_URL,
        headers={"User-Agent": "codex-coding-os-tlc-runner/1"},
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f"tla2tools-v{TLA2TOOLS_VERSION}-",
            suffix=".jar.tmp",
            dir=destination.parent,
            delete=False,
        ) as target:
            temporary_path = Path(target.name)
            total = 0
            with urllib.request.urlopen(request, timeout=60) as response:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise TlcRunnerError("TLC jar download exceeded the size limit")
                    target.write(block)
            target.flush()
            os.fsync(target.fileno())
        digest = verify_jar(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
        return digest
    except TlcRunnerError:
        raise
    except (OSError, TimeoutError) as exc:
        raise TlcRunnerError(f"failed to download pinned TLC jar: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ensure_jar(path: Path, *, download: bool) -> str:
    if path.is_file():
        try:
            return verify_jar(path)
        except TlcRunnerError:
            if not download:
                raise
    if not download:
        raise TlcRunnerError(
            "pinned TLC jar is missing; provide --jar or authorize --download"
        )
    return download_pinned_jar(path)


def _read_bounded(handle) -> str:
    handle.seek(0)
    output = handle.read(MAX_REPORTED_OUTPUT_BYTES + 1)
    if len(output) > MAX_REPORTED_OUTPUT_BYTES:
        output = output[:MAX_REPORTED_OUTPUT_BYTES]
        output += b"\n[output truncated by campaign TLC runner]\n"
    return output.decode("utf-8", errors="replace")


def run_tlc(
    jar_path: Path,
    *,
    java_executable: str = "java",
    timeout_seconds: int = 180,
) -> str:
    if timeout_seconds <= 0 or timeout_seconds > 900:
        raise TlcRunnerError("TLC timeout must be between 1 and 900 seconds")
    verify_jar(jar_path)
    repo_root = Path(__file__).resolve().parents[1]
    formal_root = repo_root / "formal"
    with tempfile.TemporaryDirectory(prefix="campaign-tlc-state-") as state_directory:
        command = (
            java_executable,
            "-cp",
            str(jar_path.resolve()),
            "tlc2.TLC",
            "-workers",
            "1",
            "-metadir",
            state_directory,
            "-config",
            "Campaign.cfg",
            "Campaign.tla",
        )
        stdout_handle = tempfile.TemporaryFile(mode="w+b")
        stderr_handle = tempfile.TemporaryFile(mode="w+b")
        try:
            try:
                completed = subprocess.run(
                    command,
                    cwd=formal_root,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TlcRunnerError(
                    f"TLC exceeded the {timeout_seconds}-second timeout"
                ) from exc
            except OSError as exc:
                raise TlcRunnerError(f"failed to start TLC: {exc}") from exc
            stdout_text = _read_bounded(stdout_handle)
            stderr_text = _read_bounded(stderr_handle)
        finally:
            stdout_handle.close()
            stderr_handle.close()
    combined = stdout_text
    if stderr_text:
        combined += f"\nTLC stderr:\n{stderr_text}"
    if completed.returncode != 0:
        raise TlcRunnerError(
            f"TLC exited with code {completed.returncode}\n{combined}".rstrip()
        )
    if TLC_SUCCESS_MARKER not in combined:
        raise TlcRunnerError(
            f"TLC did not emit its success marker\n{combined}".rstrip()
        )
    return combined


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_jar = (
        Path(tempfile.gettempdir())
        / "codex-coding-os-formal"
        / f"tla2tools-v{TLA2TOOLS_VERSION}.jar"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=Path, default=default_jar)
    parser.add_argument("--java", default="java")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        digest = ensure_jar(args.jar, download=args.download)
        output = run_tlc(
            args.jar,
            java_executable=args.java,
            timeout_seconds=args.timeout,
        )
    except TlcRunnerError as exc:
        print(f"campaign TLC check failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"verified tla2tools v{TLA2TOOLS_VERSION} sha256={digest}",
        flush=True,
    )
    print(output.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
