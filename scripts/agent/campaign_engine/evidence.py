"""Trusted execution and exact-head evidence collection.

The validator runs argument arrays directly.  It never invokes a shell, never
credits assertion text over a non-zero process exit, and rejects a candidate
whose head changes while evidence is being collected.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .admission import AdmissionError, normalize_remote_url, run_git


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_ENV_ALLOWLIST = frozenset(
    {
        "CI",
        "COLORTERM",
        "COMSPEC",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NODE_OPTIONS",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PNPM_HOME",
        "POWERSHELL_DISTRIBUTION_CHANNEL",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PYTHONIOENCODING",
        "PYTHONPATH",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


class EvidenceError(RuntimeError):
    pass


class ValidationFailure(EvidenceError):
    def __init__(self, message: str, *, evidence: Any | None = None) -> None:
        super().__init__(message)
        self.evidence = evidence


class HeadRaceError(EvidenceError):
    pass


class HostedEvidenceError(EvidenceError):
    pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _git(root: Path, *args: str) -> str:
    return run_git(root, *args, timeout=60)


def _require_sha(value: str, label: str) -> str:
    normalized = str(value or "").casefold()
    if not SHA_RE.fullmatch(normalized):
        raise EvidenceError(f"{label} must be one exact lowercase Git SHA")
    return normalized


def _normalized_status(root: Path) -> str:
    return _git(root, "status", "--porcelain=v2", "--untracked-files=all")


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


@dataclass(frozen=True, slots=True)
class TrustedCommand:
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    environment_allowlist: tuple[str, ...]
    environment: Mapping[str, str]
    timeout_seconds: float
    output_limit_bytes: int
    candidate_head: str
    expected_working_tree: str
    expected_status_sha256: str | None = None
    required_exit_code: int = 0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TrustedCommand":
        arguments = raw.get("arguments", ())
        allowlist = raw.get("environment_allowlist", ())
        environment = raw.get("environment", {})
        if not isinstance(arguments, (list, tuple)) or not all(
            isinstance(item, str) for item in arguments
        ):
            raise EvidenceError("command arguments must be a string array")
        if not isinstance(allowlist, (list, tuple)) or not all(
            isinstance(item, str) for item in allowlist
        ):
            raise EvidenceError("environment_allowlist must be a string array")
        if not isinstance(environment, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise EvidenceError("command environment must be a string mapping")
        return cls(
            executable=str(raw.get("executable", "")),
            arguments=tuple(arguments),
            working_directory=str(raw.get("working_directory", "")),
            environment_allowlist=tuple(allowlist),
            environment=dict(environment),
            timeout_seconds=float(raw.get("timeout_seconds", 0)),
            output_limit_bytes=int(raw.get("output_limit_bytes", 0)),
            candidate_head=_require_sha(str(raw.get("candidate_head", "")), "candidate head"),
            expected_working_tree=str(raw.get("expected_working_tree", "")),
            expected_status_sha256=(
                str(raw["expected_status_sha256"])
                if raw.get("expected_status_sha256") is not None
                else None
            ),
            required_exit_code=int(raw.get("required_exit_code", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    protocol_version: str
    executable: str
    executable_sha256: str
    arguments: tuple[str, ...]
    working_directory: str
    environment_names: tuple[str, ...]
    timeout_seconds: float
    output_limit_bytes: int
    candidate_head: str
    head_after: str
    status_before_sha256: str
    status_after_sha256: str
    exit_code: int
    timed_out: bool
    output_limited: bool
    stdout_sha256: str
    stderr_sha256: str
    stdout: str
    stderr: str
    required_exit_code: int
    passed: bool
    duration_ms: int
    evidence_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _StreamCollector:
    def __init__(self, limit: int, shared: "_OutputBudget") -> None:
        self.limit = limit
        self.shared = shared
        self.digest = hashlib.sha256()
        self.parts: list[bytes] = []
        self.stored = 0
        self.error: BaseException | None = None

    def read(self, handle: Any) -> None:
        try:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                self.digest.update(chunk)
                self.shared.add(len(chunk))
                if self.stored < self.limit:
                    keep = chunk[: self.limit - self.stored]
                    self.parts.append(keep)
                    self.stored += len(keep)
        except BaseException as exc:  # pragma: no cover - OS pipe failure
            self.error = exc

    @property
    def data(self) -> bytes:
        return b"".join(self.parts)


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self.exceeded = threading.Event()
        self.lock = threading.Lock()

    def add(self, count: int) -> None:
        with self.lock:
            self.total += count
            if self.total > self.limit:
                self.exceeded.set()


def _resolve_executable(value: str, cwd: Path) -> Path:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw:
        raise EvidenceError("validation executable is empty or malformed")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve(strict=True)
    else:
        found = shutil.which(raw)
        if not found:
            raise EvidenceError(f"validation executable is not available: {raw}")
        resolved = Path(found).resolve(strict=True)
    if not resolved.is_file():
        raise EvidenceError(f"validation executable is not a regular file: {resolved}")
    return resolved


def _command_environment(spec: TrustedCommand) -> dict[str, str]:
    requested = set(spec.environment_allowlist)
    if not requested.issubset(DEFAULT_ENV_ALLOWLIST):
        denied = sorted(requested - DEFAULT_ENV_ALLOWLIST)
        raise EvidenceError(f"validation environment names are not allowlisted: {denied}")
    if not set(spec.environment).issubset(requested):
        raise EvidenceError("explicit validation environment contains a non-allowlisted name")
    environment = {
        name: os.environ[name]
        for name in sorted(requested)
        if name in os.environ
    }
    environment.update(spec.environment)
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _assert_worktree_condition(
    condition: str, status_text: str, expected_digest: str | None
) -> str:
    digest = _sha256(status_text.encode("utf-8"))
    if condition == "CLEAN":
        if status_text:
            raise ValidationFailure("validation requires a clean exact candidate worktree")
    elif condition == "EXACT_STATUS":
        if not expected_digest or digest != expected_digest:
            raise ValidationFailure("validation worktree status differs from the admitted digest")
    else:
        raise EvidenceError("expected_working_tree must be CLEAN or EXACT_STATUS")
    return digest


def execute_trusted_command(spec: TrustedCommand | Mapping[str, Any]) -> CommandEvidence:
    if not isinstance(spec, TrustedCommand):
        spec = TrustedCommand.from_dict(spec)
    if spec.timeout_seconds <= 0 or spec.timeout_seconds > 86400:
        raise EvidenceError("validation timeout must be in (0, 86400]")
    if spec.output_limit_bytes <= 0 or spec.output_limit_bytes > 100 * 1024 * 1024:
        raise EvidenceError("validation output limit must be in (0, 100 MiB]")
    cwd = Path(spec.working_directory).expanduser().resolve(strict=True)
    root = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if os.path.normcase(str(root)) != os.path.normcase(str(cwd)):
        raise EvidenceError("validation working directory must be the exact Git root")
    head_before = _git(root, "rev-parse", "HEAD").casefold()
    if head_before != spec.candidate_head:
        raise HeadRaceError("candidate head changed before validation execution")
    status_before = _normalized_status(root)
    status_before_digest = _assert_worktree_condition(
        spec.expected_working_tree, status_before, spec.expected_status_sha256
    )
    executable = _resolve_executable(spec.executable, cwd)
    environment = _command_environment(spec)
    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    started = time.monotonic()
    process = subprocess.Popen(
        (str(executable), *spec.arguments),
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    assert process.stdout is not None and process.stderr is not None
    budget = _OutputBudget(spec.output_limit_bytes)
    # Each stream may retain at most the total output limit; shared accounting
    # terminates the process once their combined bytes exceed it.
    stdout = _StreamCollector(spec.output_limit_bytes, budget)
    stderr = _StreamCollector(spec.output_limit_bytes, budget)
    threads = [
        threading.Thread(target=stdout.read, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.read, args=(process.stderr,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = started + spec.timeout_seconds
    timed_out = False
    output_limited = False
    while process.poll() is None:
        if budget.exceeded.is_set():
            output_limited = True
            _terminate_process_tree(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_process_tree(process)
            break
        time.sleep(0.01)
    for thread in threads:
        thread.join(timeout=10)
    process.stdout.close()
    process.stderr.close()
    if any(thread.is_alive() for thread in threads):
        raise EvidenceError("validation output reader did not terminate")
    if stdout.error or stderr.error:
        raise EvidenceError(f"validation output read failed: {stdout.error or stderr.error}")
    exit_code = process.returncode if process.returncode is not None else -1
    head_after = _git(root, "rev-parse", "HEAD").casefold()
    status_after = _normalized_status(root)
    status_after_digest = _assert_worktree_condition(
        spec.expected_working_tree, status_after, spec.expected_status_sha256
    )
    if head_after != spec.candidate_head:
        raise HeadRaceError("candidate head changed during validation execution")
    passed = (
        not timed_out
        and not output_limited
        and exit_code == spec.required_exit_code
        and status_before_digest == status_after_digest
    )
    body = {
        "protocol_version": "ccos-validation-execution-v1",
        "executable": str(executable),
        "executable_sha256": _sha256(executable.read_bytes()),
        "arguments": spec.arguments,
        "working_directory": str(cwd),
        "environment_names": tuple(sorted(environment)),
        "timeout_seconds": spec.timeout_seconds,
        "output_limit_bytes": spec.output_limit_bytes,
        "candidate_head": spec.candidate_head,
        "head_after": head_after,
        "status_before_sha256": status_before_digest,
        "status_after_sha256": status_after_digest,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output_limited": output_limited,
        "stdout_sha256": stdout.digest.hexdigest(),
        "stderr_sha256": stderr.digest.hexdigest(),
        "stdout": stdout.data.decode("utf-8", errors="replace"),
        "stderr": stderr.data.decode("utf-8", errors="replace"),
        "required_exit_code": spec.required_exit_code,
        "passed": passed,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
    }
    body["evidence_sha256"] = _sha256(_canonical_json(body))
    evidence = CommandEvidence(**body)
    if not passed:
        reason = (
            "timeout"
            if timed_out
            else "output limit"
            if output_limited
            else f"exit {exit_code}, required {spec.required_exit_code}"
        )
        raise ValidationFailure(
            f"trusted validation failed ({reason}); evidence={evidence.evidence_sha256}",
            evidence=evidence,
        )
    return evidence


def exact_repository_evidence(
    root: str | Path, *, base_sha: str, candidate_head: str
) -> dict[str, Any]:
    repo = Path(root).expanduser().resolve(strict=True)
    base = _require_sha(base_sha, "base SHA")
    head = _require_sha(candidate_head, "candidate head")
    observed = _git(repo, "rev-parse", "HEAD").casefold()
    if observed != head:
        raise HeadRaceError("repository HEAD differs from the frozen candidate")
    _git(repo, "cat-file", "-e", f"{base}^{{commit}}")
    _git(repo, "cat-file", "-e", f"{head}^{{commit}}")
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}").casefold()
    diff = subprocess.run(
        ("git", "diff", "--binary", "--no-ext-diff", base, head, "--"),
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    if diff.returncode != 0:
        raise EvidenceError(
            f"cannot collect exact diff: {diff.stderr.decode('utf-8', errors='replace')[:1000]}"
        )
    names_raw = _git(repo, "diff", "--name-status", "-z", base, head, "--")
    tokens = [item for item in names_raw.split("\x00") if item]
    changed: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        paths = tokens[index : index + path_count]
        if len(paths) != path_count:
            raise EvidenceError("Git returned a truncated NUL-delimited name-status record")
        index += path_count
        changed.append("\t".join((status, *paths)))
    after = _git(repo, "rev-parse", "HEAD").casefold()
    if after != head:
        raise HeadRaceError("repository HEAD changed while freezing exact diff evidence")
    evidence = {
        "protocol_version": "ccos-repository-evidence-v1",
        "root": str(repo),
        "base_sha": base,
        "head_sha": head,
        "tree_sha": tree,
        "diff_sha256": _sha256(diff.stdout),
        "diff_size": len(diff.stdout),
        "changed_entries": changed,
        "status_sha256": _sha256(_normalized_status(repo).encode("utf-8")),
    }
    evidence["evidence_sha256"] = _sha256(_canonical_json(evidence))
    return evidence


def _gh_json(root: Path, args: Sequence[str], timeout: float = 60) -> Any:
    executable = shutil.which("gh")
    if not executable:
        raise HostedEvidenceError("GitHub CLI is unavailable")
    result = subprocess.run(
        (executable, *args),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise HostedEvidenceError(
            f"GitHub query failed ({result.returncode}): {result.stderr.strip()[:1000]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HostedEvidenceError("GitHub query returned non-JSON output") from exc


def collect_hosted_checks(
    root: str | Path, *, repository: str, candidate_head: str
) -> dict[str, Any]:
    repo = Path(root).resolve(strict=True)
    head = _require_sha(candidate_head, "candidate head")
    payload = _gh_json(
        repo,
        (
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repository}/commits/{head}/check-runs?per_page=100",
        ),
    )
    runs = payload.get("check_runs") if isinstance(payload, Mapping) else None
    if not isinstance(runs, list):
        raise HostedEvidenceError("GitHub check-runs response is malformed")
    checks: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, Mapping):
            raise HostedEvidenceError("GitHub check-runs response contains a non-object")
        checks.append(
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "head_sha": run.get("head_sha"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "details_url": run.get("details_url"),
                "completed_at": run.get("completed_at"),
            }
        )
    if any(item["head_sha"] != head for item in checks):
        raise HeadRaceError("hosted check evidence contains another head")
    result = {
        "protocol_version": "ccos-hosted-checks-v1",
        "repository": repository,
        "candidate_head": head,
        "checks": sorted(checks, key=lambda item: (str(item["name"]), int(item["id"] or 0))),
    }
    result["evidence_sha256"] = _sha256(_canonical_json(result))
    return result


def collect_review_receipts(
    root: str | Path, *, repository: str, pull_request: int, candidate_head: str
) -> dict[str, Any]:
    repo = Path(root).resolve(strict=True)
    head = _require_sha(candidate_head, "candidate head")
    payload = _gh_json(
        repo,
        (
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repository}/pulls/{int(pull_request)}/reviews?per_page=100",
        ),
    )
    if not isinstance(payload, list):
        raise HostedEvidenceError("GitHub reviews response is malformed")
    receipts = []
    for review in payload:
        if not isinstance(review, Mapping) or review.get("commit_id") != head:
            continue
        user = review.get("user") if isinstance(review.get("user"), Mapping) else {}
        body = str(review.get("body") or "")
        receipts.append(
            {
                "id": review.get("id"),
                "reviewer": user.get("login"),
                "state": review.get("state"),
                "commit_id": head,
                "submitted_at": review.get("submitted_at"),
                "body_sha256": _sha256(body.encode("utf-8")),
            }
        )
    result = {
        "protocol_version": "ccos-review-receipts-v1",
        "repository": repository,
        "pull_request": int(pull_request),
        "candidate_head": head,
        "reviews": sorted(receipts, key=lambda item: int(item["id"] or 0)),
    }
    result["evidence_sha256"] = _sha256(_canonical_json(result))
    return result


def publication_preflight(
    root: str | Path,
    *,
    expected_remote: str,
    candidate_head: str,
    hosted_checks: Mapping[str, Any] | None,
    required_checks: Iterable[str],
) -> dict[str, Any]:
    repo = Path(root).resolve(strict=True)
    head = _require_sha(candidate_head, "candidate head")
    if _git(repo, "rev-parse", "HEAD").casefold() != head:
        raise HeadRaceError("publication candidate head is no longer checked out")
    if _normalized_status(repo):
        raise EvidenceError("publication requires a clean exact candidate worktree")
    remote = _git(repo, "remote", "get-url", "origin")
    if normalize_remote_url(remote) != normalize_remote_url(expected_remote):
        raise EvidenceError("publication remote differs from campaign authority")
    required = tuple(str(item) for item in required_checks)
    if required and hosted_checks is None:
        raise HostedEvidenceError("required hosted check evidence is missing")
    if hosted_checks is not None and hosted_checks.get("candidate_head") != head:
        raise HeadRaceError("hosted check evidence is not bound to the candidate head")
    by_name = {
        str(item.get("name")): item
        for item in (hosted_checks or {}).get("checks", [])
        if isinstance(item, Mapping)
    }
    missing = []
    failing = []
    for name in required:
        check = by_name.get(str(name))
        if check is None:
            missing.append(str(name))
        elif check.get("status") != "completed" or check.get("conclusion") != "success":
            failing.append(str(name))
    if missing or failing:
        raise EvidenceError(
            f"publication checks are not passing; missing={missing}, failing={failing}"
        )
    result = {
        "protocol_version": "ccos-publication-preflight-v1",
        "candidate_head": head,
        "remote": normalize_remote_url(remote),
        "required_checks": sorted(required),
        "hosted_checks_evidence": (
            hosted_checks.get("evidence_sha256") if hosted_checks is not None else None
        ),
    }
    result["evidence_sha256"] = _sha256(_canonical_json(result))
    return result
