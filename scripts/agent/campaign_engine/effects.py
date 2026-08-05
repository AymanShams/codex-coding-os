"""Transactional, idempotent external effects and exact-file replacement.

External mutations have one stable operation identity and one durable state:
PREPARED -> EXECUTING -> CONFIRMED | FAILED | AMBIGUOUS | CANCELLED.
An AMBIGUOUS operation is only queried and reconciled.  It is never executed
again blindly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .admission import AdmissionError, normalize_remote_url


EFFECT_STATES = frozenset(
    {"PREPARED", "EXECUTING", "CONFIRMED", "FAILED", "AMBIGUOUS", "CANCELLED"}
)
EFFECT_KINDS = frozenset(
    {
        "PUSH",
        "CREATE_PULL_REQUEST",
        "UPSERT_COMMENT",
        "MERGE",
        "EXACT_FILE_REPLACE",
    }
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EffectError(RuntimeError):
    pass


class EffectConflict(EffectError):
    pass


class EffectCancelled(EffectError):
    pass


class AmbiguousMutation(EffectError):
    pass


class EffectStore(Protocol):
    def prepare_effect(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        node_id: str,
        kind: str,
        payload: Mapping[str, Any],
        payload_digest: str,
    ) -> Mapping[str, Any]: ...

    def get_effect(self, operation_id: str) -> Mapping[str, Any]: ...

    def update_effect(
        self,
        operation_id: str,
        *,
        expected_state: str,
        state: str,
        result: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", text):
        raise EffectError(f"{label} is not one stable identifier")
    return text


def _require_sha(value: Any, label: str) -> str:
    text = str(value or "").casefold()
    if not SHA_RE.fullmatch(text):
        raise EffectError(f"{label} must be one exact Git SHA")
    return text


@dataclass(frozen=True, slots=True)
class EffectIntent:
    operation_id: str
    campaign_id: str
    node_id: str
    kind: str
    payload: Mapping[str, Any]
    payload_digest: str

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        campaign_id: str,
        node_id: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> "EffectIntent":
        normalized_kind = str(kind).upper()
        if normalized_kind not in EFFECT_KINDS:
            raise EffectError(f"unsupported external effect kind: {kind}")
        normalized = json.loads(_canonical_json(dict(payload)))
        return cls(
            operation_id=_stable_id(operation_id, "operation id"),
            campaign_id=_stable_id(campaign_id, "campaign id"),
            node_id=_stable_id(node_id, "node id"),
            kind=normalized_kind,
            payload=normalized,
            payload_digest=_digest(normalized),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EffectBackend(Protocol):
    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def query(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ExternalEffectDriver:
    def __init__(self, store: EffectStore, backend: EffectBackend) -> None:
        self.store = store
        self.backend = backend

    def prepare(self, intent: EffectIntent) -> Mapping[str, Any]:
        return self.store.prepare_effect(
            operation_id=intent.operation_id,
            campaign_id=intent.campaign_id,
            node_id=intent.node_id,
            kind=intent.kind,
            payload=intent.payload,
            payload_digest=intent.payload_digest,
        )

    @staticmethod
    def _assert_record(record: Mapping[str, Any]) -> None:
        state = record.get("state")
        if state not in EFFECT_STATES:
            raise EffectError(f"external effect has invalid state: {state}")
        payload = record.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, Mapping) or _digest(payload) != record.get("payload_digest"):
            raise EffectConflict("external effect payload differs from its durable digest")

    def _confirm_from_query(
        self, record: Mapping[str, Any], *, expected_state: str
    ) -> Mapping[str, Any] | None:
        payload = record.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        query = dict(self.backend.query(str(record["kind"]), payload))
        if not query.get("confirmed"):
            return None
        if expected_state == "PREPARED":
            executing = self.store.update_effect(
                str(record["operation_id"]),
                expected_state="PREPARED",
                state="EXECUTING",
                result={"query_before_mutation": query},
            )
            expected_state = str(executing["state"])
        return self.store.update_effect(
            str(record["operation_id"]),
            expected_state=expected_state,
            state="CONFIRMED",
            result=query,
        )

    def run(self, operation_id: str, *, cancelled: bool = False) -> Mapping[str, Any]:
        operation = _stable_id(operation_id, "operation id")
        record = self.store.get_effect(operation)
        self._assert_record(record)
        state = str(record["state"])
        if state in {"CONFIRMED", "FAILED", "CANCELLED"}:
            return record
        if cancelled:
            if state == "EXECUTING":
                # The mutation may have escaped. Cancellation cannot rewrite
                # uncertainty into a clean cancellation.
                return self.store.update_effect(
                    operation,
                    expected_state="EXECUTING",
                    state="AMBIGUOUS",
                    result={
                        "error": "campaign cancelled while external effect may have been executing"
                    },
                )
            if state == "AMBIGUOUS":
                return self.reconcile(operation)
            return self.store.update_effect(
                operation,
                expected_state="PREPARED",
                state="CANCELLED",
                result={"error": "campaign cancellation prevented execution"},
            )
        if state == "AMBIGUOUS":
            return self.reconcile(operation)
        if state == "EXECUTING":
            # A restart observed an interrupted in-flight effect. Query only.
            ambiguous = self.store.update_effect(
                operation,
                expected_state="EXECUTING",
                state="AMBIGUOUS",
                result={
                    "error": "execution outcome was not durably confirmed before restart"
                },
            )
            return self.reconcile(str(ambiguous["operation_id"]))
        if state != "PREPARED":
            raise EffectConflict(f"external effect cannot execute from {state}")
        try:
            already = self._confirm_from_query(record, expected_state="PREPARED")
        except Exception as exc:
            executing = self.store.update_effect(
                operation,
                expected_state="PREPARED",
                state="EXECUTING",
                result={"query_before_mutation_error": f"{type(exc).__name__}: {exc}"},
            )
            return self.store.update_effect(
                operation,
                expected_state=str(executing["state"]),
                state="FAILED",
                result={"error": f"preflight query failed: {type(exc).__name__}: {exc}"},
            )
        if already is not None:
            return already
        executing = self.store.update_effect(
            operation, expected_state="PREPARED", state="EXECUTING"
        )
        payload = executing.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            result = dict(self.backend.execute(str(executing["kind"]), payload))
        except AmbiguousMutation as exc:
            ambiguous = self.store.update_effect(
                operation,
                expected_state="EXECUTING",
                state="AMBIGUOUS",
                result={"error": str(exc)},
            )
            return self.reconcile(str(ambiguous["operation_id"]))
        except Exception as exc:
            return self.store.update_effect(
                operation,
                expected_state="EXECUTING",
                state="FAILED",
                result={"error": f"{type(exc).__name__}: {exc}"},
            )
        try:
            query = dict(self.backend.query(str(executing["kind"]), payload))
        except Exception as exc:
            return self.store.update_effect(
                operation,
                expected_state="EXECUTING",
                state="AMBIGUOUS",
                result={
                    "execution": result,
                    "error": f"confirmation query failed: {type(exc).__name__}: {exc}",
                },
            )
        if not query.get("confirmed"):
            return self.store.update_effect(
                operation,
                expected_state="EXECUTING",
                state="AMBIGUOUS",
                result={
                    "execution": result,
                    "error": "mutation returned but external confirmation is absent",
                },
            )
        return self.store.update_effect(
            operation,
            expected_state="EXECUTING",
            state="CONFIRMED",
            result={"execution": result, "confirmation": query},
        )

    def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        record = self.store.get_effect(_stable_id(operation_id, "operation id"))
        self._assert_record(record)
        if record.get("state") == "CONFIRMED":
            return record
        if record.get("state") != "AMBIGUOUS":
            raise EffectConflict("only an AMBIGUOUS effect can be reconciled")
        try:
            confirmed = self._confirm_from_query(record, expected_state="AMBIGUOUS")
        except Exception as exc:
            return self.store.update_effect(
                str(record["operation_id"]),
                expected_state="AMBIGUOUS",
                state="AMBIGUOUS",
                result={
                    "confirmed": False,
                    "queried": True,
                    "error": f"reconciliation query failed: {type(exc).__name__}: {exc}",
                },
            )
        if confirmed is not None:
            return confirmed
        # Absence is not necessarily proof of failure for every provider. Leave
        # the operation ambiguous so a later named external event may reconcile.
        return self.store.update_effect(
            str(record["operation_id"]),
            expected_state="AMBIGUOUS",
            state="AMBIGUOUS",
            result={
                "confirmed": False,
                "queried": True,
                "error": "external query did not prove completion",
            },
        )


class GitHubBackend:
    """First-party Git/GitHub implementation with query-before-mutate behavior."""

    COMMENT_PAGE_SIZE = 100
    COMMENT_MAX_PAGES = 100

    def __init__(self, *, gh_executable: str = "gh", git_executable: str = "git") -> None:
        self.gh = shutil.which(gh_executable) or gh_executable
        self.git = shutil.which(git_executable) or git_executable

    @staticmethod
    def _comment_contract(payload: Mapping[str, Any]) -> tuple[str, str]:
        marker = str(payload.get("marker", "")).strip()
        body = str(payload.get("body", ""))
        if not marker:
            raise EffectError("comment marker must be nonempty")
        if body.count(marker) != 1:
            raise EffectError(
                "comment body must embed the unique marker exactly once"
            )
        return marker, body

    def _issue_comments(
        self, root: Path, repository: str, issue: int
    ) -> list[Mapping[str, Any]]:
        comments: list[Mapping[str, Any]] = []
        for page in range(1, self.COMMENT_MAX_PAGES + 1):
            rows = self._gh_json(
                root,
                (
                    "api",
                    f"repos/{repository}/issues/{issue}/comments"
                    f"?per_page={self.COMMENT_PAGE_SIZE}&page={page}",
                ),
            )
            if not isinstance(rows, list):
                raise EffectError("GitHub comment query returned a non-array page")
            comments.extend(row for row in rows if isinstance(row, Mapping))
            if len(rows) < self.COMMENT_PAGE_SIZE:
                return comments
        raise EffectError(
            "GitHub comment reconciliation exceeded the bounded page limit"
        )

    @staticmethod
    def _root(payload: Mapping[str, Any]) -> Path:
        return Path(str(payload.get("root", ""))).resolve(strict=True)

    @staticmethod
    def _repository_from_remote(normalized_remote: str) -> tuple[str, str]:
        """Return the exact CLI repository selector and owner/name identity."""

        from urllib.parse import urlsplit

        parsed = urlsplit(normalized_remote)
        parts = tuple(part for part in parsed.path.strip("/").split("/") if part)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or len(parts) != 2:
            raise EffectConflict(
                "repository remote is not one exact GitHub owner/repository identity"
            )
        owner_name = "/".join(parts)
        selector = (
            owner_name
            if parsed.hostname.casefold() == "github.com"
            else f"{parsed.hostname.casefold()}/{owner_name}"
        )
        return selector, owner_name

    def _assert_repository_binding(
        self, root: Path, payload: Mapping[str, Any]
    ) -> tuple[str, str]:
        """Recheck Git root, fetch/push remote, and provider identity immediately.

        Admission evidence can become stale before an effect executes.  The
        effect backend therefore re-resolves the immutable repository identity
        at every query and every mutation boundary.  Mutations also use the
        returned explicit remote/repository rather than ambient cwd context.
        """

        expected_remote = str(payload.get("repository_remote", "")).strip()
        if not expected_remote:
            raise EffectConflict("repository_remote is required for every GitHub effect")
        try:
            normalized_expected = normalize_remote_url(expected_remote)
        except AdmissionError as exc:
            raise EffectConflict(f"repository_remote is invalid: {exc}") from exc

        observed_root = Path(
            self._run(
                (self.git, "rev-parse", "--show-toplevel"),
                cwd=root,
                mutation=False,
            )
        ).resolve(strict=True)
        if os.path.normcase(str(observed_root)) != os.path.normcase(str(root)):
            raise EffectConflict("effect worktree is not the exact Git root")

        remote_name = _stable_id(payload.get("remote", "origin"), "Git remote name")
        observed_urls: list[str] = []
        for mode in ((), ("--push",)):
            output = self._run(
                (self.git, "remote", "get-url", *mode, "--all", remote_name),
                cwd=root,
                mutation=False,
            )
            observed_urls.extend(line.strip() for line in output.splitlines() if line.strip())
        if not observed_urls:
            raise EffectConflict("Git remote has no fetch or push URL")
        try:
            normalized_observed = {
                normalize_remote_url(value) for value in observed_urls
            }
        except AdmissionError as exc:
            raise EffectConflict(f"Git remote URL is invalid: {exc}") from exc
        if normalized_observed != {normalized_expected}:
            raise EffectConflict("Git remote changed after campaign admission")

        repository_selector, owner_name = self._repository_from_remote(normalized_expected)
        configured_repository = str(payload.get("repository", "")).strip()
        if configured_repository:
            configured = configured_repository.strip("/").casefold()
            accepted = {owner_name.casefold(), repository_selector.casefold()}
            if configured not in accepted:
                raise EffectConflict(
                    "configured GitHub repository differs from repository_remote"
                )
        return expected_remote, repository_selector

    def _run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 120,
        mutation: bool,
    ) -> str:
        try:
            result = subprocess.run(
                list(argv),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if mutation:
                raise AmbiguousMutation(f"mutation timed out: {argv[0]}") from exc
            raise EffectError(f"query timed out: {argv[0]}") from exc
        except OSError as exc:
            raise EffectError(f"cannot execute {argv[0]}: {exc}") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            if mutation:
                # Once a mutation process has started, a nonzero CLI result is
                # not proof that the provider rejected it before applying the
                # change.  This includes HTTP 5xx responses and unknown CLI
                # failures.  Only query/reconciliation may decide the outcome.
                raise AmbiguousMutation(
                    f"mutation transport failed after start: {message[:1000]}"
                )
            raise EffectError(
                f"command exited {result.returncode}: {' '.join(argv)}: {message[:1000]}"
            )
        return result.stdout.strip()

    def _gh_json(self, root: Path, args: Sequence[str]) -> Any:
        raw = self._run((self.gh, *args), cwd=root, mutation=False)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EffectError("GitHub query returned non-JSON output") from exc

    @staticmethod
    def _assert_pull_request_binding(
        row: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> None:
        expected_head = _require_sha(payload.get("head"), "pull request head")
        expected_branch = str(payload.get("head_branch", "")).strip()
        expected_base = str(payload.get("base", "main")).strip()
        if row.get("headRefOid") != expected_head:
            raise EffectConflict("pull request head differs from the exact candidate head")
        if expected_branch and row.get("headRefName") != expected_branch:
            raise EffectConflict("pull request branch differs from the admitted branch")
        if expected_base and row.get("baseRefName") != expected_base:
            raise EffectConflict("pull request base differs from the approved base")

    def _exact_pull_request(
        self, root: Path, payload: Mapping[str, Any], repository: str
    ) -> Mapping[str, Any] | None:
        base = str(payload.get("base", "main"))
        head_branch = str(payload.get("head_branch", ""))
        expected_head = _require_sha(payload.get("head"), "pull request head")
        rows = self._gh_json(
            root,
            (
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "all",
                "--head",
                head_branch,
                "--base",
                base,
                "--json",
                "number,url,state,headRefName,headRefOid,baseRefName,mergeCommit",
            ),
        )
        if not isinstance(rows, list):
            raise EffectError("GitHub pull request query returned a non-array")
        matches = [
            row
            for row in rows
            if isinstance(row, Mapping)
            and row.get("headRefName") == head_branch
            and row.get("baseRefName") == base
            and row.get("headRefOid") == expected_head
        ]
        if len(matches) > 1:
            raise EffectConflict("duplicate exact-head pull requests exist")
        return matches[0] if matches else None

    def _pull_request_by_number(
        self,
        root: Path,
        payload: Mapping[str, Any],
        repository: str,
        number: int,
    ) -> Mapping[str, Any]:
        row = self._gh_json(
            root,
            (
                "pr",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "number,url,state,headRefName,headRefOid,baseRefName,mergeCommit",
            ),
        )
        if not isinstance(row, Mapping) or int(row.get("number", 0)) != number:
            raise EffectConflict("explicit pull request identity could not be verified")
        self._assert_pull_request_binding(row, payload)
        return row

    def _bound_pull_request(
        self, root: Path, payload: Mapping[str, Any], repository: str
    ) -> Mapping[str, Any] | None:
        number = int(payload.get("pull_request", 0))
        if number > 0:
            return self._pull_request_by_number(root, payload, repository, number)
        return self._exact_pull_request(root, payload, repository)

    def query(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        root = self._root(payload)
        exact_remote, repository = self._assert_repository_binding(root, payload)
        if kind == "PUSH":
            branch = str(payload.get("branch", ""))
            head = _require_sha(payload.get("head"), "push head")
            output = self._run(
                (
                    self.git,
                    "ls-remote",
                    "--heads",
                    exact_remote,
                    f"refs/heads/{branch}",
                ),
                cwd=root,
                mutation=False,
            )
            observed = output.split()[0].casefold() if output else None
            return {"confirmed": observed == head, "remote_head": observed, "expected_head": head}
        if kind == "CREATE_PULL_REQUEST":
            match = self._exact_pull_request(root, payload, repository)
            return {"confirmed": match is not None, "pull_request": match}
        if kind == "UPSERT_COMMENT":
            marker, requested_body = self._comment_contract(payload)
            pull_request = self._bound_pull_request(root, payload, repository)
            if pull_request is None:
                raise EffectConflict("exact-head pull request does not exist for comment")
            issue = int(pull_request.get("number", 0))
            body_digest = _digest(requested_body)
            rows = self._issue_comments(root, repository, issue)
            matches = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                body = str(row.get("body") or "")
                if marker in body:
                    matches.append(
                        {
                            "id": row.get("id"),
                            "url": row.get("html_url"),
                            "body_digest": _digest(body),
                        }
                    )
            if len(matches) > 1:
                raise EffectConflict(
                    "multiple marker comments exist; refusing ambiguous reconciliation"
                )
            exact = [item for item in matches if item["body_digest"] == body_digest]
            return {
                "confirmed": len(matches) == 1 and len(exact) == 1,
                "comment": exact[0] if len(exact) == 1 else None,
                "marker_matches": matches,
            }
        if kind == "MERGE":
            expected_head = _require_sha(payload.get("head"), "merge head")
            row = self._bound_pull_request(root, payload, repository)
            merge_commit = row.get("mergeCommit") if isinstance(row, Mapping) else None
            return {
                "confirmed": bool(
                    isinstance(row, Mapping)
                    and row.get("state") == "MERGED"
                    and row.get("headRefOid") == expected_head
                    and isinstance(merge_commit, Mapping)
                    and merge_commit.get("oid")
                ),
                "pull_request": row,
            }
        raise EffectError(f"GitHub backend cannot query effect kind {kind}")

    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        root = self._root(payload)
        exact_remote, repository = self._assert_repository_binding(root, payload)
        if kind == "PUSH":
            head = _require_sha(payload.get("head"), "push head")
            if self._run((self.git, "rev-parse", "HEAD"), cwd=root, mutation=False).casefold() != head:
                raise EffectConflict("local HEAD changed before push")
            branch = str(payload.get("branch", ""))
            output = self._run(
                (self.git, "push", exact_remote, f"{head}:refs/heads/{branch}"),
                cwd=root,
                mutation=True,
            )
            return {"pushed": True, "output_digest": _digest(output)}
        if kind == "CREATE_PULL_REQUEST":
            existing = self.query(kind, payload)
            if existing.get("confirmed"):
                return {"replayed": True, **existing}
            output = self._run(
                (
                    self.gh,
                    "pr",
                    "create",
                    "--repo",
                    repository,
                    "--base",
                    str(payload.get("base", "main")),
                    "--head",
                    str(payload.get("head_branch", "")),
                    "--title",
                    str(payload.get("title", "")),
                    "--body",
                    str(payload.get("body", "")),
                ),
                cwd=root,
                mutation=True,
            )
            return {"created": True, "url": output.splitlines()[-1] if output else None}
        if kind == "UPSERT_COMMENT":
            _, body = self._comment_contract(payload)
            pull_request = self._bound_pull_request(root, payload, repository)
            if pull_request is None:
                raise EffectConflict("exact-head pull request does not exist for comment")
            issue = int(pull_request.get("number", 0))
            marker_matches = self.query(kind, payload).get("marker_matches", [])
            if len(marker_matches) > 1:
                raise EffectConflict("multiple marker comments exist; refusing ambiguous update")
            if marker_matches:
                comment_id = int(marker_matches[0]["id"])
                output = self._run(
                    (
                        self.gh,
                        "api",
                        "--method",
                        "PATCH",
                        f"repos/{repository}/issues/comments/{comment_id}",
                        "-f",
                        f"body={body}",
                    ),
                    cwd=root,
                    mutation=True,
                )
                return {"updated": True, "response_digest": _digest(output)}
            output = self._run(
                (
                    self.gh,
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/issues/{issue}/comments",
                    "-f",
                    f"body={body}",
                ),
                cwd=root,
                mutation=True,
            )
            return {"created": True, "response_digest": _digest(output)}
        if kind == "MERGE":
            existing = self.query(kind, payload)
            if existing.get("confirmed"):
                return {"replayed": True, **existing}
            pull_request = existing.get("pull_request")
            number = int(payload.get("pull_request", 0))
            if number <= 0 and isinstance(pull_request, Mapping):
                number = int(pull_request.get("number", 0))
            if number <= 0:
                raise EffectConflict("exact-head pull request does not exist for merge")
            if isinstance(pull_request, Mapping) and pull_request.get("state") != "OPEN":
                raise EffectConflict("exact-head pull request is not open for merge")
            head = _require_sha(payload.get("head"), "merge head")
            method = str(payload.get("method", "squash"))
            if method not in {"merge", "squash", "rebase"}:
                raise EffectError("merge method must be merge, squash, or rebase")
            output = self._run(
                (
                    self.gh,
                    "pr",
                    "merge",
                    str(number),
                    "--repo",
                    repository,
                    f"--{method}",
                    "--match-head-commit",
                    head,
                ),
                cwd=root,
                mutation=True,
            )
            return {"merged": True, "output_digest": _digest(output)}
        raise EffectError(f"GitHub backend cannot execute effect kind {kind}")


class ExactFileEffectDriver:
    """Narrow exact-file replacement with baseline and post-write proof.

    This preserves the proven one-file replacement semantics without importing
    the old broker, controller, ACL lifecycle, or case authority.  It can only
    replace one explicitly named regular file with explicitly supplied bytes.
    """

    def __init__(self, journal_root: str | Path) -> None:
        self.journal_root = Path(journal_root).expanduser().resolve(strict=False)
        self.journal_root.mkdir(parents=True, exist_ok=True)

    def replace(
        self,
        *,
        operation_id: str,
        target: str | Path,
        expected_baseline_sha256: str,
        replacement: bytes,
        expected_replacement_sha256: str,
    ) -> dict[str, Any]:
        operation = _stable_id(operation_id, "operation id")
        if not SHA256_RE.fullmatch(expected_baseline_sha256):
            raise EffectError("baseline digest must be SHA256")
        if not SHA256_RE.fullmatch(expected_replacement_sha256):
            raise EffectError("replacement digest must be SHA256")
        if hashlib.sha256(replacement).hexdigest() != expected_replacement_sha256:
            raise EffectConflict("replacement bytes differ from their approved digest")
        path = Path(target).expanduser().resolve(strict=True)
        metadata = os.lstat(path)
        if path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
            raise EffectConflict("exact-file target must be one non-linked regular file")
        operation_file_stem = hashlib.sha256(operation.encode("utf-8")).hexdigest()
        journal_path = self.journal_root / f"{operation_file_stem}.json"
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if (
                journal.get("target") != str(path)
                or journal.get("replacement_sha256") != expected_replacement_sha256
                or journal.get("baseline_sha256") != expected_baseline_sha256
            ):
                raise EffectConflict("operation identity is already bound to another replacement")
            if journal.get("state") == "CONFIRMED":
                if _file_digest(path) != expected_replacement_sha256:
                    raise EffectConflict("confirmed exact-file target later changed")
                return journal
        observed = _file_digest(path)
        if observed == expected_replacement_sha256:
            result = {
                "protocol_version": "ccos-exact-file-effect-v1",
                "operation_id": operation,
                "target": str(path),
                "baseline_sha256": expected_baseline_sha256,
                "replacement_sha256": expected_replacement_sha256,
                "state": "CONFIRMED",
                "replayed": True,
            }
            self._atomic_journal(journal_path, result)
            return result
        if observed != expected_baseline_sha256:
            raise EffectConflict("exact-file target differs from its approved baseline")
        backup_path = self.journal_root / f"{operation_file_stem}.baseline"
        backup_path.write_bytes(path.read_bytes())
        if _file_digest(backup_path) != expected_baseline_sha256:
            raise EffectError("exact-file baseline backup verification failed")
        prepared = {
            "protocol_version": "ccos-exact-file-effect-v1",
            "operation_id": operation,
            "target": str(path),
            "baseline_sha256": expected_baseline_sha256,
            "replacement_sha256": expected_replacement_sha256,
            "backup": str(backup_path),
            "state": "PREPARED",
            "replayed": False,
        }
        self._atomic_journal(journal_path, prepared)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.{operation_file_stem}.", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path = Path(temporary)
            if _file_digest(temp_path) != expected_replacement_sha256:
                raise EffectError("exact-file staged replacement verification failed")
            os.replace(temp_path, path)
            if _file_digest(path) != expected_replacement_sha256:
                # Restore the verified baseline on any post-replacement mismatch.
                os.replace(backup_path, path)
                raise EffectError("exact-file post-replacement verification failed")
            confirmed = {**prepared, "state": "CONFIRMED"}
            self._atomic_journal(journal_path, confirmed)
            return confirmed
        except BaseException:
            if Path(temporary).exists():
                Path(temporary).unlink()
            raise

    @staticmethod
    def _atomic_journal(path: Path, value: Mapping[str, Any]) -> None:
        data = _canonical_json(value) + b"\n"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
