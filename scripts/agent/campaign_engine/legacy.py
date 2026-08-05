"""Read-only legacy inspection and evidence-preserving archive ingestion.

This module has no legacy transition, registration, actor, review, repair,
publication, or disposition operation.  Legacy records are never activated in
the campaign engine and never interpreted as new-engine success or failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol


ARCHIVE_PROTOCOL = "ccos-legacy-archive-v1"
UNRESOLVED = "LEGACY_ARCHIVED_UNRESOLVED"
TERMINAL = "LEGACY_ARCHIVED_TERMINAL_EVIDENCE"
LEGACY_TERMINAL_STATES = frozenset(
    {
        "CLOSED_SUCCESS",
        "CLOSED_FAILURE",
        "CASE_LOCKED",
        "STOPPED",
        "CANCELLED",
        "FAILED",
    }
)


class LegacyError(RuntimeError):
    pass


class LegacyStore(Protocol):
    def record_legacy_archive(
        self,
        *,
        archive_id: str,
        source_path: str,
        digest: str,
        last_state: str,
        classification: str,
        evidence: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_relative(value: object, *, field: str) -> Path:
    """Return one canonical relative archive path or reject escape spellings."""

    rendered = str(value)
    relative = PurePosixPath(rendered)
    if (
        not rendered
        or relative.is_absolute()
        or rendered != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in rendered
    ):
        raise LegacyError(f"legacy archive {field} is not a canonical relative path")
    return Path(*relative.parts)


def _safe_regular_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix().encode("utf-8")):
        relative = path.relative_to(root)
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise LegacyError(f"legacy path is not canonical: {relative}")
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or path.is_symlink():
            raise LegacyError(f"legacy archive refuses symbolic links: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise LegacyError(f"legacy archive refuses non-regular file: {relative}")
        result.append(path)
    return result


def inspect_legacy_root(root: str | Path) -> dict[str, Any]:
    source = Path(root).expanduser().resolve(strict=True)
    if not source.is_dir() or source.is_symlink():
        raise LegacyError("legacy state root must be one real directory")
    files = []
    for path in _safe_regular_files(source):
        metadata = path.stat()
        files.append(
            {
                "path": path.relative_to(source).as_posix(),
                "size": metadata.st_size,
                "sha256": _file_sha(path),
                "mtime_ns": metadata.st_mtime_ns,
            }
        )
    digest = hashlib.sha256()
    digest.update(b"CCOS-LEGACY-ARCHIVE-v1\0")
    for record in files:
        digest.update(_canonical_json(record))
        digest.update(b"\0")
    state_file = source / "case-state.json"
    cases: dict[str, Any] = {}
    store_revision: int | None = None
    if state_file.is_file():
        try:
            document = json.loads(state_file.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LegacyError(f"legacy case-state.json is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(document, Mapping):
            raise LegacyError("legacy case-state.json must be an object")
        raw_cases = document.get("cases", {})
        if not isinstance(raw_cases, Mapping):
            raise LegacyError("legacy cases must be an object")
        cases = {str(key): value for key, value in raw_cases.items()}
        raw_revision = document.get("revision")
        store_revision = raw_revision if isinstance(raw_revision, int) else None
    case_index = []
    for case_id, record in sorted(cases.items()):
        if not isinstance(record, Mapping):
            raise LegacyError(f"legacy case is not an object: {case_id}")
        record_case_id = str(record.get("case_id", case_id))
        if record_case_id != case_id:
            raise LegacyError(f"legacy case key and embedded ID differ: {case_id}")
        state = str(record.get("state", "UNKNOWN"))
        case_index.append(
            {
                "case_id": case_id,
                "last_state": state,
                "case_revision": record.get("revision"),
                "classification": TERMINAL if state in LEGACY_TERMINAL_STATES else UNRESOLVED,
                "record_sha256": _sha(_canonical_json(record)),
                "evidence_count": len(record.get("events", {}))
                if isinstance(record.get("events"), Mapping)
                else 0,
            }
        )
    result = {
        "protocol_version": ARCHIVE_PROTOCOL,
        "source_root": str(source),
        "files": files,
        "source_digest": digest.hexdigest(),
        "legacy_store_revision": store_revision,
        "case_count": len(case_index),
        "cases": case_index,
    }
    result["inspection_digest"] = _sha(_canonical_json(result))
    return result


@dataclass(frozen=True, slots=True)
class LegacyArchiveResult:
    archive_id: str
    archive_root: str
    source_root: str
    source_digest: str
    manifest_digest: str
    case_count: int
    unresolved_count: int
    verified: bool
    replayed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _make_read_only(path: Path) -> None:
    for item in sorted(path.rglob("*"), reverse=True):
        if item.is_file():
            item.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    path.chmod(stat.S_IREAD | stat.S_IEXEC)


def archive_legacy_root(
    root: str | Path,
    *,
    state_root: str | Path,
    store: LegacyStore | None = None,
) -> LegacyArchiveResult:
    inspection = inspect_legacy_root(root)
    source = Path(str(inspection["source_root"]))
    destination_base = Path(state_root).expanduser().resolve(strict=False) / "legacy-archives"
    archive_id = f"legacy-{inspection['source_digest'][:24]}"
    destination = destination_base / archive_id
    manifest_path = destination / "archive-manifest.json"
    if destination.exists():
        verification = verify_legacy_archive(destination)
        if verification["source_digest"] != inspection["source_digest"]:
            raise LegacyError("existing archive ID is bound to different source bytes")
        return LegacyArchiveResult(
            archive_id=archive_id,
            archive_root=str(destination),
            source_root=str(source),
            source_digest=str(inspection["source_digest"]),
            manifest_digest=str(verification["manifest_digest"]),
            case_count=int(inspection["case_count"]),
            unresolved_count=sum(
                1 for case in inspection["cases"] if case["classification"] == UNRESOLVED
            ),
            verified=True,
            replayed=True,
        )
    destination_base.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{archive_id}.", dir=destination_base))
    try:
        raw_root = staging / "raw"
        raw_root.mkdir()
        for record in inspection["files"]:
            relative = _safe_archive_relative(record["path"], field="source path")
            source_path = source / relative
            target_path = raw_root / relative
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
            if (
                target_path.stat().st_size != record["size"]
                or _file_sha(target_path) != record["sha256"]
            ):
                raise LegacyError(f"legacy archive copy verification failed: {relative}")
        cases_document: Mapping[str, Any] = {}
        state_file = source / "case-state.json"
        if state_file.is_file():
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            cases_document = loaded.get("cases", {})
        case_records = []
        for case in inspection["cases"]:
            case_id = str(case["case_id"])
            record = cases_document[case_id]
            record_bytes = _canonical_json(record) + b"\n"
            case_filename = f"case-{_sha(case_id.encode('utf-8'))}.json"
            case_path = staging / "cases" / case_filename
            _atomic_write(case_path, record_bytes)
            if _sha(_canonical_json(record)) != case["record_sha256"]:
                raise LegacyError(f"legacy case changed while archiving: {case_id}")
            retained = {
                **case,
                "archive_record": case_path.relative_to(staging).as_posix(),
                "retained_evidence": True,
                "translated_outcome": None,
            }
            case_records.append(retained)
            if store is not None:
                store.record_legacy_archive(
                    archive_id=f"{archive_id}:{case_id}",
                    source_path=str(source),
                    digest=str(case["record_sha256"]),
                    last_state=str(case["last_state"]),
                    classification=str(case["classification"]),
                    evidence=retained,
                )
        manifest = {
            "protocol_version": ARCHIVE_PROTOCOL,
            "archive_id": archive_id,
            "source_root": str(source),
            "source_digest": inspection["source_digest"],
            "legacy_store_revision": inspection["legacy_store_revision"],
            "files": inspection["files"],
            "cases": case_records,
            "activation": "DENIED",
            "legacy_mutation": "ABSENT",
        }
        manifest["manifest_digest"] = _sha(_canonical_json(manifest))
        _atomic_write(staging / "archive-manifest.json", _canonical_json(manifest) + b"\n")
        verification = verify_legacy_archive(staging)
        if not verification["verified"]:
            raise LegacyError("staged legacy archive did not verify")
        os.replace(staging, destination)
        _make_read_only(destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    verification = verify_legacy_archive(destination)
    return LegacyArchiveResult(
        archive_id=archive_id,
        archive_root=str(destination),
        source_root=str(source),
        source_digest=str(inspection["source_digest"]),
        manifest_digest=str(verification["manifest_digest"]),
        case_count=int(inspection["case_count"]),
        unresolved_count=sum(
            1 for case in inspection["cases"] if case["classification"] == UNRESOLVED
        ),
        verified=True,
        replayed=False,
    )


def verify_legacy_archive(root: str | Path) -> dict[str, Any]:
    archive = Path(root).expanduser().resolve(strict=True)
    manifest_path = archive / "archive-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyError(f"cannot read legacy archive manifest: {exc}") from exc
    if not isinstance(manifest, Mapping) or manifest.get("protocol_version") != ARCHIVE_PROTOCOL:
        raise LegacyError("legacy archive protocol is invalid")
    expected_manifest_digest = str(manifest.get("manifest_digest", ""))
    without_digest = dict(manifest)
    without_digest.pop("manifest_digest", None)
    if _sha(_canonical_json(without_digest)) != expected_manifest_digest:
        raise LegacyError("legacy archive manifest digest is invalid")
    source_digest = hashlib.sha256()
    source_digest.update(b"CCOS-LEGACY-ARCHIVE-v1\0")
    for record in manifest.get("files", []):
        if not isinstance(record, Mapping):
            raise LegacyError("legacy archive file entry is malformed")
        relative = _safe_archive_relative(record.get("path", ""), field="source path")
        target = archive / "raw" / relative
        if (
            not target.is_file()
            or target.stat().st_size != record.get("size")
            or _file_sha(target) != record.get("sha256")
        ):
            raise LegacyError(f"legacy archive file verification failed: {relative}")
        source_digest.update(_canonical_json(dict(record)))
        source_digest.update(b"\0")
    if source_digest.hexdigest() != manifest.get("source_digest"):
        raise LegacyError("legacy archive source digest is invalid")
    for case in manifest.get("cases", []):
        if not isinstance(case, Mapping):
            raise LegacyError("legacy archive case entry is malformed")
        relative = _safe_archive_relative(case["archive_record"], field="case record")
        record_path = archive / relative
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if _sha(_canonical_json(record)) != case.get("record_sha256"):
            raise LegacyError(f"legacy archived case digest is invalid: {case.get('case_id')}")
        if case.get("translated_outcome") is not None:
            raise LegacyError("legacy archive illegally translated a legacy outcome")
    return {
        "protocol_version": ARCHIVE_PROTOCOL,
        "archive_root": str(archive),
        "source_digest": manifest["source_digest"],
        "manifest_digest": expected_manifest_digest,
        "case_count": len(manifest.get("cases", [])),
        "verified": True,
    }


def inspect_legacy_case(root: str | Path, case_id: str) -> dict[str, Any]:
    source = Path(root).expanduser().resolve(strict=True)
    document = json.loads((source / "case-state.json").read_text(encoding="utf-8"))
    cases = document.get("cases", {})
    if not isinstance(cases, Mapping) or case_id not in cases:
        raise LegacyError(f"legacy case does not exist: {case_id}")
    record = cases[case_id]
    if not isinstance(record, Mapping):
        raise LegacyError("legacy case record is malformed")
    state = str(record.get("state", "UNKNOWN"))
    return {
        "protocol_version": ARCHIVE_PROTOCOL,
        "case_id": case_id,
        "last_state": state,
        "classification": TERMINAL if state in LEGACY_TERMINAL_STATES else UNRESOLVED,
        "record_sha256": _sha(_canonical_json(record)),
        "record": record,
        "read_only": True,
        "translated_outcome": None,
    }
