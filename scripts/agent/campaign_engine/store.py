"""Durable SQLite store for the single campaign lifecycle authority."""

from __future__ import annotations

import base64
import binascii
from contextlib import closing
import fnmatch
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from .admission import ScopeOverlapError, scopes_overlap
from .ed25519 import verify as verify_ed25519_signature
from .model import (
    Actor,
    ActorRole,
    AuthorityError,
    BudgetError,
    BudgetToken,
    CampaignSnapshot,
    CampaignSpec,
    CampaignState,
    EffectKind,
    EffectState,
    Event,
    EventType,
    Evidence,
    EvidenceKind,
    ExternalEffectIntent,
    Finding,
    Lease,
    LeaseState,
    NodeState,
    RequestConflict,
    RevisionConflict,
    StoreError,
    TransitionError,
    canonical_json,
    canonical_json_digest,
)
from .reducer import reduce


SCHEMA_VERSION = 1


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        migration_digest TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        campaign_id TEXT PRIMARY KEY,
        specification_revision INTEGER NOT NULL,
        specification_digest TEXT NOT NULL,
        spec_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        state TEXT NOT NULL,
        store_revision INTEGER NOT NULL CHECK(store_revision >= 0),
        authority_epoch INTEGER NOT NULL CHECK(authority_epoch >= 0),
        cancellation_epoch INTEGER NOT NULL CHECK(cancellation_epoch >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nodes (
        campaign_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        state TEXT NOT NULL,
        fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
        snapshot_json TEXT NOT NULL,
        PRIMARY KEY (campaign_id, node_id),
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dependencies (
        campaign_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        depends_on_node_id TEXT NOT NULL,
        PRIMARY KEY (campaign_id, node_id, depends_on_node_id),
        FOREIGN KEY (campaign_id, node_id) REFERENCES nodes(campaign_id, node_id),
        FOREIGN KEY (campaign_id, depends_on_node_id) REFERENCES nodes(campaign_id, node_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actors (
        actor_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        node_id TEXT,
        role TEXT NOT NULL,
        native_thread_id TEXT NOT NULL,
        identity_digest TEXT,
        native_identity_json TEXT NOT NULL DEFAULT '{}',
        host_pid INTEGER,
        authority_epoch INTEGER NOT NULL,
        can_write INTEGER NOT NULL CHECK(can_write IN (0, 1)),
        bound_request_id TEXT,
        used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0, 1)),
        actor_json TEXT NOT NULL,
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS actors_native_thread_once
    ON actors(native_thread_id)
    WHERE native_thread_id <> 'UNBOUND'
    """,
    """
    CREATE TABLE IF NOT EXISTS leases (
        lease_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        resource_key TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
        cancellation_epoch INTEGER NOT NULL CHECK(cancellation_epoch >= 0),
        state TEXT NOT NULL,
        expires_at TEXT,
        lease_json TEXT NOT NULL,
        FOREIGN KEY (campaign_id, node_id) REFERENCES nodes(campaign_id, node_id),
        FOREIGN KEY (actor_id) REFERENCES actors(actor_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS one_active_lease_per_resource
    ON leases(resource_key) WHERE state = 'ACTIVE'
    """,
    """
    CREATE TABLE IF NOT EXISTS operations (
        request_id TEXT PRIMARY KEY,
        campaign_id TEXT,
        kind TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json TEXT,
        created_revision INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_effect_outbox (
        operation_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        node_id TEXT,
        kind TEXT NOT NULL,
        state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        authority_epoch INTEGER NOT NULL,
        cancellation_epoch INTEGER NOT NULL,
        fencing_epoch INTEGER,
        created_revision INTEGER NOT NULL,
        updated_revision INTEGER NOT NULL,
        result_json TEXT,
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        node_id TEXT,
        kind TEXT NOT NULL,
        digest TEXT NOT NULL,
        candidate_head TEXT,
        payload_json TEXT NOT NULL,
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reviews (
        review_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        cohort_digest TEXT NOT NULL,
        candidate_head TEXT,
        state TEXT NOT NULL,
        receipt_json TEXT,
        FOREIGN KEY (campaign_id, node_id) REFERENCES nodes(campaign_id, node_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        campaign_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        finding_id TEXT NOT NULL,
        origin TEXT NOT NULL,
        blocking INTEGER NOT NULL CHECK(blocking IN (0, 1)),
        resolved INTEGER NOT NULL CHECK(resolved IN (0, 1)),
        finding_json TEXT NOT NULL,
        PRIMARY KEY (campaign_id, node_id, finding_id, origin),
        FOREIGN KEY (campaign_id, node_id) REFERENCES nodes(campaign_id, node_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resource_locks (
        resource_key TEXT PRIMARY KEY,
        fencing_epoch INTEGER NOT NULL CHECK(fencing_epoch >= 0),
        lease_id TEXT,
        campaign_id TEXT,
        node_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        event_json TEXT NOT NULL,
        telemetry_category TEXT,
        UNIQUE (campaign_id, revision),
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telemetry (
        telemetry_id TEXT PRIMARY KEY,
        campaign_id TEXT,
        category TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_installations (
        installation_id TEXT PRIMARY KEY,
        source_commit TEXT NOT NULL,
        bundle_digest TEXT NOT NULL,
        install_transaction TEXT NOT NULL,
        protocol_version TEXT NOT NULL,
        schema_compatibility TEXT NOT NULL,
        host_capability_probe_version TEXT NOT NULL,
        installation_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS legacy_archives (
        archive_id TEXT PRIMARY KEY,
        source_path TEXT NOT NULL,
        digest TEXT NOT NULL,
        last_state TEXT NOT NULL,
        classification TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        archive_json TEXT NOT NULL
    )
    """,
)


EFFECT_TRANSITIONS: dict[EffectState, set[EffectState]] = {
    EffectState.PREPARED: {EffectState.EXECUTING, EffectState.CANCELLED},
    EffectState.EXECUTING: {
        EffectState.CONFIRMED,
        EffectState.FAILED,
        EffectState.AMBIGUOUS,
        EffectState.CANCELLED,
    },
    EffectState.AMBIGUOUS: {
        EffectState.CONFIRMED,
        EffectState.FAILED,
        EffectState.CANCELLED,
    },
    EffectState.CONFIRMED: set(),
    EffectState.FAILED: set(),
    EffectState.CANCELLED: set(),
}

READ_ONLY_ACTOR_ROLES = {
    ActorRole.REVIEWER,
    ActorRole.CLOSURE_REVIEWER,
    ActorRole.VALIDATOR,
    ActorRole.SUPERVISOR,
    ActorRole.PARENT,
}
CONCURRENT_READ_ONLY_ACTOR_ROLES = READ_ONLY_ACTOR_ROLES - {ActorRole.VALIDATOR}


class CampaignStore:
    """SQLite authority with compare-and-swap lifecycle transitions."""

    def __init__(self, path: str | Path, *, timeout_seconds: float = 10.0) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.timeout_seconds = timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def __enter__(self) -> "CampaignStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Connections are operation-scoped, so close is intentionally a no-op."""

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}")
        return connection

    def _initialize(self) -> None:
        existed = self.path.exists() and self.path.stat().st_size > 0
        with closing(self._connect()) as connection:
            if existed:
                self._assert_integrity(connection)
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise StoreError(
                    f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            while current < SCHEMA_VERSION:
                self._backup_connection(connection, f"pre-migration-v{current}-to-v{current + 1}")
                self._apply_migration(connection, current + 1)
                current += 1
            self._assert_integrity(connection)

    def _apply_migration(self, connection: sqlite3.Connection, version: int) -> None:
        if version != 1:
            raise StoreError(f"no migration implementation for version {version}")
        digest = canonical_json_digest([statement.strip() for statement in SCHEMA_STATEMENTS])
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, migration_digest) VALUES (?, ?)",
                (version, digest),
            )
            connection.execute(f"PRAGMA user_version={version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _next_backup_path(self, label: str) -> Path:
        base = self.path.with_name(f"{self.path.name}.{label}.bak")
        if not base.exists():
            return base
        counter = 1
        while True:
            candidate = self.path.with_name(f"{self.path.name}.{label}.{counter}.bak")
            if not candidate.exists():
                return candidate
            counter += 1

    def _backup_connection(self, connection: sqlite3.Connection, label: str) -> Path:
        destination = self._next_backup_path(label)
        with closing(sqlite3.connect(destination)) as backup:
            connection.backup(backup)
        return destination

    @staticmethod
    def _assert_integrity(connection: sqlite3.Connection) -> None:
        rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if rows != ["ok"]:
            raise StoreError("SQLite integrity check failed: " + "; ".join(rows))
        foreign_key_rows = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if foreign_key_rows:
            raise StoreError(
                "SQLite foreign-key check failed: "
                + "; ".join(repr(row) for row in foreign_key_rows[:20])
            )
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current:
            expected = {
                1: canonical_json_digest(
                    [statement.strip() for statement in SCHEMA_STATEMENTS]
                )
            }
            try:
                migration_rows = {
                    int(row["version"]): str(row["migration_digest"])
                    for row in connection.execute(
                        "SELECT version, migration_digest FROM schema_migrations"
                    )
                }
            except sqlite3.Error as exc:
                raise StoreError("SQLite migration provenance is unavailable") from exc
            required = {version: expected[version] for version in range(1, current + 1)}
            if migration_rows != required:
                raise StoreError("SQLite migration provenance digest mismatch")

    def integrity_check(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            self._assert_integrity(connection)
            return {
                "status": "ok",
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                "schema_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            }

    def migrate(self) -> dict[str, Any]:
        self._initialize()
        return self.integrity_check()

    def backup(self, destination: str | Path | None = None) -> Path:
        target = (
            Path(destination).expanduser().resolve(strict=False)
            if destination is not None
            else self._next_backup_path("manual")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as source, closing(sqlite3.connect(target)) as backup:
            source.backup(backup)
        return target

    @staticmethod
    def _load_snapshot(connection: sqlite3.Connection, campaign_id: str) -> CampaignSnapshot:
        row = connection.execute(
            "SELECT snapshot_json FROM campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown campaign: {campaign_id}")
        return CampaignSnapshot.from_dict(json.loads(str(row["snapshot_json"])))

    @staticmethod
    def _operation_result(
        connection: sqlite3.Connection, request_id: str, payload_digest: str
    ) -> Mapping[str, Any] | None:
        row = connection.execute(
            "SELECT payload_digest, status, result_json FROM operations WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_digest"]) != payload_digest:
            raise RequestConflict(
                "request identifier is already bound to a different payload digest"
            )
        if str(row["status"]) != "CONFIRMED" or row["result_json"] is None:
            raise StoreError(f"request is already in state {row['status']}")
        return json.loads(str(row["result_json"]))

    @staticmethod
    def _begin_operation(
        connection: sqlite3.Connection,
        *,
        request_id: str,
        campaign_id: str | None,
        kind: str,
        payload_digest: str,
        revision: int | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO operations(
                request_id, campaign_id, kind, payload_digest, status, created_revision
            ) VALUES (?, ?, ?, ?, 'EXECUTING', ?)
            """,
            (request_id, campaign_id, kind, payload_digest, revision),
        )

    @staticmethod
    def _finish_operation(
        connection: sqlite3.Connection, request_id: str, result: Mapping[str, Any]
    ) -> None:
        connection.execute(
            "UPDATE operations SET status='CONFIRMED', result_json=? WHERE request_id=?",
            (canonical_json(result), request_id),
        )

    @staticmethod
    def _sync_snapshot(connection: sqlite3.Connection, snapshot: CampaignSnapshot) -> None:
        campaign_id = snapshot.spec.campaign_id
        for node in snapshot.nodes:
            connection.execute(
                """
                INSERT INTO nodes(campaign_id, node_id, state, fencing_epoch, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, node_id) DO UPDATE SET
                    state=excluded.state,
                    fencing_epoch=excluded.fencing_epoch,
                    snapshot_json=excluded.snapshot_json
                """,
                (
                    campaign_id,
                    node.node_id,
                    node.state.value,
                    node.fencing_epoch,
                    canonical_json(node.to_dict()),
                ),
            )
        connection.execute("DELETE FROM findings WHERE campaign_id=?", (campaign_id,))
        for node in snapshot.nodes:
            resolved = set(node.resolved_finding_ids)
            for finding in node.findings + node.closure_findings:
                connection.execute(
                    """
                    INSERT INTO findings(
                        campaign_id, node_id, finding_id, origin, blocking, resolved, finding_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign_id,
                        node.node_id,
                        finding.finding_id,
                        finding.origin.value,
                        int(finding.blocking),
                        int(finding.finding_id in resolved),
                        canonical_json(finding.to_dict()),
                    ),
                )

    @staticmethod
    def _telemetry_category(snapshot: CampaignSnapshot) -> str | None:
        if snapshot.state is CampaignState.COMPLETED:
            return "completed"
        if snapshot.state is CampaignState.FAILED:
            return "failed"
        if snapshot.state is CampaignState.CANCELLED:
            return "stopped"
        return None

    def create_campaign(
        self,
        spec: CampaignSpec | Mapping[str, Any],
        *,
        request_id: str | None = None,
        payload_digest: str | None = None,
    ) -> CampaignSnapshot:
        campaign_spec = spec if isinstance(spec, CampaignSpec) else CampaignSpec.from_dict(spec)
        campaign_spec.verify_digest()
        snapshot = CampaignSnapshot.initial(campaign_spec)
        request_id = request_id or (
            f"create:{campaign_spec.campaign_id}:{campaign_spec.specification_digest}"
        )
        computed_digest = canonical_json_digest(campaign_spec.to_dict())
        if payload_digest is not None and payload_digest != computed_digest:
            raise RequestConflict("declared payload digest differs from campaign specification")
        payload_digest = computed_digest
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                prior = self._operation_result(connection, request_id, payload_digest)
                if prior is not None:
                    connection.commit()
                    return CampaignSnapshot.from_dict(prior["snapshot"])
                existing = connection.execute(
                    "SELECT specification_digest, snapshot_json FROM campaigns WHERE campaign_id=?",
                    (campaign_spec.campaign_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["specification_digest"]) != campaign_spec.specification_digest:
                        raise StoreError("campaign identifier already owns another specification")
                    result = {"snapshot": json.loads(str(existing["snapshot_json"])), "effects": []}
                    self._begin_operation(
                        connection,
                        request_id=request_id,
                        campaign_id=campaign_spec.campaign_id,
                        kind="CREATE_CAMPAIGN",
                        payload_digest=payload_digest,
                        revision=snapshot.revision,
                    )
                    self._finish_operation(connection, request_id, result)
                    connection.commit()
                    return CampaignSnapshot.from_dict(result["snapshot"])
                active_rows = connection.execute(
                    "SELECT campaign_id, spec_json FROM campaigns "
                    "WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')"
                ).fetchall()
                proposed_worktree = str(
                    Path(campaign_spec.worktree).resolve(strict=False)
                ).casefold()
                for active_row in active_rows:
                    active_spec = CampaignSpec.from_dict(
                        json.loads(str(active_row["spec_json"]))
                    )
                    active_worktree = str(
                        Path(active_spec.worktree).resolve(strict=False)
                    ).casefold()
                    if active_worktree == proposed_worktree and scopes_overlap(
                        campaign_spec.allowed_paths, active_spec.allowed_paths
                    ):
                        raise ScopeOverlapError(
                            "allowed scope overlaps active campaign/resource: "
                            f"{active_row['campaign_id']}"
                        )
                self._begin_operation(
                    connection,
                    request_id=request_id,
                    campaign_id=campaign_spec.campaign_id,
                    kind="CREATE_CAMPAIGN",
                    payload_digest=payload_digest,
                    revision=0,
                )
                connection.execute(
                    """
                    INSERT INTO campaigns(
                        campaign_id, specification_revision, specification_digest,
                        spec_json, snapshot_json, state, store_revision,
                        authority_epoch, cancellation_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign_spec.campaign_id,
                        campaign_spec.specification_revision,
                        campaign_spec.specification_digest,
                        canonical_json(campaign_spec.to_dict()),
                        canonical_json(snapshot.to_dict()),
                        snapshot.state.value,
                        snapshot.revision,
                        snapshot.authority_epoch,
                        snapshot.cancellation_epoch,
                    ),
                )
                self._sync_snapshot(connection, snapshot)
                for node_spec in campaign_spec.nodes:
                    for dependency in node_spec.dependencies:
                        connection.execute(
                            """
                            INSERT INTO dependencies(campaign_id, node_id, depends_on_node_id)
                            VALUES (?, ?, ?)
                            """,
                            (campaign_spec.campaign_id, node_spec.node_id, dependency),
                        )
                result = {"snapshot": snapshot.to_dict(), "effects": []}
                self._finish_operation(connection, request_id, result)
                connection.commit()
                return snapshot
            except Exception:
                connection.rollback()
                raise

    def get_snapshot(self, campaign_id: str) -> CampaignSnapshot:
        with closing(self._connect()) as connection:
            return self._load_snapshot(connection, campaign_id)

    def list_campaigns(self, repository_root: str | None = None) -> list[CampaignSnapshot]:
        with closing(self._connect()) as connection:
            snapshots = [
                CampaignSnapshot.from_dict(json.loads(str(row["snapshot_json"])))
                for row in connection.execute(
                    "SELECT snapshot_json FROM campaigns ORDER BY campaign_id"
                )
            ]
        if repository_root is None:
            return snapshots
        requested = str(Path(repository_root).expanduser().resolve(strict=False)).casefold()
        return [
            snapshot
            for snapshot in snapshots
            if requested
            in {
                str(Path(snapshot.spec.git_root).expanduser().resolve(strict=False)).casefold(),
                str(Path(snapshot.spec.worktree).expanduser().resolve(strict=False)).casefold(),
            }
        ]

    def _persist_transition(
        self,
        connection: sqlite3.Connection,
        event: Event,
        next_snapshot: CampaignSnapshot,
        effects: tuple[ExternalEffectIntent, ...],
        payload_digest: str,
    ) -> None:
        changed = connection.execute(
            """
            UPDATE campaigns SET
                snapshot_json=?, state=?, store_revision=?, authority_epoch=?, cancellation_epoch=?
            WHERE campaign_id=? AND store_revision=?
            """,
            (
                canonical_json(next_snapshot.to_dict()),
                next_snapshot.state.value,
                next_snapshot.revision,
                next_snapshot.authority_epoch,
                next_snapshot.cancellation_epoch,
                event.campaign_id,
                event.expected_revision,
            ),
        ).rowcount
        if changed != 1:
            raise RevisionConflict("compare-and-swap revision update failed")
        self._sync_snapshot(connection, next_snapshot)
        category = self._telemetry_category(next_snapshot)
        connection.execute(
            """
            INSERT INTO events(
                event_id, campaign_id, revision, event_type, payload_digest,
                event_json, telemetry_category
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.campaign_id,
                next_snapshot.revision,
                event.event_type.value,
                payload_digest,
                canonical_json(event.to_dict()),
                category,
            ),
        )
        if category:
            connection.execute(
                "INSERT OR IGNORE INTO telemetry(telemetry_id, campaign_id, category, payload_json) VALUES (?, ?, ?, ?)",
                (
                    f"{event.event_id}:{category}",
                    event.campaign_id,
                    category,
                    canonical_json({"revision": next_snapshot.revision}),
                ),
            )
        for intent in effects:
            if intent.external:
                self._insert_effect(connection, intent, next_snapshot.revision)
        if event.event_type is EventType.START_REVIEW:
            review_id = str(event.payload.get("review_id", event.event_id))
            cohort = tuple(event.payload.get("review_cohort", ()))
            node = next_snapshot.node(str(event.node_id))
            connection.execute(
                """
                INSERT INTO reviews(
                    review_id, campaign_id, node_id, cohort_digest, candidate_head, state
                ) VALUES (?, ?, ?, ?, ?, 'DISPATCHED')
                """,
                (
                    review_id,
                    event.campaign_id,
                    event.node_id,
                    canonical_json_digest(cohort),
                    node.candidate_head,
                ),
            )
        if event.event_type is EventType.CANCEL:
            connection.execute(
                "UPDATE leases SET state='INVALIDATED' WHERE campaign_id=? AND state='ACTIVE'",
                (event.campaign_id,),
            )
            connection.execute(
                "UPDATE resource_locks SET lease_id=NULL WHERE campaign_id=?",
                (event.campaign_id,),
            )
            connection.execute(
                "UPDATE external_effect_outbox SET state='CANCELLED', updated_revision=? "
                "WHERE campaign_id=? AND state='PREPARED'",
                (next_snapshot.revision, event.campaign_id),
            )
            connection.execute(
                "UPDATE external_effect_outbox SET state='AMBIGUOUS', updated_revision=? "
                "WHERE campaign_id=? AND state='EXECUTING'",
                (next_snapshot.revision, event.campaign_id),
            )
        elif next_snapshot.state is CampaignState.FAILED:
            connection.execute(
                "UPDATE leases SET state='INVALIDATED' WHERE campaign_id=? AND state='ACTIVE'",
                (event.campaign_id,),
            )
            connection.execute(
                "UPDATE resource_locks SET lease_id=NULL WHERE campaign_id=?",
                (event.campaign_id,),
            )

    @staticmethod
    def _insert_effect(
        connection: sqlite3.Connection,
        intent: ExternalEffectIntent,
        revision: int,
    ) -> None:
        payload_json = canonical_json(intent.payload)
        payload_digest = canonical_json_digest(intent.payload)
        existing = connection.execute(
            "SELECT payload_digest, kind FROM external_effect_outbox WHERE operation_id=?",
            (intent.operation_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["payload_digest"]) != payload_digest
                or str(existing["kind"]) != intent.kind.value
            ):
                raise RequestConflict("effect operation identity was rebound")
            return
        connection.execute(
            """
            INSERT INTO external_effect_outbox(
                operation_id, campaign_id, node_id, kind, state, payload_json,
                payload_digest, authority_epoch, cancellation_epoch, fencing_epoch,
                created_revision, updated_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.operation_id,
                intent.campaign_id,
                intent.node_id,
                intent.kind.value,
                intent.state.value,
                payload_json,
                payload_digest,
                intent.authority_epoch,
                intent.cancellation_epoch,
                intent.fencing_epoch,
                revision,
                revision,
            ),
        )

    def _apply_event_tx(
        self,
        connection: sqlite3.Connection,
        event: Event,
        *,
        operation_digest: str | None = None,
    ) -> tuple[CampaignSnapshot, tuple[ExternalEffectIntent, ...]]:
        payload_digest = operation_digest or canonical_json_digest(event.to_dict())
        prior = self._operation_result(connection, event.event_id, payload_digest)
        if prior is not None:
            return (
                CampaignSnapshot.from_dict(prior["snapshot"]),
                tuple(ExternalEffectIntent.from_dict(item) for item in prior["effects"]),
            )
        snapshot = self._load_snapshot(connection, event.campaign_id)
        self._begin_operation(
            connection,
            request_id=event.event_id,
            campaign_id=event.campaign_id,
            kind=event.event_type.value,
            payload_digest=payload_digest,
            revision=event.expected_revision,
        )
        next_snapshot, effects = reduce(snapshot, event)
        self._persist_transition(connection, event, next_snapshot, effects, payload_digest)
        result = {
            "snapshot": next_snapshot.to_dict(),
            "effects": [effect.to_dict() for effect in effects],
        }
        self._finish_operation(connection, event.event_id, result)
        return next_snapshot, effects

    def _record_rejection(self, event: Event, exc: Exception) -> None:
        category = "denied"
        if isinstance(exc, (TransitionError,)) and any(
            phrase in str(exc).lower()
            for phrase in ("already used", "already been used", "generation already")
        ):
            category = "loop_prevented"
        if isinstance(exc, BudgetError):
            category = "loop_prevented"
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO telemetry(telemetry_id, campaign_id, category, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        f"{event.event_id}:{category}",
                        event.campaign_id,
                        category,
                        canonical_json({"event_type": event.event_type.value, "error": str(exc)}),
                    ),
                )
                connection.commit()
        except sqlite3.Error:
            pass

    def apply_event(
        self, event: Event | Mapping[str, Any]
    ) -> tuple[CampaignSnapshot, tuple[ExternalEffectIntent, ...]]:
        campaign_event = event if isinstance(event, Event) else Event.from_dict(event)
        if campaign_event.event_type in {
            EventType.AUTHORIZE_REPAIR,
            EventType.AUTHORIZE_PUBLICATION,
        }:
            raise AuthorityError(
                f"{campaign_event.event_type.value} requires a one-use human "
                "authorization receipt"
            )
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = self._apply_event_tx(connection, campaign_event)
                connection.commit()
                return result
            except Exception as exc:
                connection.rollback()
                self._record_rejection(campaign_event, exc)
                raise

    @staticmethod
    def _verify_human_authorization_signature(
        snapshot: CampaignSnapshot,
        receipt: Mapping[str, Any],
    ) -> None:
        verifier = snapshot.spec.publication_authority.get(
            "human_authorization"
        )
        if not isinstance(verifier, Mapping):
            raise AuthorityError(
                "approved specification has no human authorization verifier"
            )
        if (
            verifier.get("algorithm") != "ED25519"
            or receipt.get("signature_algorithm") != "ED25519"
        ):
            raise AuthorityError(
                "human authorization receipt signature algorithm mismatch"
            )
        encoded_public_key = str(verifier.get("public_key_base64", ""))
        encoded_signature = str(receipt.get("signature_base64", ""))
        try:
            public_key_bytes = base64.b64decode(
                encoded_public_key, validate=True
            )
            signature = base64.b64decode(encoded_signature, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AuthorityError(
                "human authorization receipt signature encoding is invalid"
            ) from exc
        if (
            base64.b64encode(public_key_bytes).decode("ascii")
            != encoded_public_key
            or base64.b64encode(signature).decode("ascii")
            != encoded_signature
        ):
            raise AuthorityError(
                "human authorization receipt signature encoding is invalid"
            )
        if len(public_key_bytes) != 32 or len(signature) != 64:
            raise AuthorityError(
                "human authorization receipt signature dimensions are invalid"
            )
        signed_payload = dict(receipt)
        signed_payload.pop("signature_base64", None)
        if not verify_ed25519_signature(
            public_key_bytes,
            signature,
            canonical_json(signed_payload).encode("utf-8"),
        ):
            raise AuthorityError(
                "human authorization receipt signature is invalid"
            )

    @staticmethod
    def _validate_human_authorization_receipt(
        snapshot: CampaignSnapshot,
        event: Event,
        receipt: Mapping[str, Any],
    ) -> tuple[str, str]:
        expected_kind = {
            EventType.AUTHORIZE_REPAIR: "repair_authorized",
            EventType.AUTHORIZE_PUBLICATION: "publication_authorized",
        }.get(event.event_type)
        if expected_kind is None:
            raise AuthorityError("event is not a human authorization transition")
        if not event.node_id:
            raise AuthorityError("human authorization requires an exact node")
        node = snapshot.node(event.node_id)
        receipt_id = str(receipt.get("receipt_id", "")).strip()
        authorized_by = str(receipt.get("authorized_by", "")).strip()
        if not receipt_id or not authorized_by:
            raise AuthorityError(
                "human authorization receipt requires receipt_id and authorized_by"
            )
        CampaignStore._verify_human_authorization_signature(snapshot, receipt)
        recorded_authorizer = str(
            snapshot.spec.publication_authority.get("authorized_by", "")
        ).strip()
        if not recorded_authorizer or authorized_by != recorded_authorizer:
            raise AuthorityError(
                "human authorization receipt authorized_by differs from recorded authority"
            )
        frozen_blockers = tuple(
            finding.finding_id for finding in node.findings if finding.blocking
        )
        supplied_blockers = tuple(
            str(item) for item in receipt.get("frozen_blocker_ids", ())
        )
        exact_bindings = {
            "event_kind": expected_kind,
            "campaign_id": snapshot.spec.campaign_id,
            "specification_digest": snapshot.spec.specification_digest,
            "specification_revision": snapshot.spec.specification_revision,
            "store_revision": snapshot.revision,
            "authority_epoch": snapshot.authority_epoch,
            "cancellation_epoch": snapshot.cancellation_epoch,
            "node_id": event.node_id,
            "candidate_head": node.candidate_head,
        }
        for key, expected in exact_bindings.items():
            observed = receipt.get(key)
            if isinstance(expected, int):
                try:
                    observed = int(observed)
                except (TypeError, ValueError):
                    pass
            elif expected is not None:
                observed = str(observed or "")
            if observed != expected:
                raise AuthorityError(
                    f"human authorization receipt {key} binding mismatch"
                )
        if supplied_blockers != frozen_blockers:
            raise AuthorityError(
                "human authorization receipt frozen_blocker_ids binding mismatch"
            )
        digest = canonical_json_digest(receipt)
        if (
            event.payload.get("authorization_receipt_id") != receipt_id
            or event.payload.get("authorization_receipt_digest") != digest
        ):
            raise AuthorityError(
                "authorization event does not bind the exact receipt identity and digest"
            )
        return receipt_id, digest

    def apply_human_authorized_event(
        self,
        event: Event | Mapping[str, Any],
        authorization_receipt: Mapping[str, Any],
    ) -> tuple[CampaignSnapshot, tuple[ExternalEffectIntent, ...]]:
        """Atomically consume one human receipt and apply its exact transition.

        Authorization is intentionally non-idempotent. A receipt that already
        exists in ``operations`` cannot be replayed, even with the same payload.
        The lifecycle event and receipt consumption commit in one
        ``BEGIN IMMEDIATE`` transaction.
        """

        campaign_event = event if isinstance(event, Event) else Event.from_dict(event)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                snapshot = self._load_snapshot(connection, campaign_event.campaign_id)
                receipt_id = str(
                    authorization_receipt.get("receipt_id", "")
                ).strip()
                receipt_digest = canonical_json_digest(authorization_receipt)
                if not receipt_id:
                    raise AuthorityError(
                        "human authorization receipt requires receipt_id"
                    )
                existing = connection.execute(
                    "SELECT payload_digest FROM operations WHERE request_id=?",
                    (receipt_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_digest"]) != receipt_digest:
                        raise RequestConflict(
                            "human authorization receipt identifier was rebound"
                        )
                    raise AuthorityError(
                        "human authorization receipt was already consumed"
                    )
                validated_id, validated_digest = (
                    self._validate_human_authorization_receipt(
                        snapshot, campaign_event, authorization_receipt
                    )
                )
                if validated_id != receipt_id or validated_digest != receipt_digest:
                    raise AuthorityError(
                        "human authorization receipt canonical identity changed"
                    )
                self._begin_operation(
                    connection,
                    request_id=receipt_id,
                    campaign_id=campaign_event.campaign_id,
                    kind=f"HUMAN_AUTHORIZATION:{campaign_event.event_type.value}",
                    payload_digest=receipt_digest,
                    revision=snapshot.revision,
                )
                next_snapshot, effects = self._apply_event_tx(
                    connection, campaign_event
                )
                self._finish_operation(
                    connection,
                    receipt_id,
                    {
                        "authorization_receipt_digest": receipt_digest,
                        "event_id": campaign_event.event_id,
                        "revision": next_snapshot.revision,
                    },
                )
                connection.commit()
                return next_snapshot, effects
            except Exception as exc:
                connection.rollback()
                self._record_rejection(campaign_event, exc)
                raise

    def consume_budget(
        self,
        campaign_id: str,
        token: BudgetToken | str,
        *,
        request_id: str,
        expected_revision: int,
        authority_epoch: int,
        cancellation_epoch: int,
        node_id: str | None = None,
        fencing_epoch: int | None = None,
    ) -> CampaignSnapshot:
        event = Event(
            event_id=request_id,
            campaign_id=campaign_id,
            event_type=EventType.CONSUME_BUDGET,
            expected_revision=expected_revision,
            authority_epoch=authority_epoch,
            cancellation_epoch=cancellation_epoch,
            node_id=node_id,
            fencing_epoch=fencing_epoch,
            payload={
                "token": (token if isinstance(token, BudgetToken) else BudgetToken(str(token))).value
            },
        )
        return self.apply_event(event)[0]

    def reserve_budget_attempt(
        self,
        campaign_id: str,
        token: BudgetToken | str,
        *,
        request_id: str,
        expected_revision: int,
        authority_epoch: int,
        cancellation_epoch: int,
        node_id: str | None = None,
        fencing_epoch: int | None = None,
    ) -> tuple[CampaignSnapshot, bool]:
        """Consume one durable token for one exact attempt identity.

        ``consume_budget`` is idempotent, but its historical return shape cannot
        tell a caller whether it created the reservation or replayed one.  A
        retry/no-op caller must know that distinction before touching a host or
        an external system.  This method makes the distinction inside the same
        ``BEGIN IMMEDIATE`` transaction that consumes the token, so concurrent
        supervisors cannot both execute one reserved attempt.

        The request payload still includes the exact expected revision and
        epochs.  Reusing the request identifier at another revision therefore
        fails with ``RequestConflict`` instead of silently creating a fresh
        attempt.
        """

        budget_token = token if isinstance(token, BudgetToken) else BudgetToken(str(token))
        event = Event(
            event_id=request_id,
            campaign_id=campaign_id,
            event_type=EventType.CONSUME_BUDGET,
            expected_revision=expected_revision,
            authority_epoch=authority_epoch,
            cancellation_epoch=cancellation_epoch,
            node_id=node_id,
            fencing_epoch=fencing_epoch,
            payload={"token": budget_token.value},
        )
        payload_digest = canonical_json_digest(event.to_dict())
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT 1 FROM operations WHERE request_id=?", (request_id,)
                ).fetchone()
                if existing is not None:
                    self._operation_result(connection, request_id, payload_digest)
                    current = self._load_snapshot(connection, campaign_id)
                    connection.commit()
                    return current, False
                next_snapshot, _ = self._apply_event_tx(connection, event)
                connection.commit()
                return next_snapshot, True
            except Exception as exc:
                connection.rollback()
                self._record_rejection(event, exc)
                raise

    def record_runtime_operation(
        self,
        *,
        request_id: str,
        campaign_id: str,
        kind: str,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Persist one idempotent non-lifecycle runtime receipt.

        Transport-failure and retry receipts use the same one-request/one-
        payload binding as mutations, but they do not alter campaign state or
        claim lifecycle authority.
        """

        payload_digest = canonical_json_digest(payload)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = self._operation_result(connection, request_id, payload_digest)
            if prior is not None:
                connection.commit()
                return dict(prior)
            self._begin_operation(
                connection,
                request_id=request_id,
                campaign_id=campaign_id,
                kind=str(kind),
                payload_digest=payload_digest,
                revision=revision,
            )
            recorded = {"payload": dict(payload), "result": dict(result)}
            self._finish_operation(connection, request_id, recorded)
            connection.commit()
            return recorded

    def get_runtime_operation(self, request_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT campaign_id, kind, payload_digest, status, result_json, created_revision "
                "FROM operations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            result = json.loads(str(row["result_json"])) if row["result_json"] else None
            return {
                "request_id": request_id,
                "campaign_id": row["campaign_id"],
                "kind": str(row["kind"]),
                "payload_digest": str(row["payload_digest"]),
                "status": str(row["status"]),
                "result": result,
                "created_revision": row["created_revision"],
            }

    @staticmethod
    def _insert_actor_record(connection: sqlite3.Connection, actor: Actor) -> None:
        actor_json = canonical_json(actor.to_dict())
        existing = connection.execute(
            "SELECT actor_json FROM actors WHERE actor_id=?", (actor.actor_id,)
        ).fetchone()
        if existing is not None:
            if str(existing["actor_json"]) != actor_json:
                raise RequestConflict("actor identifier was rebound")
            return
        connection.execute(
            """
            INSERT INTO actors(
                actor_id, campaign_id, node_id, role, native_thread_id,
                native_identity_json, host_pid, authority_epoch, can_write, actor_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor.actor_id,
                actor.campaign_id,
                actor.node_id,
                actor.role.value,
                actor.native_thread_id,
                canonical_json(actor.native_identity),
                actor.host_pid,
                actor.authority_epoch,
                int(actor.can_write),
                actor_json,
            ),
        )

    def acquire_lease(
        self,
        campaign_id: str,
        node_id: str,
        resource_key: str,
        actor: Actor,
        *,
        lease_id: str,
        request_id: str,
        expected_revision: int,
        authority_epoch: int,
        cancellation_epoch: int,
        expires_at: str | None = None,
    ) -> Lease:
        request_payload = {
            "campaign_id": campaign_id,
            "node_id": node_id,
            "resource_key": resource_key,
            "actor": actor.to_dict(),
            "lease_id": lease_id,
            "expected_revision": expected_revision,
            "authority_epoch": authority_epoch,
            "cancellation_epoch": cancellation_epoch,
            "expires_at": expires_at,
        }
        request_digest = canonical_json_digest(request_payload)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                prior = self._operation_result(connection, request_id, request_digest)
                if prior is not None:
                    row = connection.execute(
                        "SELECT lease_json FROM leases WHERE lease_id=?", (lease_id,)
                    ).fetchone()
                    if row is None:
                        raise StoreError("idempotent lease result is missing")
                    connection.commit()
                    return Lease.from_dict(json.loads(str(row["lease_json"])))
                snapshot = self._load_snapshot(connection, campaign_id)
                if actor.campaign_id != campaign_id or actor.node_id != node_id:
                    raise AuthorityError("actor binding differs from lease campaign or node")
                if actor.authority_epoch != authority_epoch:
                    raise AuthorityError("actor authority epoch differs from lease request")
                if snapshot.revision != expected_revision:
                    raise RevisionConflict(
                        f"expected revision {expected_revision}, current {snapshot.revision}"
                    )
                if snapshot.authority_epoch != authority_epoch:
                    raise AuthorityError("lease authority epoch is stale")
                if snapshot.cancellation_epoch != cancellation_epoch:
                    raise AuthorityError("lease cancellation epoch is stale")
                read_only = actor.role in READ_ONLY_ACTOR_ROLES
                concurrent_read_only = actor.role in CONCURRENT_READ_ONLY_ACTOR_ROLES
                if read_only and actor.can_write:
                    raise AuthorityError("reviewer, closure reviewer, and parent leases are read-only")
                if not read_only and not actor.can_write:
                    raise AuthorityError("exclusive worker lease must be write-capable")
                active = connection.execute(
                    "SELECT lease_id FROM leases WHERE resource_key=? AND state='ACTIVE'",
                    (resource_key,),
                ).fetchone()
                if active is not None:
                    raise StoreError(f"resource already has active lease: {resource_key}")
                node_leases = list(
                    connection.execute(
                        """
                        SELECT l.lease_id, a.can_write, a.role
                        FROM leases AS l JOIN actors AS a ON a.actor_id=l.actor_id
                        WHERE l.campaign_id=? AND l.node_id=? AND l.state='ACTIVE'
                        """,
                        (campaign_id, node_id),
                    )
                )
                if concurrent_read_only:
                    if any(
                        int(row["can_write"])
                        or ActorRole(str(row["role"])) is ActorRole.VALIDATOR
                        for row in node_leases
                    ):
                        raise StoreError("concurrent read-only lease conflicts with exclusive actor")
                elif node_leases:
                    raise StoreError("exclusive lifecycle lease conflicts with active node leases")
                lock = connection.execute(
                    "SELECT fencing_epoch FROM resource_locks WHERE resource_key=?",
                    (resource_key,),
                ).fetchone()
                node_snapshot = snapshot.node(node_id)
                if concurrent_read_only:
                    fence = node_snapshot.fencing_epoch
                    self._begin_operation(
                        connection,
                        request_id=request_id,
                        campaign_id=campaign_id,
                        kind="ACQUIRE_READ_ONLY_LEASE",
                        payload_digest=request_digest,
                        revision=expected_revision,
                    )
                    next_snapshot = snapshot
                else:
                    previous_resource_fence = int(lock["fencing_epoch"]) if lock else 0
                    fence = max(previous_resource_fence, node_snapshot.fencing_epoch) + 1
                    event = Event(
                        event_id=request_id,
                        campaign_id=campaign_id,
                        event_type=EventType.ACQUIRE_LEASE,
                        expected_revision=expected_revision,
                        authority_epoch=authority_epoch,
                        cancellation_epoch=cancellation_epoch,
                        node_id=node_id,
                        actor_id=actor.actor_id,
                        fencing_epoch=fence,
                        payload={"lease_id": lease_id, "resource_key": resource_key},
                    )
                    next_snapshot, _ = self._apply_event_tx(
                        connection, event, operation_digest=request_digest
                    )
                self._insert_actor_record(connection, actor)
                lease = Lease(
                    lease_id=lease_id,
                    campaign_id=campaign_id,
                    node_id=node_id,
                    resource_key=resource_key,
                    actor_id=actor.actor_id,
                    fencing_epoch=fence,
                    cancellation_epoch=next_snapshot.cancellation_epoch,
                    expires_at=expires_at,
                )
                connection.execute(
                    """
                    INSERT INTO leases(
                        lease_id, campaign_id, node_id, resource_key, actor_id,
                        fencing_epoch, cancellation_epoch, state, expires_at, lease_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.lease_id,
                        lease.campaign_id,
                        lease.node_id,
                        lease.resource_key,
                        lease.actor_id,
                        lease.fencing_epoch,
                        lease.cancellation_epoch,
                        lease.state.value,
                        lease.expires_at,
                        canonical_json(lease.to_dict()),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO resource_locks(
                        resource_key, fencing_epoch, lease_id, campaign_id, node_id
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(resource_key) DO UPDATE SET
                        fencing_epoch=excluded.fencing_epoch,
                        lease_id=excluded.lease_id,
                        campaign_id=excluded.campaign_id,
                        node_id=excluded.node_id
                    """,
                    (resource_key, fence, lease_id, campaign_id, node_id),
                )
                if concurrent_read_only:
                    self._finish_operation(
                        connection,
                        request_id,
                        {"lease": lease.to_dict(), "snapshot": next_snapshot.to_dict()},
                    )
                connection.commit()
                return lease
            except Exception:
                connection.rollback()
                raise

    def bind_actor(
        self,
        lease_id: str,
        actor_id: str,
        role: ActorRole | str,
        native_thread_id: str,
        identity_digest: str,
        authority_epoch: int,
        cancellation_epoch: int,
        fencing_epoch: int,
        request_id: str,
        native_identity: Mapping[str, Any] | None = None,
        host_pid: int | None = None,
    ) -> Actor:
        role = role if isinstance(role, ActorRole) else ActorRole(str(role))
        native_identity = dict(native_identity or {})
        payload = {
            "lease_id": lease_id,
            "actor_id": actor_id,
            "role": role.value,
            "native_thread_id": native_thread_id,
            "identity_digest": identity_digest,
            "authority_epoch": authority_epoch,
            "cancellation_epoch": cancellation_epoch,
            "fencing_epoch": fencing_epoch,
            "native_identity": native_identity,
            "host_pid": host_pid,
        }
        digest = canonical_json_digest(payload)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                prior = self._operation_result(connection, request_id, digest)
                if prior is not None:
                    connection.commit()
                    return Actor.from_dict(prior["actor"])
                lease_row = connection.execute(
                    "SELECT * FROM leases WHERE lease_id=?", (lease_id,)
                ).fetchone()
                if lease_row is None or str(lease_row["state"]) != LeaseState.ACTIVE.value:
                    raise StoreError("actor binding requires an active one-use lease")
                if str(lease_row["actor_id"]) != actor_id:
                    raise AuthorityError("actor identifier differs from lease")
                snapshot = self._load_snapshot(connection, str(lease_row["campaign_id"]))
                if (
                    snapshot.authority_epoch != authority_epoch
                    or snapshot.cancellation_epoch != cancellation_epoch
                    or int(lease_row["fencing_epoch"]) != fencing_epoch
                ):
                    raise AuthorityError("actor binding epochs differ from current lease authority")
                row = connection.execute(
                    "SELECT * FROM actors WHERE actor_id=?", (actor_id,)
                ).fetchone()
                if row is None:
                    raise StoreError("lease actor record is missing")
                if str(row["role"]) != role.value:
                    raise AuthorityError("bound actor role differs from leased role")
                if int(row["used"]) or (
                    str(row["native_thread_id"]) != "UNBOUND"
                    and str(row["native_thread_id"]) != native_thread_id
                ):
                    raise AuthorityError("actor lease is already bound or used")
                can_write = role not in READ_ONLY_ACTOR_ROLES
                if canonical_json_digest(native_identity) != identity_digest:
                    raise AuthorityError("native actor identity digest is invalid")
                required_identity = {
                    "thread_id",
                    "cwd",
                    "source_digest",
                    "sandbox_type",
                    "writable_roots",
                    "mediated_write_scope",
                    "dynamic_tool_digest",
                    "native_write_mode",
                    "role",
                    "lease_digest",
                    "thread_created_idle",
                }
                if not required_identity.issubset(native_identity):
                    raise AuthorityError("native actor identity evidence is incomplete")
                normalized_cwd = str(native_identity.get("cwd", "")).replace("\\", "/").rstrip("/").casefold()
                expected_cwd = snapshot.spec.worktree.replace("\\", "/").rstrip("/").casefold()
                if (
                    native_identity.get("thread_id") != native_thread_id
                    or native_identity.get("role") != role.value
                    or normalized_cwd != expected_cwd
                    or native_identity.get("thread_created_idle") is not True
                    or native_identity.get("turn_id") not in {None, ""}
                ):
                    raise AuthorityError("native actor was not exactly bound while idle")
                source_digest = str(native_identity.get("source_digest", ""))
                lease_digest = str(native_identity.get("lease_digest", ""))
                if len(source_digest) != 64 or len(lease_digest) != 64:
                    raise AuthorityError("native actor identity digests are invalid")
                sandbox_type = str(native_identity.get("sandbox_type", "")).replace("_", "-").casefold()
                writable_roots = native_identity.get("writable_roots")
                mediated_scope = native_identity.get("mediated_write_scope")
                dynamic_tool_digest = str(
                    native_identity.get("dynamic_tool_digest", "")
                )
                native_write_mode = str(native_identity.get("native_write_mode", ""))
                if not isinstance(writable_roots, list) or not isinstance(
                    mediated_scope, list
                ):
                    raise AuthorityError("native actor writable roots are invalid")
                if can_write:
                    expected_scope = list(snapshot.node_spec(str(lease_row["node_id"])).allowed_paths)
                    if (
                        sandbox_type not in {"read-only", "readonly"}
                        or writable_roots
                        or mediated_scope != expected_scope
                        or native_write_mode != "scoped-dynamic-tools"
                        or len(dynamic_tool_digest) != 64
                    ):
                        raise AuthorityError(
                            "write actor lacks exact read-only mediated-write authority"
                        )
                elif (
                    sandbox_type not in {"read-only", "readonly"}
                    or writable_roots
                    or mediated_scope
                    or native_write_mode != "denied"
                    or len(dynamic_tool_digest) != 64
                ):
                    raise AuthorityError("read-only actor has writable native authority")
                if host_pid != native_identity.get("host_pid"):
                    raise AuthorityError("native host process identity differs from binding")
                process_identity = native_identity.get("host_process_identity")
                if host_pid is None:
                    if process_identity is not None:
                        raise AuthorityError("actor without a host PID has process identity")
                elif (
                    not isinstance(process_identity, Mapping)
                    or process_identity.get("pid") != host_pid
                    or not str(process_identity.get("creation_token", ""))
                    or not str(process_identity.get("executable", ""))
                ):
                    raise AuthorityError("native host process creation identity is incomplete")
                actor = Actor(
                    actor_id=actor_id,
                    campaign_id=str(lease_row["campaign_id"]),
                    node_id=str(lease_row["node_id"]),
                    role=role,
                    native_thread_id=native_thread_id,
                    authority_epoch=authority_epoch,
                    can_write=can_write,
                    native_identity=native_identity,
                    host_pid=host_pid,
                    principal_id=Actor.from_dict(
                        json.loads(str(row["actor_json"]))
                    ).principal_id,
                )
                self._begin_operation(
                    connection,
                    request_id=request_id,
                    campaign_id=actor.campaign_id,
                    kind="BIND_ACTOR",
                    payload_digest=digest,
                    revision=snapshot.revision,
                )
                connection.execute(
                    """
                    UPDATE actors SET role=?, native_thread_id=?, identity_digest=?,
                        native_identity_json=?, host_pid=?, can_write=?,
                        bound_request_id=?, used=1, actor_json=?
                    WHERE actor_id=?
                    """,
                    (
                        role.value,
                        native_thread_id,
                        identity_digest,
                        canonical_json(native_identity),
                        host_pid,
                        int(can_write),
                        request_id,
                        canonical_json(actor.to_dict()),
                        actor_id,
                    ),
                )
                result = {"actor": actor.to_dict()}
                self._finish_operation(connection, request_id, result)
                connection.commit()
                return actor
            except Exception:
                connection.rollback()
                raise

    def release_lease(
        self,
        lease_id: str,
        *,
        request_id: str,
        expected_revision: int,
        authority_epoch: int,
        cancellation_epoch: int,
    ) -> CampaignSnapshot:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM leases WHERE lease_id=?", (lease_id,)
                ).fetchone()
                if row is None:
                    raise StoreError(f"unknown lease: {lease_id}")
                payload = {
                    "lease_id": lease_id,
                    "expected_revision": expected_revision,
                    "authority_epoch": authority_epoch,
                    "cancellation_epoch": cancellation_epoch,
                }
                digest = canonical_json_digest(payload)
                prior = self._operation_result(connection, request_id, digest)
                if prior is not None:
                    connection.commit()
                    return CampaignSnapshot.from_dict(prior["snapshot"])
                if str(row["state"]) != LeaseState.ACTIVE.value:
                    raise StoreError("lease is not active")
                actor_row = connection.execute(
                    "SELECT role FROM actors WHERE actor_id=?", (row["actor_id"],)
                ).fetchone()
                if actor_row is None:
                    raise StoreError("lease actor is missing")
                role = ActorRole(str(actor_row["role"]))
                if role in CONCURRENT_READ_ONLY_ACTOR_ROLES:
                    snapshot = self._load_snapshot(connection, str(row["campaign_id"]))
                    if snapshot.revision != expected_revision:
                        raise RevisionConflict("read-only lease release revision is stale")
                    if (
                        snapshot.authority_epoch != authority_epoch
                        or snapshot.cancellation_epoch != cancellation_epoch
                    ):
                        raise AuthorityError("read-only lease release epochs are stale")
                    self._begin_operation(
                        connection,
                        request_id=request_id,
                        campaign_id=str(row["campaign_id"]),
                        kind="RELEASE_READ_ONLY_LEASE",
                        payload_digest=digest,
                        revision=expected_revision,
                    )
                else:
                    event = Event(
                        event_id=request_id,
                        campaign_id=str(row["campaign_id"]),
                        event_type=EventType.RELEASE_LEASE,
                        expected_revision=expected_revision,
                        authority_epoch=authority_epoch,
                        cancellation_epoch=cancellation_epoch,
                        node_id=str(row["node_id"]),
                        actor_id=str(row["actor_id"]),
                        fencing_epoch=int(row["fencing_epoch"]),
                        payload={"lease_id": lease_id},
                    )
                    snapshot, _ = self._apply_event_tx(
                        connection, event, operation_digest=digest
                    )
                released = json.loads(str(row["lease_json"]))
                released["state"] = LeaseState.RELEASED.value
                connection.execute(
                    "UPDATE leases SET state='RELEASED', lease_json=? WHERE lease_id=?",
                    (canonical_json(released), lease_id),
                )
                connection.execute(
                    "UPDATE resource_locks SET lease_id=NULL WHERE lease_id=?", (lease_id,)
                )
                if role in CONCURRENT_READ_ONLY_ACTOR_ROLES:
                    self._finish_operation(
                        connection,
                        request_id,
                        {"snapshot": snapshot.to_dict(), "effects": []},
                    )
                connection.commit()
                return snapshot
            except Exception:
                connection.rollback()
                raise

    def list_active_leases(self, campaign_id: str | None = None) -> list[Lease]:
        query = "SELECT lease_json FROM leases WHERE state='ACTIVE'"
        values: tuple[Any, ...] = ()
        if campaign_id is not None:
            query += " AND campaign_id=?"
            values = (campaign_id,)
        query += " ORDER BY resource_key, lease_id"
        with closing(self._connect()) as connection:
            return [
                Lease.from_dict(json.loads(str(row["lease_json"])))
                for row in connection.execute(query, values)
            ]

    def get_lease(self, lease_id: str) -> Lease:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT lease_json, state FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown lease: {lease_id}")
            payload = json.loads(str(row["lease_json"]))
            payload["state"] = str(row["state"])
            return Lease.from_dict(payload)

    def get_actor(self, actor_id: str) -> Actor:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT actor_json FROM actors WHERE actor_id=?", (actor_id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown actor: {actor_id}")
            return Actor.from_dict(json.loads(str(row["actor_json"])))

    def list_active_actor_identities(
        self, campaign_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.list_actor_identities(campaign_id=campaign_id, active_only=True)

    def list_actor_identities(
        self, campaign_id: str | None = None, *, active_only: bool = False
    ) -> list[dict[str, Any]]:
        query = """
            SELECT a.actor_json, a.identity_digest, a.native_identity_json,
                   a.host_pid, l.lease_id, l.resource_key, l.fencing_epoch,
                   l.cancellation_epoch, l.state AS lease_state
            FROM actors AS a JOIN leases AS l ON l.actor_id=a.actor_id
            WHERE 1=1
        """
        values: tuple[Any, ...] = ()
        if active_only:
            query += " AND l.state='ACTIVE'"
        if campaign_id is not None:
            query += " AND l.campaign_id=?"
            values = (campaign_id,)
        query += " ORDER BY a.actor_id, l.lease_id"
        with closing(self._connect()) as connection:
            return [
                {
                    "actor": json.loads(str(row["actor_json"])),
                    "identity_digest": row["identity_digest"],
                    "native_identity": json.loads(str(row["native_identity_json"])),
                    "host_pid": int(row["host_pid"]) if row["host_pid"] is not None else None,
                    "lease_id": str(row["lease_id"]),
                    "resource_key": str(row["resource_key"]),
                    "fencing_epoch": int(row["fencing_epoch"]),
                    "cancellation_epoch": int(row["cancellation_epoch"]),
                    "lease_state": str(row["lease_state"]),
                }
                for row in connection.execute(query, values)
            ]

    def current_epochs(self, campaign_id: str, node_id: str | None = None) -> dict[str, Any]:
        snapshot = self.get_snapshot(campaign_id)
        result: dict[str, Any] = {
            "campaign_id": campaign_id,
            "revision": snapshot.revision,
            "authority_epoch": snapshot.authority_epoch,
            "cancellation_epoch": snapshot.cancellation_epoch,
        }
        if node_id is not None:
            node = snapshot.node(node_id)
            result.update(
                {
                    "node_id": node_id,
                    "fencing_epoch": node.fencing_epoch,
                    "lease_actor_id": node.lease_actor_id,
                }
            )
        return result

    def verify_actor_action(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        lease_id: str,
        authority_epoch: int,
        cancellation_epoch: int,
        fencing_epoch: int,
        repository_root: str,
        action: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Authorize one exact write action from a bound, fenced native actor."""

        action = str(action or "").strip()
        if not action:
            raise AuthorityError("actor action must not be empty")
        with closing(self._connect()) as connection:
            snapshot = self._load_snapshot(connection, campaign_id)
            if snapshot.state in {
                CampaignState.CANCELLED,
                CampaignState.COMPLETED,
                CampaignState.FAILED,
            }:
                raise AuthorityError("terminal campaign cannot authorize actor writes")
            if (
                snapshot.authority_epoch != authority_epoch
                or snapshot.cancellation_epoch != cancellation_epoch
            ):
                raise AuthorityError("actor action epochs are stale")
            row = connection.execute(
                """
                SELECT a.*, l.lease_id, l.resource_key, l.fencing_epoch AS lease_fence,
                       l.cancellation_epoch AS lease_cancel, l.state AS lease_state,
                       l.campaign_id AS lease_campaign, l.node_id AS lease_node
                FROM actors AS a JOIN leases AS l ON l.actor_id=a.actor_id
                WHERE a.actor_id=? AND l.lease_id=?
                """,
                (actor_id, lease_id),
            ).fetchone()
            if row is None:
                raise AuthorityError("actor and lease are not bound to each other")
            if (
                str(row["lease_state"]) != LeaseState.ACTIVE.value
                or str(row["lease_campaign"]) != campaign_id
                or int(row["used"]) != 1
                or not row["bound_request_id"]
                or str(row["native_thread_id"]) == "UNBOUND"
                or not row["identity_digest"]
            ):
                raise AuthorityError("actor lease is not active and natively bound")
            role = ActorRole(str(row["role"]))
            if role in READ_ONLY_ACTOR_ROLES or not int(row["can_write"]):
                raise AuthorityError(f"{role.value} actor is write denied")
            if (
                int(row["authority_epoch"]) != authority_epoch
                or int(row["lease_cancel"]) != cancellation_epoch
                or int(row["lease_fence"]) != fencing_epoch
            ):
                raise AuthorityError("actor lease epochs or fence are stale")
            node_id = str(row["lease_node"])
            node = snapshot.node(node_id)
            if node.fencing_epoch != fencing_epoch or node.lease_actor_id != actor_id:
                raise AuthorityError("node no longer recognizes the actor lease fence")
            supplied_root = Path(repository_root).expanduser().resolve(strict=False)
            expected_root = Path(snapshot.spec.git_root).expanduser().resolve(strict=False)
            if str(supplied_root).casefold() != str(expected_root).casefold():
                raise AuthorityError("actor repository root differs from admitted exact Git root")
            relative_path: str | None = None
            if path is not None:
                candidate = Path(path).expanduser()
                if not candidate.is_absolute():
                    candidate = supplied_root / candidate
                candidate = candidate.resolve(strict=False)
                try:
                    relative_path = candidate.relative_to(supplied_root).as_posix()
                except ValueError as exc:
                    raise AuthorityError("actor path escapes the admitted repository root") from exc
                allowed_paths = snapshot.node_spec(node_id).allowed_paths or snapshot.spec.allowed_paths
                if not any(
                    fnmatch.fnmatchcase(relative_path.casefold(), pattern.casefold())
                    for pattern in allowed_paths
                ):
                    raise AuthorityError("actor path is outside the immutable allowed scope")
            authorization = {
                "campaign_id": campaign_id,
                "actor_id": actor_id,
                "lease_id": lease_id,
                "node_id": node_id,
                "role": role.value,
                "native_thread_id": str(row["native_thread_id"]),
                "identity_digest": str(row["identity_digest"]),
                "authority_epoch": authority_epoch,
                "cancellation_epoch": cancellation_epoch,
                "fencing_epoch": fencing_epoch,
                "repository_root": str(expected_root),
                "action": action,
                "path": relative_path,
            }
            authorization["authorization_digest"] = canonical_json_digest(authorization)
            return authorization

    def verify_publication_authority(
        self,
        campaign_id: str,
        effect_kind: EffectKind | str,
        *,
        authority_epoch: int,
        cancellation_epoch: int,
        node_id: str | None = None,
        candidate_head: str | None = None,
    ) -> dict[str, Any]:
        """Verify exact live epochs and immutable publication authority, without mutation."""

        kind = (
            effect_kind
            if isinstance(effect_kind, EffectKind)
            else EffectKind(str(effect_kind))
        )
        snapshot = self.get_snapshot(campaign_id)
        if snapshot.authority_epoch != authority_epoch:
            raise AuthorityError("publication authority epoch is stale")
        if snapshot.cancellation_epoch != cancellation_epoch:
            raise AuthorityError("publication cancellation epoch is stale")
        if snapshot.state in {CampaignState.DRAFT, CampaignState.APPROVED}:
            raise TransitionError("unstarted campaign has no publication authority")
        if snapshot.state in {CampaignState.CANCELLED, CampaignState.FAILED}:
            raise TransitionError("cancelled or failed campaign has no publication authority")
        allowed = tuple(snapshot.spec.publication_authority.get("allowed_effects", ()))
        if kind.value not in allowed:
            raise AuthorityError(f"publication effect is not authorized: {kind.value}")
        if node_id is None or candidate_head is None:
            raise AuthorityError(
                "publication authority requires one exact node and candidate head"
            )
        node = snapshot.node(node_id)
        if node.candidate_head != candidate_head:
            raise AuthorityError("publication candidate head differs from frozen node head")
        if kind is EffectKind.EXACT_FILE_REPLACE:
            if node.state is not NodeState.DONE or snapshot.state is not CampaignState.COMPLETED:
                raise AuthorityError(
                    "exact-file installation authority requires a completed published node"
                )
        elif node.state is NodeState.READY_TO_PUBLISH:
            sequence = tuple(
                snapshot.spec.publication_authority.get("required_effects", ())
            )
            completed = tuple(node.completed_publication_effects)
            expected = sequence[len(completed)] if len(completed) < len(sequence) else None
            if expected != kind.value:
                raise AuthorityError(
                    "publication effect is not the next immutable required effect"
                )
        elif node.state is NodeState.PUBLISHING:
            if node.pending_publication_effect != kind.value:
                raise AuthorityError(
                    "publication effect differs from the reducer-prepared operation"
                )
        else:
            raise AuthorityError(
                "node lifecycle state is not publication-ready"
            )
        result: dict[str, Any] = {
            "campaign_id": campaign_id,
            "effect_kind": kind.value,
            "authority_epoch": authority_epoch,
            "cancellation_epoch": cancellation_epoch,
            "specification_digest": snapshot.spec.specification_digest,
            "authorized": True,
        }
        result.update(
            {
                "node_id": node_id,
                "candidate_head": node.candidate_head,
                "fencing_epoch": node.fencing_epoch,
                "node_state": node.state.value,
                "campaign_state": snapshot.state.value,
            }
        )
        return result

    def cancel_campaign(
        self,
        campaign_id: str,
        *,
        request_id: str,
        expected_revision: int | None = None,
        authority_epoch: int | None = None,
        cancellation_epoch: int | None = None,
        reason: str = "STOP",
    ) -> CampaignSnapshot:
        current = self.get_snapshot(campaign_id)
        event = Event(
            event_id=request_id,
            campaign_id=campaign_id,
            event_type=EventType.CANCEL,
            expected_revision=(current.revision if expected_revision is None else expected_revision),
            authority_epoch=(current.authority_epoch if authority_epoch is None else authority_epoch),
            cancellation_epoch=(
                current.cancellation_epoch
                if cancellation_epoch is None
                else cancellation_epoch
            ),
            payload={"reason": reason},
        )
        return self.apply_event(event)[0]

    def prepare_effect(
        self,
        operation_id: str,
        campaign_id: str,
        node_id: str | None,
        kind: EffectKind | str,
        payload: Mapping[str, Any],
        payload_digest: str | None = None,
    ) -> dict[str, Any]:
        effect_kind = kind if isinstance(kind, EffectKind) else EffectKind(str(kind))
        computed = canonical_json_digest(payload)
        if payload_digest is not None and payload_digest != computed:
            raise RequestConflict("effect payload digest differs from supplied payload")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                snapshot = self._load_snapshot(connection, campaign_id)
                if snapshot.state is CampaignState.CANCELLED:
                    raise TransitionError("cancelled campaign cannot prepare new external effects")
                node = snapshot.node(node_id) if node_id else None
                allowed = tuple(snapshot.spec.publication_authority.get("allowed_effects", ()))
                if effect_kind.value not in allowed:
                    raise AuthorityError(
                        f"effect lacks immutable publication authority: {effect_kind.value}"
                    )
                if (
                    node is not None
                    and node.candidate_head is not None
                    and payload.get("candidate_head") != node.candidate_head
                ):
                    raise AuthorityError("effect candidate head differs from frozen node head")
                if node is not None and (
                    node.state is not NodeState.PUBLISHING
                    or node.pending_publication_effect != effect_kind.value
                    or node.pending_publication_operation_id != operation_id
                ):
                    raise AuthorityError(
                        "node effect is not the exact reducer-prepared publication operation"
                    )
                intent = ExternalEffectIntent(
                    operation_id=operation_id,
                    campaign_id=campaign_id,
                    node_id=node_id,
                    kind=effect_kind,
                    state=EffectState.PREPARED,
                    payload=payload,
                    authority_epoch=snapshot.authority_epoch,
                    cancellation_epoch=snapshot.cancellation_epoch,
                    fencing_epoch=node.fencing_epoch if node else None,
                    external=True,
                )
                self._insert_effect(connection, intent, snapshot.revision)
                connection.commit()
                return self.get_effect(operation_id)
            except Exception:
                connection.rollback()
                raise

    def get_effect(self, operation_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM external_effect_outbox WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown effect operation: {operation_id}")
            return self._effect_row(row)

    @staticmethod
    def _effect_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operation_id": str(row["operation_id"]),
            "campaign_id": str(row["campaign_id"]),
            "node_id": row["node_id"],
            "kind": str(row["kind"]),
            "state": str(row["state"]),
            "payload": json.loads(str(row["payload_json"])),
            "payload_digest": str(row["payload_digest"]),
            "authority_epoch": int(row["authority_epoch"]),
            "cancellation_epoch": int(row["cancellation_epoch"]),
            "fencing_epoch": (
                int(row["fencing_epoch"]) if row["fencing_epoch"] is not None else None
            ),
            "created_revision": int(row["created_revision"]),
            "updated_revision": int(row["updated_revision"]),
            "result": json.loads(str(row["result_json"])) if row["result_json"] else None,
        }

    def list_outbox(
        self, *, state: EffectState | str | None = None, campaign_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if state is not None:
            clauses.append("state=?")
            values.append(
                (state if isinstance(state, EffectState) else EffectState(str(state))).value
            )
        if campaign_id is not None:
            clauses.append("campaign_id=?")
            values.append(campaign_id)
        query = "SELECT * FROM external_effect_outbox"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_revision, operation_id"
        with closing(self._connect()) as connection:
            return [self._effect_row(row) for row in connection.execute(query, values)]

    def update_effect(
        self,
        operation_id: str,
        state: EffectState | str,
        *,
        result: Mapping[str, Any] | None = None,
        expected_state: EffectState | str | None = None,
    ) -> dict[str, Any]:
        target = state if isinstance(state, EffectState) else EffectState(str(state))
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM external_effect_outbox WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise StoreError(f"unknown effect operation: {operation_id}")
                current = EffectState(str(row["state"]))
                expected = (
                    expected_state
                    if isinstance(expected_state, EffectState)
                    else EffectState(str(expected_state))
                ) if expected_state is not None else None
                if expected is not None and current is not expected:
                    raise RevisionConflict("effect state compare-and-swap failed")
                if target is current:
                    if result is not None:
                        snapshot = self._load_snapshot(
                            connection, str(row["campaign_id"])
                        )
                        connection.execute(
                            """
                            UPDATE external_effect_outbox
                            SET result_json=?, updated_revision=?
                            WHERE operation_id=? AND state=?
                            """,
                            (
                                canonical_json(result),
                                snapshot.revision,
                                operation_id,
                                current.value,
                            ),
                        )
                        row = connection.execute(
                            "SELECT * FROM external_effect_outbox WHERE operation_id=?",
                            (operation_id,),
                        ).fetchone()
                    connection.commit()
                    return self._effect_row(row)
                if target not in EFFECT_TRANSITIONS[current]:
                    raise TransitionError(
                        f"effect transition {current.value} -> {target.value} is forbidden"
                    )
                snapshot = self._load_snapshot(connection, str(row["campaign_id"]))
                if target is EffectState.EXECUTING:
                    if snapshot.state is CampaignState.CANCELLED:
                        raise TransitionError("cancelled campaign cannot execute a prepared effect")
                    if (
                        int(row["authority_epoch"]) != snapshot.authority_epoch
                        or int(row["cancellation_epoch"]) != snapshot.cancellation_epoch
                    ):
                        raise AuthorityError("effect epochs are stale")
                    if row["node_id"] is not None and row["fencing_epoch"] is not None:
                        node = snapshot.node(str(row["node_id"]))
                        if int(row["fencing_epoch"]) != node.fencing_epoch:
                            raise AuthorityError("effect fencing epoch is stale")
                connection.execute(
                    """
                    UPDATE external_effect_outbox SET state=?, result_json=?, updated_revision=?
                    WHERE operation_id=? AND state=?
                    """,
                    (
                        target.value,
                        canonical_json(result) if result is not None else None,
                        snapshot.revision,
                        operation_id,
                        current.value,
                    ),
                )
                connection.commit()
                return self.get_effect(operation_id)
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _verify_evidence_digest(item: Evidence, *, terminal: bool) -> None:
        payload = json.loads(canonical_json(item.payload))
        protocol = str(payload.get("protocol_version", ""))
        is_terminal = protocol == "ccos-native-terminal-receipt-v1"
        if is_terminal != terminal:
            if is_terminal:
                raise AuthorityError(
                    "native terminal receipts require the attested evidence path"
                )
            raise AuthorityError("terminal evidence has the wrong receipt protocol")
        if is_terminal:
            claimed = str(payload.pop("receipt_digest", ""))
            expected = canonical_json_digest(payload)
        elif "evidence_sha256" in payload:
            claimed = str(payload.pop("evidence_sha256", ""))
            expected = canonical_json_digest(payload)
        elif "admission_sha256" in payload:
            claimed = str(payload.pop("admission_sha256", ""))
            expected = canonical_json_digest(
                {
                    "repository": payload.get("repository"),
                    "installed_runtime": payload.get("installed_runtime"),
                    "allowed_paths": payload.get("allowed_paths"),
                    "validation_commands": payload.get("validation_commands"),
                    "human_authorization_verifier": payload.get(
                        "human_authorization_verifier"
                    ),
                }
            )
        else:
            raise AuthorityError("evidence payload has no recognized digest field")
        if not claimed or claimed != item.digest or expected != item.digest:
            raise AuthorityError("evidence digest does not match its canonical payload")

    @staticmethod
    def _insert_evidence(connection: sqlite3.Connection, item: Evidence) -> None:
        existing = connection.execute(
            "SELECT digest, payload_json FROM evidence WHERE evidence_id=?",
            (item.evidence_id,),
        ).fetchone()
        payload_json = canonical_json(item.payload)
        if existing is not None:
            if (
                str(existing["digest"]) != item.digest
                or str(existing["payload_json"]) != payload_json
            ):
                raise RequestConflict("evidence identifier was rebound")
            return
        connection.execute(
            """
            INSERT INTO evidence(
                evidence_id, campaign_id, node_id, kind, digest,
                candidate_head, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.evidence_id,
                item.campaign_id,
                item.node_id,
                item.kind.value,
                item.digest,
                item.candidate_head,
                payload_json,
            ),
        )

    def record_evidence(self, evidence: Evidence | Mapping[str, Any]) -> Evidence:
        item = evidence if isinstance(evidence, Evidence) else Evidence.from_dict(evidence)
        self._verify_evidence_digest(item, terminal=False)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._insert_evidence(connection, item)
                connection.commit()
                return item
            except Exception:
                connection.rollback()
                raise

    def record_terminal_evidence(
        self, evidence: Evidence | Mapping[str, Any]
    ) -> Evidence:
        """Attest one native terminal receipt against its live bound actor lease."""

        item = evidence if isinstance(evidence, Evidence) else Evidence.from_dict(evidence)
        if item.kind not in {EvidenceKind.GIT, EvidenceKind.REVIEW}:
            raise AuthorityError("terminal evidence kind must be GIT or REVIEW")
        self._verify_evidence_digest(item, terminal=True)
        payload = json.loads(canonical_json(item.payload))
        actor_id = str(payload.get("actor_id", ""))
        lease_id = str(payload.get("lease_id", ""))
        operation_id = f"attest:{item.evidence_id}"
        operation_digest = canonical_json_digest(item.to_dict())
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                prior = self._operation_result(connection, operation_id, operation_digest)
                if prior is not None:
                    connection.commit()
                    return item
                row = connection.execute(
                    """
                    SELECT a.actor_json, a.native_thread_id, a.identity_digest,
                           a.bound_request_id, a.used, l.*
                    FROM actors AS a JOIN leases AS l ON l.actor_id=a.actor_id
                    WHERE a.actor_id=? AND l.lease_id=?
                    """,
                    (actor_id, lease_id),
                ).fetchone()
                if row is None:
                    raise AuthorityError("terminal receipt actor lease is missing")
                actor = Actor.from_dict(json.loads(str(row["actor_json"])))
                snapshot = self._load_snapshot(connection, item.campaign_id)
                if (
                    str(row["state"]) != LeaseState.ACTIVE.value
                    or int(row["used"]) != 1
                    or not row["bound_request_id"]
                    or not row["identity_digest"]
                    or actor.campaign_id != item.campaign_id
                    or actor.node_id != item.node_id
                    or str(row["campaign_id"]) != item.campaign_id
                    or str(row["node_id"]) != item.node_id
                    or actor.role.value != payload.get("role")
                    or actor.native_thread_id != payload.get("native_thread_id")
                    or int(row["fencing_epoch"]) != int(payload.get("fencing_epoch", -1))
                    or int(row["cancellation_epoch"])
                    != int(payload.get("cancellation_epoch", -1))
                    or actor.authority_epoch != int(payload.get("authority_epoch", -1))
                    or snapshot.authority_epoch != actor.authority_epoch
                    or snapshot.cancellation_epoch != int(row["cancellation_epoch"])
                    or not str(payload.get("native_turn_id", ""))
                ):
                    raise AuthorityError(
                        "terminal receipt differs from the active native actor binding"
                    )
                if item.candidate_head != payload.get("candidate_head"):
                    raise AuthorityError("terminal receipt candidate head was changed")
                if actor.role in {ActorRole.REVIEWER, ActorRole.CLOSURE_REVIEWER}:
                    result_payload = payload.get("result_payload")
                    if (
                        not actor.principal_id
                        or not isinstance(result_payload, Mapping)
                        or result_payload.get("reviewer_id") != actor.principal_id
                        or snapshot.node(str(item.node_id)).candidate_head
                        != item.candidate_head
                    ):
                        raise AuthorityError(
                            "review receipt is not bound to its assigned reviewer and frozen head"
                        )
                self._begin_operation(
                    connection,
                    request_id=operation_id,
                    campaign_id=item.campaign_id,
                    kind="ATTEST_NATIVE_TERMINAL_RECEIPT",
                    payload_digest=operation_digest,
                    revision=snapshot.revision,
                )
                self._insert_evidence(connection, item)
                result = {
                    "evidence_id": item.evidence_id,
                    "digest": item.digest,
                    "actor_id": actor_id,
                    "lease_id": lease_id,
                    "native_thread_id": payload.get("native_thread_id"),
                    "native_turn_id": payload.get("native_turn_id"),
                    "principal_id": actor.principal_id,
                }
                self._finish_operation(connection, operation_id, result)
                connection.commit()
                return item
            except Exception:
                connection.rollback()
                raise

    def verify_terminal_evidence_attestation(
        self,
        evidence_id: str,
        *,
        digest: str,
        actor_id: str,
        lease_id: str,
        native_thread_id: str,
        native_turn_id: str,
        principal_id: str | None,
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status, result_json FROM operations WHERE request_id=?",
                (f"attest:{evidence_id}",),
            ).fetchone()
            if row is None or str(row["status"]) != "CONFIRMED" or not row["result_json"]:
                raise AuthorityError("native terminal receipt has no completed attestation")
            result = json.loads(str(row["result_json"]))
            expected = {
                "evidence_id": evidence_id,
                "digest": digest,
                "actor_id": actor_id,
                "lease_id": lease_id,
                "native_thread_id": native_thread_id,
                "native_turn_id": native_turn_id,
                "principal_id": principal_id,
            }
            if result != expected:
                raise AuthorityError("native terminal receipt attestation tuple differs")
            return result

    def find_evidence_by_digest(
        self,
        campaign_id: str,
        node_id: str,
        digest: str,
        *,
        kind: EvidenceKind,
        candidate_head: str,
    ) -> list[Evidence]:
        """Return exact persisted evidence records for a frozen receipt digest."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT evidence_id, campaign_id, node_id, kind, digest,
                       candidate_head, payload_json
                FROM evidence
                WHERE campaign_id=? AND node_id=? AND digest=? AND kind=?
                  AND candidate_head=?
                ORDER BY evidence_id
                """,
                (campaign_id, node_id, digest, kind.value, candidate_head),
            )
            return [
                Evidence.from_dict(
                    {
                        "evidence_id": row["evidence_id"],
                        "campaign_id": row["campaign_id"],
                        "node_id": row["node_id"],
                        "kind": row["kind"],
                        "digest": row["digest"],
                        "candidate_head": row["candidate_head"],
                        "payload": json.loads(str(row["payload_json"])),
                    }
                )
                for row in rows
            ]

    def record_review(
        self,
        review_id: str,
        campaign_id: str,
        node_id: str,
        cohort_digest: str,
        candidate_head: str,
        state: str,
        receipt: Mapping[str, Any] | None = None,
    ) -> None:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                receipt_json = canonical_json(receipt) if receipt is not None else None
                existing = connection.execute(
                    "SELECT * FROM reviews WHERE review_id=?", (review_id,)
                ).fetchone()
                if existing is not None:
                    immutable = (
                        str(existing["campaign_id"]),
                        str(existing["node_id"]),
                        str(existing["cohort_digest"]),
                        str(existing["candidate_head"] or ""),
                    )
                    requested = (
                        campaign_id,
                        node_id,
                        cohort_digest,
                        candidate_head,
                    )
                    if immutable != requested:
                        raise RequestConflict("review identifier was rebound")
                    if (
                        existing["receipt_json"] is not None
                        and receipt_json is not None
                        and str(existing["receipt_json"]) != receipt_json
                    ):
                        raise RequestConflict("frozen review receipt was rebound")
                    connection.execute(
                        "UPDATE reviews SET state=?, receipt_json=COALESCE(receipt_json, ?) "
                        "WHERE review_id=?",
                        (state, receipt_json, review_id),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO reviews(
                            review_id, campaign_id, node_id, cohort_digest,
                            candidate_head, state, receipt_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            review_id,
                            campaign_id,
                            node_id,
                            cohort_digest,
                            candidate_head,
                            state,
                            receipt_json,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def record_finding(self, campaign_id: str, node_id: str, finding: Finding) -> None:
        snapshot = self.get_snapshot(campaign_id)
        node = snapshot.node(node_id)
        if finding.finding_id not in {
            item.finding_id for item in node.findings + node.closure_findings
        }:
            raise TransitionError("findings must first be frozen by the lifecycle reducer")

    def record_runtime_installation(self, installation: Mapping[str, Any]) -> None:
        required = (
            "installation_id",
            "source_commit",
            "bundle_digest",
            "install_transaction",
            "protocol_version",
            "schema_compatibility",
            "host_capability_probe_version",
        )
        missing = [name for name in required if not installation.get(name)]
        if missing:
            raise StoreError(f"runtime installation is missing fields: {missing}")
        raw = canonical_json(installation)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT installation_json FROM runtime_installations WHERE installation_id=?",
                    (installation["installation_id"],),
                ).fetchone()
                if existing is not None:
                    if str(existing["installation_json"]) != raw:
                        raise RequestConflict("runtime installation identifier was rebound")
                    connection.commit()
                    return
                connection.execute(
                    """
                    INSERT INTO runtime_installations(
                        installation_id, source_commit, bundle_digest, install_transaction,
                        protocol_version, schema_compatibility,
                        host_capability_probe_version, installation_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(installation[name] for name in required) + (raw,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def list_runtime_installations(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [
                json.loads(str(row["installation_json"]))
                for row in connection.execute(
                    "SELECT installation_json FROM runtime_installations ORDER BY installation_id"
                )
            ]

    def record_legacy_archive(
        self,
        archive_id: str,
        source_path: str,
        digest: str,
        last_state: str,
        classification: str,
        evidence: Mapping[str, Any] | Iterable[Mapping[str, Any]],
        translated_outcome: str | None = None,
    ) -> dict[str, Any]:
        if classification not in {
            "LEGACY_ARCHIVED_UNRESOLVED",
            "LEGACY_ARCHIVED_TERMINAL_EVIDENCE",
        }:
            raise StoreError("legacy archive classification is not permitted")
        if translated_outcome is not None:
            raise StoreError("legacy archives must never translate into campaign outcomes")
        record = {
            "archive_id": archive_id,
            "source_path": source_path,
            "digest": digest,
            "last_state": last_state,
            "classification": classification,
            "evidence": evidence,
            "translated_outcome": None,
        }
        raw = canonical_json(record)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT archive_json FROM legacy_archives WHERE archive_id=?",
                    (archive_id,),
                ).fetchone()
                if existing is not None and str(existing["archive_json"]) != raw:
                    raise RequestConflict("legacy archive identifier was rebound")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO legacy_archives(
                        archive_id, source_path, digest, last_state, classification,
                        evidence_json, archive_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        archive_id,
                        source_path,
                        digest,
                        last_state,
                        classification,
                        canonical_json(evidence),
                        raw,
                    ),
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def list_legacy_archives(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [
                json.loads(str(row["archive_json"]))
                for row in connection.execute(
                    "SELECT archive_json FROM legacy_archives ORDER BY archive_id"
                )
            ]

    def recover_after_restart(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                ambiguous = connection.execute(
                    "UPDATE external_effect_outbox SET state='AMBIGUOUS' WHERE state='EXECUTING'"
                ).rowcount
                # A supervisor restart loses the in-memory transport that owns
                # every active native lease.  Keeping such a lease active would
                # leave an unauditable writer or reviewer and a permanent
                # resource lock.  Recovery therefore fences every orphaned
                # active lease.  The supervisor then fails the exact affected
                # node through the reducer instead of silently redispatching a
                # second implementation or review generation.
                invalidated = connection.execute(
                    "UPDATE leases SET state='INVALIDATED' WHERE state='ACTIVE'"
                ).rowcount
                connection.execute(
                    """
                    UPDATE resource_locks SET lease_id=NULL
                    WHERE lease_id IN (SELECT lease_id FROM leases WHERE state<>'ACTIVE')
                    """
                )
                connection.commit()
                return {"ambiguous_effects": ambiguous, "invalidated_leases": invalidated}
            except Exception:
                connection.rollback()
                raise

    def telemetry_counts(self, campaign_id: str | None = None) -> dict[str, int]:
        query = "SELECT category, COUNT(*) AS count FROM telemetry"
        values: tuple[Any, ...] = ()
        if campaign_id is not None:
            query += " WHERE campaign_id=?"
            values = (campaign_id,)
        query += " GROUP BY category"
        counts = {
            "completed": 0,
            "failed": 0,
            "stopped": 0,
            "denied": 0,
            "loop_prevented": 0,
        }
        with closing(self._connect()) as connection:
            for row in connection.execute(query, values):
                counts[str(row["category"])] = int(row["count"])
        return counts
