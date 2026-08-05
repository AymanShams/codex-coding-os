"""Deterministic supervisor for one approved campaign graph.

The supervisor chooses from the immutable graph, spends durable budget before
autonomous work, and delegates all lifecycle decisions to ``reducer.reduce``
through ``CampaignStore.apply_event``. It never edits product files and it
returns when an external event is required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping, Protocol, Sequence

from .effects import ExternalEffectDriver
from .evidence import (
    EvidenceError,
    HostedEvidenceError,
    TrustedCommand,
    ValidationFailure,
    collect_hosted_checks,
    exact_repository_evidence,
    execute_trusted_command,
    publication_preflight,
)
from .host import (
    ActorLease,
    HostProtocolError,
    HostScopeError,
    NativeCodexHost,
    TerminalReceipt,
    terminate_verified_process_tree,
)
from .model import (
    Actor,
    ActorRole,
    BudgetError,
    BudgetToken,
    CampaignMode,
    CampaignSnapshot,
    CampaignState,
    EffectKind,
    EffectState,
    Event,
    EventType,
    Evidence,
    EvidenceKind,
    Finding,
    FindingOrigin,
    NodeSnapshot,
    NodeState,
    TransitionError,
    canonical_json_digest,
)
from .store import CampaignStore


class SupervisorError(RuntimeError):
    pass


class AttemptAlreadyReserved(SupervisorError):
    """The exact autonomous attempt already owns its durable token."""


class SupervisorDeadlineExceeded(SupervisorError):
    pass


class WorkerHost(Protocol):
    def create_idle_actor(
        self,
        lease: ActorLease,
        *,
        bind_authority: Callable[[ActorLease, Mapping[str, Any]], None],
        authorize_action: Callable[
            [ActorLease, str, str | None], Mapping[str, Any]
        ]
        | None = None,
        ephemeral: bool = False,
    ) -> Any: ...

    def start_actor_turn(self, lease_id: str, prompt: str) -> Any: ...

    def collect_terminal_receipt(
        self,
        lease_id: str,
        *,
        current_epochs: Callable[[str, str], Mapping[str, int]],
        timeout: float | None = None,
    ) -> TerminalReceipt: ...

    def interrupt(self, lease_id: str) -> None: ...


@dataclass(frozen=True)
class SupervisorDecision:
    campaign_id: str
    revision: int
    campaign_state: str
    action: str
    node_id: str | None = None
    wait_event: str | None = None
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if value["details"] is None:
            value["details"] = {}
        return value


@dataclass(frozen=True)
class WorkerRuntime:
    lease: ActorLease
    role: ActorRole
    reviewer_id: str | None = None


def _stable_id(*parts: object) -> str:
    body = ":".join(str(item) for item in parts)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
    return f"ccos:{digest}"


def _utc(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise SupervisorError("approved deadline must include a timezone")
    return parsed.astimezone(timezone.utc)


class DeterministicSupervisor:
    """Advance one campaign until one durable action or one external wait."""

    def __init__(
        self,
        store: CampaignStore,
        *,
        host: WorkerHost | None = None,
        effect_driver: ExternalEffectDriver | None = None,
        hosted_check_collector: Callable[..., Mapping[str, Any]] = collect_hosted_checks,
        publication_preflight_runner: Callable[..., Mapping[str, Any]] = publication_preflight,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.host = host or NativeCodexHost()
        self.effect_driver = effect_driver
        self.hosted_check_collector = hosted_check_collector
        self.publication_preflight_runner = publication_preflight_runner
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._workers: dict[str, WorkerRuntime] = {}

    @staticmethod
    def _event(
        snapshot: CampaignSnapshot,
        event_type: EventType,
        *,
        label: str,
        node_id: str | None = None,
        actor_id: str | None = None,
        fencing_epoch: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Event:
        return Event(
            event_id=_stable_id(
                snapshot.spec.campaign_id,
                snapshot.revision,
                event_type.value,
                node_id or "campaign",
                label,
            ),
            campaign_id=snapshot.spec.campaign_id,
            event_type=event_type,
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            node_id=node_id,
            actor_id=actor_id,
            fencing_epoch=fencing_epoch,
            payload=payload or {},
        )

    def _check_deadline(
        self, snapshot: CampaignSnapshot, node: NodeSnapshot | None = None
    ) -> None:
        deadline = getattr(snapshot.spec, "deadline_utc", None)
        if node is not None:
            node_spec = snapshot.node_spec(node.node_id)
            deadline = getattr(node_spec, "deadline_utc", None) or deadline
        if deadline and self.now().astimezone(timezone.utc) > _utc(str(deadline)):
            raise SupervisorDeadlineExceeded(
                f"approved deadline exceeded for {node.node_id if node else snapshot.spec.campaign_id}"
            )

    def _fail_deadline(
        self, snapshot: CampaignSnapshot, node: NodeSnapshot | None = None
    ) -> SupervisorDecision:
        if snapshot.state is CampaignState.DRAFT:
            raise SupervisorDeadlineExceeded(
                "draft campaign deadline expired before exact specification approval"
            )
        target = node
        if target is None and snapshot.active_node_id:
            target = snapshot.node(snapshot.active_node_id)
        if target is None:
            target = next(
                (item for item in snapshot.nodes if item.state not in {
                    NodeState.DONE,
                    NodeState.FAILED_EXACT_NODE,
                    NodeState.CANCELLED,
                }),
                None,
            )
        if target is None:
            raise SupervisorDeadlineExceeded("campaign deadline expired without a fail-able node")
        self._interrupt_node_workers(snapshot.spec.campaign_id, target.node_id)
        next_snapshot = self._apply(
            self._event(
                snapshot,
                EventType.FAIL_NODE,
                label="deadline-exceeded",
                node_id=target.node_id,
                payload={"reason": "approved deadline exceeded"},
            )
        )
        return SupervisorDecision(
            next_snapshot.spec.campaign_id,
            next_snapshot.revision,
            next_snapshot.state.value,
            "DEADLINE_EXCEEDED",
            target.node_id,
        )

    def _fail_budget(
        self, snapshot: CampaignSnapshot, reason: BaseException
    ) -> SupervisorDecision:
        target = (
            snapshot.node(snapshot.active_node_id)
            if snapshot.active_node_id is not None
            else next(
                (
                    item
                    for item in snapshot.nodes
                    if item.state
                    not in {
                        NodeState.DONE,
                        NodeState.FAILED_EXACT_NODE,
                        NodeState.CANCELLED,
                    }
                ),
                None,
            )
        )
        if target is None:
            raise BudgetError(str(reason))
        if snapshot.spec.mode is CampaignMode.AUTOMATED:
            try:
                snapshot, _ = self._reserve_attempt(
                    snapshot,
                    BudgetToken.REJECTED_ATTEMPT,
                    node_id=target.node_id,
                    attempt_identity=(
                        f"budget-rejected:{target.node_id}:{type(reason).__name__}:"
                        f"{reason}"
                    ),
                )
            except BudgetError:
                snapshot = self.store.get_snapshot(snapshot.spec.campaign_id)
        self._interrupt_node_workers(snapshot.spec.campaign_id, target.node_id)
        next_snapshot = self._apply(
            self._event(
                snapshot,
                EventType.FAIL_NODE,
                label="attempt-budget-exhausted",
                node_id=target.node_id,
                payload={"reason": f"attempt budget exhausted: {reason}"},
            )
        )
        return SupervisorDecision(
            next_snapshot.spec.campaign_id,
            next_snapshot.revision,
            next_snapshot.state.value,
            "BUDGET_EXHAUSTED",
            target.node_id,
            details={"reason": str(reason)},
        )

    @staticmethod
    def select_next_approved_node(snapshot: CampaignSnapshot) -> NodeSnapshot | None:
        if snapshot.state is not CampaignState.RUNNING or snapshot.active_node_id:
            return None
        by_id = {item.node_id: item for item in snapshot.nodes}
        for spec in snapshot.spec.nodes:
            node = by_id[spec.node_id]
            if node.state is not NodeState.PENDING:
                continue
            if all(by_id[item].state is NodeState.DONE for item in spec.dependencies):
                return node
        return None

    def _apply(self, event: Event) -> CampaignSnapshot:
        return self.store.apply_event(event)[0]

    def _interrupt_node_workers(self, campaign_id: str, node_id: str) -> tuple[str, ...]:
        """Interrupt and forget every in-process worker bound to one exact node."""

        lease_ids = tuple(
            lease_id
            for lease_id, runtime in self._workers.items()
            if runtime.lease.campaign_id == campaign_id
            and runtime.lease.node_id == node_id
        )
        for lease_id in lease_ids:
            self.host.interrupt(lease_id)
            self._workers.pop(lease_id, None)
        return lease_ids

    def _fail_dispatch(
        self,
        campaign_id: str,
        node_id: str,
        role: ActorRole,
        exc: BaseException,
    ) -> None:
        """Fence a node after a worker/cohort transport fails during dispatch."""

        self._interrupt_node_workers(campaign_id, node_id)
        snapshot = self.store.get_snapshot(campaign_id)
        if snapshot.state in {
            CampaignState.COMPLETED,
            CampaignState.FAILED,
            CampaignState.CANCELLED,
        }:
            return
        node = snapshot.node(node_id)
        if node.state in {
            NodeState.DONE,
            NodeState.FAILED_EXACT_NODE,
            NodeState.CANCELLED,
        }:
            return
        if role is ActorRole.REVIEWER:
            label = "review cohort dispatch"
        elif role is ActorRole.CLOSURE_REVIEWER:
            label = "closure cohort dispatch"
        else:
            label = f"{role.value.casefold()} dispatch"
        self._apply(
            self._event(
                snapshot,
                EventType.FAIL_NODE,
                label=f"{label}:{type(exc).__name__}",
                node_id=node_id,
                payload={"reason": f"{label} failed: {type(exc).__name__}: {exc}"},
            )
        )

    def _consume(
        self,
        snapshot: CampaignSnapshot,
        token: BudgetToken,
        *,
        node_id: str | None,
        label: str,
    ) -> CampaignSnapshot:
        if snapshot.spec.mode is CampaignMode.MANUAL:
            return snapshot
        reserved, created = self._reserve_attempt(
            snapshot,
            token,
            node_id=node_id,
            attempt_identity=label,
        )
        if not created:
            raise AttemptAlreadyReserved(
                f"autonomous attempt is already reserved: {token.value}:{label}"
            )
        return reserved

    def _reserve_attempt(
        self,
        snapshot: CampaignSnapshot,
        token: BudgetToken,
        *,
        node_id: str | None,
        attempt_identity: str,
    ) -> tuple[CampaignSnapshot, bool]:
        """Reserve one exact automated attempt before any effective action.

        Retry, rejected, and no-op attempts are one-use identities rather than
        revision-derived counters.  The store performs the reservation and
        token consumption atomically, returning ``False`` to every concurrent
        or replaying caller after the first reservation.
        """

        if snapshot.spec.mode is CampaignMode.MANUAL:
            return snapshot, True
        request_id = _stable_id(
            "budget-attempt",
            snapshot.spec.campaign_id,
            token.value,
            node_id or "campaign",
            attempt_identity,
        )
        if any(item.request_id == request_id for item in snapshot.budget_receipts):
            return snapshot, False
        return self.store.reserve_budget_attempt(
            snapshot.spec.campaign_id,
            token,
            request_id=request_id,
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            node_id=node_id,
        )

    def _acquire_actor(
        self,
        snapshot: CampaignSnapshot,
        node_id: str,
        role: ActorRole,
        *,
        reviewer_id: str | None = None,
        native_worker: bool,
    ) -> tuple[CampaignSnapshot, Actor, Any]:
        snapshot = self._consume(
            snapshot,
            BudgetToken.CHILD_CREATION,
            node_id=node_id,
            label=f"create:{role.value}:{reviewer_id or 'single'}",
        ) if native_worker else snapshot
        actor_id = _stable_id(
            snapshot.spec.campaign_id,
            node_id,
            role.value,
            reviewer_id or "single",
            snapshot.revision,
        )
        read_only = role in {
            ActorRole.REVIEWER,
            ActorRole.CLOSURE_REVIEWER,
            ActorRole.PARENT,
            ActorRole.VALIDATOR,
            ActorRole.SUPERVISOR,
        }
        actor = Actor(
            actor_id=actor_id,
            campaign_id=snapshot.spec.campaign_id,
            node_id=node_id,
            role=role,
            native_thread_id="UNBOUND" if native_worker else f"trusted-{actor_id}",
            authority_epoch=snapshot.authority_epoch,
            can_write=not read_only,
            principal_id=reviewer_id,
        )
        lease_id = _stable_id("lease", actor_id)
        resource = (
            f"readonly:{snapshot.spec.worktree}:{actor_id}"
            if read_only
            else f"write:{snapshot.spec.worktree}"
        )
        lease = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            node_id,
            resource,
            actor,
            lease_id=lease_id,
            request_id=_stable_id("acquire", lease_id),
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        return self.store.get_snapshot(snapshot.spec.campaign_id), actor, lease

    def _host_lease(
        self,
        snapshot: CampaignSnapshot,
        node_id: str,
        role: ActorRole,
        actor: Actor,
        lease: Any,
        *,
        reviewer_id: str | None,
    ) -> ActorLease:
        node = snapshot.node(node_id)
        allowed = (
            snapshot.node_spec(node_id).allowed_paths
            if role in {ActorRole.IMPLEMENTER, ActorRole.REPAIRER}
            else ()
        )
        candidate_head = (
            node.candidate_head or node.start_head or snapshot.spec.base_sha
        )
        return ActorLease.issue(
            lease_id=lease.lease_id,
            request_id=_stable_id("native", lease.lease_id),
            campaign_id=snapshot.spec.campaign_id,
            node_id=node_id,
            actor_id=actor.actor_id,
            role=role.value,
            worktree=snapshot.spec.worktree,
            allowed_paths=allowed,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            fencing_epoch=lease.fencing_epoch,
            candidate_head=candidate_head,
        )

    def _bind_native(
        self, lease: ActorLease, native_identity: Mapping[str, Any]
    ) -> None:
        thread_id = str(native_identity.get("thread_id", ""))
        self.store.bind_actor(
            lease.lease_id,
            lease.actor_id,
            lease.role,
            thread_id,
            canonical_json_digest(native_identity),
            lease.authority_epoch,
            lease.cancellation_epoch,
            lease.fencing_epoch,
            _stable_id("bind", lease.lease_id, thread_id),
            native_identity=native_identity,
            host_pid=(
                int(native_identity["host_pid"])
                if isinstance(native_identity.get("host_pid"), int)
                else None
            ),
        )

    def _authorize_worker_action(
        self, lease: ActorLease, action: str, path: str | None
    ) -> Mapping[str, Any]:
        snapshot = self.store.get_snapshot(lease.campaign_id)
        return self.store.verify_actor_action(
            lease.campaign_id,
            actor_id=lease.actor_id,
            lease_id=lease.lease_id,
            authority_epoch=lease.authority_epoch,
            cancellation_epoch=lease.cancellation_epoch,
            fencing_epoch=lease.fencing_epoch,
            repository_root=snapshot.spec.git_root,
            action=action,
            path=path,
        )

    @staticmethod
    def _worker_prompt(
        snapshot: CampaignSnapshot,
        node_id: str,
        role: ActorRole,
        reviewer_id: str | None,
    ) -> str:
        node = snapshot.node(node_id)
        spec = snapshot.node_spec(node_id)
        binding = {
            "campaign_id": snapshot.spec.campaign_id,
            "node_id": node_id,
            "role": role.value,
            "reviewer_id": reviewer_id,
            "specification_digest": snapshot.spec.specification_digest,
            "authority_epoch": snapshot.authority_epoch,
            "cancellation_epoch": snapshot.cancellation_epoch,
            "fencing_epoch": node.fencing_epoch,
            "repository_remote": snapshot.spec.repository_remote,
            "worktree": snapshot.spec.worktree,
            "branch": snapshot.spec.branch,
            "base_sha": snapshot.spec.base_sha,
            "candidate_head": node.candidate_head,
            "allowed_paths": list(spec.allowed_paths),
            "objective": spec.objective,
        }
        if role in {ActorRole.REVIEWER, ActorRole.CLOSURE_REVIEWER}:
            instruction = (
                "Review the exact frozen candidate read-only. Return evidence-bound finding "
                "records only. Do not edit, publish, or create another task. Return one JSON "
                "object with reviewer_id, candidate_head, findings, and verdict. Each finding "
                "must contain finding_id, title, blocking, and details. Use only the supplied "
                "campaign_list_files, campaign_read_file, campaign_search, campaign_git_status, "
                "and campaign_git_diff tools."
            )
        else:
            instruction = (
                "Implement only this node inside allowed paths. Run no publication mutation. "
                "The native OS sandbox is read-only. Inspect through campaign_list_files, "
                "campaign_read_file, campaign_search, campaign_git_status, and campaign_git_diff. "
                "Make changes only through campaign_apply_patch, then use campaign_commit to "
                "commit the exact candidate and leave the worktree clean."
            )
        return f"{instruction}\n\nExact campaign binding:\n{binding!r}"

    def dispatch_worker(
        self,
        campaign_id: str,
        node_id: str,
        role: ActorRole,
        *,
        reviewer_id: str | None = None,
        lifecycle_event: EventType | None = None,
    ) -> ActorLease:
        snapshot = self.store.get_snapshot(campaign_id)
        self._check_deadline(snapshot, snapshot.node(node_id))
        snapshot, actor, lease = self._acquire_actor(
            snapshot,
            node_id,
            role,
            reviewer_id=reviewer_id,
            native_worker=True,
        )
        host_lease = self._host_lease(
            snapshot,
            node_id,
            role,
            actor,
            lease,
            reviewer_id=reviewer_id,
        )
        try:
            self.host.create_idle_actor(
                host_lease,
                bind_authority=self._bind_native,
                authorize_action=self._authorize_worker_action,
            )
            snapshot = self.store.get_snapshot(campaign_id)
            if lifecycle_event is not None:
                token = {
                    EventType.START_IMPLEMENTATION: BudgetToken.CHILD_START,
                    EventType.START_REPAIR: BudgetToken.REPAIR_DISPATCH,
                }[lifecycle_event]
                event = self._event(
                    snapshot,
                    lifecycle_event,
                    label=f"dispatch:{role.value}",
                    node_id=node_id,
                    actor_id=actor.actor_id,
                    fencing_epoch=lease.fencing_epoch,
                    payload={
                        "budget_token": token.value,
                        "operation_id": _stable_id("dispatch", host_lease.lease_id),
                        "effect_payload": {"lease_id": host_lease.lease_id},
                    },
                )
                snapshot = self._apply(event)
                if role is ActorRole.REPAIRER:
                    snapshot = self._consume(
                        snapshot,
                        BudgetToken.CHILD_START,
                        node_id=node_id,
                        label=f"start:{host_lease.lease_id}",
                    )
            else:
                snapshot = self._consume(
                    snapshot,
                    BudgetToken.CHILD_START,
                    node_id=node_id,
                    label=f"start:{host_lease.lease_id}",
                )
            self.host.start_actor_turn(
                host_lease.lease_id,
                self._worker_prompt(snapshot, node_id, role, reviewer_id),
            )
            self._workers[host_lease.lease_id] = WorkerRuntime(
                lease=host_lease, role=role, reviewer_id=reviewer_id
            )
            return host_lease
        except BudgetError as exc:
            self.host.interrupt(host_lease.lease_id)
            self._workers.pop(host_lease.lease_id, None)
            current = self.store.get_snapshot(campaign_id)
            if current.state not in {
                CampaignState.COMPLETED,
                CampaignState.FAILED,
                CampaignState.CANCELLED,
            }:
                self._fail_budget(current, exc)
            raise
        except BaseException as exc:
            self.host.interrupt(host_lease.lease_id)
            self._workers.pop(host_lease.lease_id, None)
            self._fail_dispatch(campaign_id, node_id, role, exc)
            raise

    def complete_worker(
        self,
        lease_id: str,
        *,
        timeout: float | None = None,
        transport_retry: bool = False,
    ) -> TerminalReceipt:
        runtime = self._workers.get(lease_id)
        if runtime is None:
            raise SupervisorError(f"unknown in-process worker lease: {lease_id}")
        failure_id = _stable_id("transport-failure", lease_id)
        retry_id = _stable_id("transport-retry", lease_id)
        failure = self.store.get_runtime_operation(failure_id)
        if not transport_retry and failure is not None:
            snapshot = self.store.get_snapshot(runtime.lease.campaign_id)
            _, reserved = self._reserve_attempt(
                snapshot,
                BudgetToken.REJECTED_ATTEMPT,
                node_id=runtime.lease.node_id,
                attempt_identity=f"terminal-collection-without-retry:{lease_id}",
            )
            reason = (
                "terminal collection after a transport failure requires the "
                "one explicit transport retry"
            )
            if not reserved:
                reason += "; the rejected attempt is already recorded"
            raise SupervisorError(reason)
        if transport_retry:
            snapshot = self.store.get_snapshot(runtime.lease.campaign_id)
            if failure is None or self.store.get_runtime_operation(retry_id) is not None:
                _, reserved = self._reserve_attempt(
                    snapshot,
                    BudgetToken.REJECTED_ATTEMPT,
                    node_id=runtime.lease.node_id,
                    attempt_identity=(
                        f"invalid-transport-retry:{lease_id}:"
                        f"{'missing-failure' if failure is None else 'already-used'}"
                    ),
                )
                suffix = "" if reserved else "; rejection already recorded"
                raise SupervisorError(
                    "transport retry requires one prior failure and can be used only once"
                    + suffix
                )
            snapshot, reserved = self._reserve_attempt(
                snapshot,
                BudgetToken.TRANSPORT_RETRY,
                node_id=runtime.lease.node_id,
                attempt_identity=f"transport-retry:{lease_id}",
            )
            if not reserved:
                raise SupervisorError(
                    "the one-use transport retry reservation was already consumed"
                )
            self.store.record_runtime_operation(
                request_id=retry_id,
                campaign_id=runtime.lease.campaign_id,
                kind="TRANSPORT_RETRY_STARTED",
                payload={"lease_id": lease_id, "failure_request_id": failure_id},
                result={"started": True},
                revision=snapshot.revision,
            )
        try:
            receipt = self.host.collect_terminal_receipt(
                lease_id, current_epochs=self.store.current_epochs, timeout=timeout
            )
        except HostProtocolError as exc:
            snapshot = self.store.get_snapshot(runtime.lease.campaign_id)
            self.store.record_runtime_operation(
                request_id=(
                    _stable_id("transport-failure-final", lease_id)
                    if transport_retry
                    else failure_id
                ),
                campaign_id=runtime.lease.campaign_id,
                kind="TRANSPORT_FAILURE",
                payload={
                    "lease_id": lease_id,
                    "retry": transport_retry,
                    "error_type": type(exc).__name__,
                },
                result={"error": str(exc)},
                revision=snapshot.revision,
            )
            if transport_retry:
                self.host.interrupt(lease_id)
                self._workers.pop(lease_id, None)
                self._fail_dispatch(
                    runtime.lease.campaign_id,
                    runtime.lease.node_id,
                    runtime.role,
                    exc,
                )
            raise
        evidence_payload = receipt.to_dict()
        digest_payload = dict(evidence_payload)
        claimed_digest = str(digest_payload.pop("receipt_digest", ""))
        binding = runtime.lease
        receipt_binding = (
            receipt.lease_id == lease_id
            and receipt.campaign_id == binding.campaign_id
            and receipt.node_id == binding.node_id
            and receipt.actor_id == binding.actor_id
            and receipt.role == runtime.role.value
            and receipt.authority_epoch == binding.authority_epoch
            and receipt.cancellation_epoch == binding.cancellation_epoch
            and receipt.fencing_epoch == binding.fencing_epoch
            and receipt.candidate_head == binding.candidate_head
        )
        if (
            not receipt_binding
            or not claimed_digest
            or canonical_json_digest(digest_payload) != claimed_digest
        ):
            error = SupervisorError(
                "terminal receipt digest or exact actor binding is invalid"
            )
            self.host.interrupt(lease_id)
            self._workers.pop(lease_id, None)
            self._fail_dispatch(
                binding.campaign_id, binding.node_id, runtime.role, error
            )
            raise error
        snapshot = self.store.get_snapshot(receipt.campaign_id)
        # Native terminal receipts use the attested path below. Generic
        # evidence insertion deliberately rejects this receipt protocol.
        self.store.record_terminal_evidence(
            Evidence(
                evidence_id=_stable_id("receipt", lease_id),
                campaign_id=receipt.campaign_id,
                node_id=receipt.node_id,
                kind=(
                    EvidenceKind.REVIEW
                    if runtime.role in {ActorRole.REVIEWER, ActorRole.CLOSURE_REVIEWER}
                    else EvidenceKind.GIT
                ),
                digest=receipt.receipt_digest,
                payload=evidence_payload,
                candidate_head=receipt.candidate_head,
            )
        )
        event_type = {
            ActorRole.IMPLEMENTER: EventType.IMPLEMENTATION_COMPLETED,
            ActorRole.REPAIRER: EventType.REPAIR_COMPLETED,
        }.get(runtime.role)
        if receipt.turn_status.casefold() not in {"completed", "complete"}:
            snapshot = self._apply(
                self._event(
                    snapshot,
                    EventType.FAIL_NODE,
                    label=f"worker:{receipt.turn_status}:{lease_id}",
                    node_id=receipt.node_id,
                    actor_id=receipt.actor_id,
                    fencing_epoch=receipt.fencing_epoch,
                    payload={"reason": f"worker_terminal:{receipt.turn_status}"},
                )
            )
        elif event_type is not None:
            snapshot = self._apply(
                self._event(
                    snapshot,
                    event_type,
                    label=f"terminal:{lease_id}",
                    node_id=receipt.node_id,
                    actor_id=receipt.actor_id,
                    fencing_epoch=receipt.fencing_epoch,
                    payload={"receipt_digest": receipt.receipt_digest},
                )
            )
        if snapshot.state not in {
            CampaignState.FAILED,
            CampaignState.CANCELLED,
            CampaignState.COMPLETED,
        }:
            snapshot = self.store.release_lease(
                lease_id,
                request_id=_stable_id("release", lease_id),
                expected_revision=snapshot.revision,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
            )
        self._workers.pop(lease_id, None)
        return receipt

    def _changed_paths(self, evidence: Mapping[str, Any]) -> tuple[str, ...]:
        values: list[str] = []
        for entry in evidence.get("changed_entries", ()):
            parts = str(entry).split("\t")
            values.extend(part for part in parts[1:] if part)
        return tuple(values)

    def _validate_scope(
        self, snapshot: CampaignSnapshot, node_id: str, evidence: Mapping[str, Any]
    ) -> None:
        from .host import ScopeGuard

        guard = ScopeGuard(snapshot.spec.worktree, snapshot.node_spec(node_id).allowed_paths)
        changed = self._changed_paths(evidence)
        if not changed:
            raise ValidationFailure("implementation produced no committed candidate diff")
        for path in changed:
            guard.require(path)

    def validate_node(self, campaign_id: str, node_id: str) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        node = snapshot.node(node_id)
        if node.state not in {NodeState.VALIDATING, NodeState.REVALIDATING}:
            raise SupervisorError("node is not ready for trusted validation")
        self._check_deadline(snapshot, node)
        commands = {
            item.command_id: item
            for item in snapshot.spec.required_validation_commands
        }
        required = snapshot.node_spec(node_id).validation_command_ids
        if not required:
            raise SupervisorError(
                "admitted node has no required trusted validation command"
            )
        if not node.start_head:
            raise SupervisorError("admitted node has no immutable start head")
        snapshot = self._consume(
            snapshot,
            BudgetToken.VALIDATION_EXECUTION,
            node_id=node_id,
            label=f"{node.state.value}:{required[0]}",
        )
        snapshot, actor, lease = self._acquire_actor(
            snapshot,
            node_id,
            ActorRole.VALIDATOR,
            native_worker=False,
        )
        try:
            root = Path(snapshot.spec.worktree).resolve(strict=True)
            candidate_head = subprocess.run(
                ("git", "rev-parse", "HEAD"),
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=30,
            ).stdout.strip().casefold()
            for index, command_id in enumerate(required):
                if index:
                    snapshot = self._consume(
                        snapshot,
                        BudgetToken.VALIDATION_EXECUTION,
                        node_id=node_id,
                        label=f"{node.state.value}:{command_id}",
                    )
                command = commands[command_id]
                working = Path(command.working_directory).expanduser().resolve(
                    strict=True
                )
                if working != root:
                    raise EvidenceError(
                        "validation command working_directory differs from the exact worktree"
                    )
                trusted = TrustedCommand(
                    executable=command.executable,
                    arguments=command.arguments,
                    working_directory=str(working),
                    environment_allowlist=command.environment_allowlist,
                    environment={},
                    timeout_seconds=command.timeout_seconds,
                    output_limit_bytes=command.output_limit_bytes,
                    candidate_head=candidate_head,
                    expected_working_tree=command.expected_worktree_condition,
                    expected_status_sha256=command.expected_status_sha256,
                    required_exit_code=command.required_exit_code,
                )
                result = execute_trusted_command(trusted)
                payload = result.to_dict()
                self.store.record_evidence(
                    Evidence(
                        evidence_id=_stable_id(
                            "validation", campaign_id, node_id, command_id, candidate_head
                        ),
                        campaign_id=campaign_id,
                        node_id=node_id,
                        kind=EvidenceKind.VALIDATION,
                        digest=result.evidence_sha256,
                        payload=payload,
                        candidate_head=candidate_head,
                    )
                )
            node_repository = exact_repository_evidence(
                root,
                base_sha=str(node.start_head),
                candidate_head=candidate_head,
            )
            self._validate_scope(snapshot, node_id, node_repository)
            campaign_repository = exact_repository_evidence(
                root,
                base_sha=snapshot.spec.base_sha,
                candidate_head=candidate_head,
            )
            self.store.record_evidence(
                Evidence(
                    evidence_id=_stable_id(
                        "repository-node-local",
                        campaign_id,
                        node_id,
                        node.start_head,
                        candidate_head,
                    ),
                    campaign_id=campaign_id,
                    node_id=node_id,
                    kind=EvidenceKind.REPOSITORY,
                    digest=str(node_repository["evidence_sha256"]),
                    payload=node_repository,
                    candidate_head=candidate_head,
                )
            )
            self.store.record_evidence(
                Evidence(
                    evidence_id=_stable_id(
                        "repository-campaign-cumulative",
                        campaign_id,
                        node_id,
                        snapshot.spec.base_sha,
                        candidate_head,
                    ),
                    campaign_id=campaign_id,
                    node_id=node_id,
                    kind=EvidenceKind.REPOSITORY,
                    digest=str(campaign_repository["evidence_sha256"]),
                    payload=campaign_repository,
                    candidate_head=candidate_head,
                )
            )
            snapshot = self.store.get_snapshot(campaign_id)
            event_type = (
                EventType.VALIDATION_PASSED
                if node.state is NodeState.VALIDATING
                else EventType.REVALIDATION_PASSED
            )
            token_payload: dict[str, Any] = {
                "candidate_head": candidate_head,
                "candidate_tree": campaign_repository["tree_sha"],
                "candidate_diff_digest": campaign_repository["diff_sha256"],
                "candidate_node_diff_digest": node_repository["diff_sha256"],
            }
            if event_type is EventType.REVALIDATION_PASSED:
                token_payload.update(
                    {
                        "budget_token": BudgetToken.CLOSURE_DISPATCH.value,
                        "operation_id": _stable_id("closure-dispatch", campaign_id, node_id),
                    }
                )
            snapshot = self._apply(
                self._event(
                    snapshot,
                    event_type,
                    label=candidate_head,
                    node_id=node_id,
                    actor_id=actor.actor_id,
                    fencing_epoch=lease.fencing_epoch,
                    payload=token_payload,
                )
            )
            action = "CANDIDATE_FROZEN" if event_type is EventType.VALIDATION_PASSED else "CLOSURE_READY"
        except (EvidenceError, HostScopeError, OSError, subprocess.SubprocessError) as exc:
            failed_evidence = (
                exc.evidence
                if isinstance(exc, ValidationFailure)
                and getattr(exc, "evidence", None) is not None
                else None
            )
            if failed_evidence is not None:
                payload = failed_evidence.to_dict()
                self.store.record_evidence(
                    Evidence(
                        evidence_id=_stable_id(
                            "validation-failed",
                            campaign_id,
                            node_id,
                            str(command_id),
                            str(failed_evidence.evidence_sha256),
                        ),
                        campaign_id=campaign_id,
                        node_id=node_id,
                        kind=EvidenceKind.VALIDATION,
                        digest=str(failed_evidence.evidence_sha256),
                        payload=payload,
                        candidate_head=str(failed_evidence.candidate_head),
                    )
                )
            snapshot = self.store.get_snapshot(campaign_id)
            event_type = (
                EventType.VALIDATION_FAILED
                if node.state is NodeState.VALIDATING
                else EventType.REVALIDATION_FAILED
            )
            snapshot = self._apply(
                self._event(
                    snapshot,
                    event_type,
                    label=type(exc).__name__,
                    node_id=node_id,
                    actor_id=actor.actor_id,
                    fencing_epoch=lease.fencing_epoch,
                    payload={"reason": str(exc)},
                )
            )
            action = "VALIDATION_FAILED"
        finally:
            current = self.store.get_snapshot(campaign_id)
            try:
                self.store.release_lease(
                    lease.lease_id,
                    request_id=_stable_id("release", lease.lease_id),
                    expected_revision=current.revision,
                    authority_epoch=current.authority_epoch,
                    cancellation_epoch=current.cancellation_epoch,
                )
            except Exception:
                if current.state not in {
                    CampaignState.CANCELLED,
                    CampaignState.FAILED,
                    CampaignState.COMPLETED,
                }:
                    raise
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            action,
            node_id,
        )

    def begin_review(self, campaign_id: str, node_id: str) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        node = snapshot.node(node_id)
        if node.state is not NodeState.CANDIDATE_FROZEN:
            raise SupervisorError("candidate is not frozen")
        review_id = _stable_id("review", campaign_id, node_id, node.candidate_head)
        snapshot = self._apply(
            self._event(
                snapshot,
                EventType.START_REVIEW,
                label=review_id,
                node_id=node_id,
                payload={
                    "review_id": review_id,
                    "review_cohort": list(snapshot.spec.required_review_cohort),
                    "budget_token": BudgetToken.REVIEW_DISPATCH.value,
                    "operation_id": _stable_id("review-dispatch", review_id),
                },
            )
        )
        leases = []
        try:
            for reviewer_id in snapshot.spec.required_review_cohort:
                leases.append(
                    self.dispatch_worker(
                        campaign_id,
                        node_id,
                        ActorRole.REVIEWER,
                        reviewer_id=reviewer_id,
                    ).lease_id
                )
        except BudgetError as exc:
            self._fail_budget(self.store.get_snapshot(campaign_id), exc)
            raise
        except AttemptAlreadyReserved:
            raise
        except BaseException as exc:
            self._fail_dispatch(
                campaign_id, node_id, ActorRole.REVIEWER, exc
            )
            raise
        current = self.store.get_snapshot(campaign_id)
        return SupervisorDecision(
            campaign_id,
            current.revision,
            current.state.value,
            "REVIEW_DISPATCHED",
            node_id,
            wait_event="review_complete",
            details={"review_id": review_id, "leases": leases},
        )

    def freeze_review(
        self,
        campaign_id: str,
        node_id: str,
        *,
        receipts: Sequence[Mapping[str, Any]],
        findings: Sequence[Mapping[str, Any]],
    ) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        node = snapshot.node(node_id)
        if node.state is not NodeState.CHECKS_AND_REVIEW:
            raise SupervisorError("node is not waiting for the review cohort")
        verified_findings = self._verify_review_receipts(
            snapshot,
            node,
            receipts,
            findings,
            expected_role=ActorRole.REVIEWER,
        )
        review_id = _stable_id("review", campaign_id, node_id, node.candidate_head)
        snapshot = self._apply(
            self._event(
                snapshot,
                EventType.FREEZE_FINDINGS,
                label=canonical_json_digest(verified_findings),
                node_id=node_id,
                payload={
                    "findings": verified_findings,
                    "receipts": list(receipts),
                },
            )
        )
        self.store.record_review(
            review_id,
            campaign_id,
            node_id,
            canonical_json_digest(snapshot.spec.required_review_cohort),
            str(node.candidate_head),
            "FROZEN",
            {"receipts": list(receipts)},
        )
        blocking = [item.finding_id for item in snapshot.node(node_id).findings if item.blocking]
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "FINDINGS_FROZEN",
            node_id,
            wait_event=("repair_authorized" if blocking else None),
            details={"blocking_finding_ids": blocking},
        )

    def _verify_review_receipts(
        self,
        snapshot: CampaignSnapshot,
        node: NodeSnapshot,
        receipts: Sequence[Mapping[str, Any]],
        findings: Sequence[Mapping[str, Any]],
        *,
        expected_role: ActorRole,
    ) -> list[dict[str, Any]]:
        """Bind a complete cohort to persisted native terminal evidence."""

        required = tuple(snapshot.spec.required_review_cohort)
        reviewers = tuple(str(item.get("reviewer_id", "")) for item in receipts)
        if (
            len(reviewers) != len(set(reviewers))
            or tuple(sorted(reviewers)) != tuple(sorted(required))
        ):
            raise SupervisorError("review receipts do not match the exact frozen cohort")
        verified_findings: list[dict[str, Any]] = []
        finding_ids: set[str] = set()
        for receipt in receipts:
            reviewer_id = str(receipt.get("reviewer_id", ""))
            candidate_head = str(receipt.get("candidate_head", ""))
            receipt_digest = str(receipt.get("receipt_digest", ""))
            if candidate_head != node.candidate_head:
                raise SupervisorError("review receipt is bound to another candidate head")
            if not receipt_digest:
                raise SupervisorError("review receipt has no evidence digest")
            evidence = self.store.find_evidence_by_digest(
                snapshot.spec.campaign_id,
                node.node_id,
                receipt_digest,
                kind=EvidenceKind.REVIEW,
                candidate_head=candidate_head,
            )
            if len(evidence) != 1:
                raise SupervisorError(
                    "review receipt digest has no unique persisted terminal evidence"
                )
            payload = dict(evidence[0].payload)
            result_payload = payload.get("result_payload")
            if not isinstance(result_payload, Mapping):
                raise SupervisorError("review receipt evidence has no structured result")
            if (
                payload.get("receipt_digest") != receipt_digest
                or payload.get("campaign_id") != snapshot.spec.campaign_id
                or payload.get("node_id") != node.node_id
                or payload.get("candidate_head") != candidate_head
                or payload.get("role") != expected_role.value
                or result_payload.get("reviewer_id") != reviewer_id
                or result_payload.get("candidate_head") != candidate_head
            ):
                raise SupervisorError("review receipt evidence binding is inconsistent")
            actor_id = str(payload.get("actor_id", ""))
            lease_id = str(payload.get("lease_id", ""))
            try:
                actor = self.store.get_actor(actor_id)
                lease = self.store.get_lease(lease_id)
            except Exception as exc:
                raise SupervisorError("review receipt actor evidence is missing") from exc
            if (
                actor.role is not expected_role
                or actor.campaign_id != snapshot.spec.campaign_id
                or actor.node_id != node.node_id
                or actor.principal_id != reviewer_id
                or lease.actor_id != actor_id
                or lease.campaign_id != snapshot.spec.campaign_id
                or lease.node_id != node.node_id
            ):
                raise SupervisorError("review receipt actor authority is inconsistent")
            try:
                self.store.verify_terminal_evidence_attestation(
                    evidence[0].evidence_id,
                    digest=receipt_digest,
                    actor_id=actor_id,
                    lease_id=lease_id,
                    native_thread_id=str(payload.get("native_thread_id", "")),
                    native_turn_id=str(payload.get("native_turn_id", "")),
                    principal_id=reviewer_id,
                )
            except Exception as exc:
                raise SupervisorError(
                    "review receipt lacks exact native terminal attestation"
                ) from exc
            evidence_resolved = tuple(
                str(item)
                for item in result_payload.get("resolved_finding_ids", ())
            )
            supplied_resolved = tuple(
                str(item) for item in receipt.get("resolved_finding_ids", ())
            )
            if evidence_resolved != supplied_resolved:
                raise SupervisorError("review receipt resolution evidence was changed")
            raw_findings = result_payload.get("findings", ())
            if not isinstance(raw_findings, (list, tuple)):
                raise SupervisorError("review receipt findings evidence is not an array")
            for raw in raw_findings:
                if not isinstance(raw, Mapping):
                    raise SupervisorError("review receipt finding evidence is not an object")
                item = dict(raw)
                finding_id = str(item.get("finding_id", ""))
                if not finding_id or finding_id in finding_ids:
                    raise SupervisorError(
                        "review receipt finding identifiers must be nonempty and unique"
                    )
                finding_ids.add(finding_id)
                verified_findings.append(item)
        supplied = sorted((dict(item) for item in findings), key=lambda item: str(item.get("finding_id", "")))
        verified = sorted(verified_findings, key=lambda item: str(item.get("finding_id", "")))
        if canonical_json_digest(supplied) != canonical_json_digest(verified):
            raise SupervisorError("review findings differ from persisted receipt evidence")
        return verified_findings

    def collect_review_cohort(
        self,
        lease_ids: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        runtimes = [self._workers.get(lease_id) for lease_id in lease_ids]
        if not runtimes or any(runtime is None for runtime in runtimes):
            raise SupervisorError("review cohort contains an unknown in-process lease")
        bound = [runtime for runtime in runtimes if runtime is not None]
        campaign_ids = {runtime.lease.campaign_id for runtime in bound}
        node_ids = {runtime.lease.node_id for runtime in bound}
        roles = {runtime.role for runtime in bound}
        if (
            len(campaign_ids) != 1
            or len(node_ids) != 1
            or len(roles) != 1
            or not roles.issubset(
                {ActorRole.REVIEWER, ActorRole.CLOSURE_REVIEWER}
            )
        ):
            raise SupervisorError("review cohort lease bindings are inconsistent")
        campaign_id = next(iter(campaign_ids))
        node_id = next(iter(node_ids))
        role = next(iter(roles))
        try:
            return self._collect_review_cohort_unchecked(
                lease_ids, timeout=timeout
            )
        except BaseException as exc:
            self._fail_dispatch(campaign_id, node_id, role, exc)
            raise

    def _collect_review_cohort_unchecked(
        self,
        lease_ids: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        receipts: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for lease_id in lease_ids:
            runtime = self._workers.get(lease_id)
            if runtime is None or runtime.reviewer_id is None:
                raise SupervisorError("review worker has no frozen reviewer identity")
            receipt = self.complete_worker(lease_id, timeout=timeout)
            payload = dict(receipt.result_payload)
            reviewer_id = str(payload.get("reviewer_id", runtime.reviewer_id))
            candidate_head = str(payload.get("candidate_head", receipt.candidate_head))
            if reviewer_id != runtime.reviewer_id:
                raise SupervisorError("reviewer result changed its bound reviewer identity")
            if candidate_head != receipt.candidate_head:
                raise SupervisorError("reviewer result changed its bound candidate head")
            receipts.append(
                {
                    "reviewer_id": reviewer_id,
                    "candidate_head": candidate_head,
                    "receipt_digest": receipt.receipt_digest,
                    "verdict": payload.get("verdict"),
                    "resolved_finding_ids": list(
                        payload.get("resolved_finding_ids", [])
                        if isinstance(payload.get("resolved_finding_ids", []), list)
                        else []
                    ),
                }
            )
            raw_findings = payload.get("findings", [])
            if not isinstance(raw_findings, list):
                raise SupervisorError("reviewer findings must be an array")
            for raw in raw_findings:
                if not isinstance(raw, Mapping):
                    raise SupervisorError("review finding must be an object")
                item = dict(raw)
                finding_id = str(item.get("finding_id", ""))
                if not finding_id or finding_id in seen:
                    raise SupervisorError("review finding identifiers must be nonempty and unique")
                seen.add(finding_id)
                findings.append(item)
        return receipts, findings

    def authorize_repair(
        self,
        campaign_id: str,
        node_id: str,
        *,
        authorization_receipt: Mapping[str, Any],
    ) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        node = snapshot.node(node_id)
        blockers = [item.finding_id for item in node.findings if item.blocking]
        receipt = dict(authorization_receipt)
        receipt_id = str(receipt.get("receipt_id", "")).strip()
        receipt_digest = canonical_json_digest(receipt)
        event = self._event(
            snapshot,
            EventType.AUTHORIZE_REPAIR,
            label=receipt_id or receipt_digest,
            node_id=node_id,
            payload={
                "finding_ids": blockers,
                "authorization_receipt_id": receipt_id,
                "authorization_receipt_digest": receipt_digest,
            },
        )
        snapshot, _ = self.store.apply_human_authorized_event(event, receipt)
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "REPAIR_AUTHORIZED",
            node_id,
        )

    def authorize_publication(
        self,
        campaign_id: str,
        node_id: str,
        *,
        authorization_receipt: Mapping[str, Any],
    ) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        receipt = dict(authorization_receipt)
        receipt_id = str(receipt.get("receipt_id", "")).strip()
        receipt_digest = canonical_json_digest(receipt)
        event = self._event(
            snapshot,
            EventType.AUTHORIZE_PUBLICATION,
            label=receipt_id or receipt_digest,
            node_id=node_id,
            payload={
                "authorization_receipt_id": receipt_id,
                "authorization_receipt_digest": receipt_digest,
            },
        )
        snapshot, _ = self.store.apply_human_authorized_event(event, receipt)
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "PUBLICATION_AUTHORIZED",
            node_id,
        )

    def complete_closure(
        self,
        campaign_id: str,
        node_id: str,
        *,
        receipts: Sequence[Mapping[str, Any]] = (),
        resolved_finding_ids: Sequence[str],
        findings: Sequence[Mapping[str, Any]],
    ) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        node_before = snapshot.node(node_id)
        if node_before.state is not NodeState.CLOSURE:
            raise SupervisorError("node is not in closure")
        verified_findings = self._verify_review_receipts(
            snapshot,
            node_before,
            receipts,
            findings,
            expected_role=ActorRole.CLOSURE_REVIEWER,
        )
        resolution_sets = [
            {str(item) for item in receipt.get("resolved_finding_ids", ())}
            for receipt in receipts
        ]
        verified_resolved = (
            set.intersection(*resolution_sets) if resolution_sets else set()
        )
        if set(str(item) for item in resolved_finding_ids) != verified_resolved:
            raise SupervisorError(
                "closure resolution differs from the exact verified cohort receipts"
            )
        snapshot = self._apply(
            self._event(
                snapshot,
                EventType.COMPLETE_CLOSURE,
                label=canonical_json_digest(
                    {
                        "resolved": sorted(verified_resolved),
                        "findings": verified_findings,
                    }
                ),
                node_id=node_id,
                payload={
                    "resolved_finding_ids": sorted(verified_resolved),
                    "findings": verified_findings,
                },
            )
        )
        closure_review_id = _stable_id(
            "closure-review", campaign_id, node_id, node_before.candidate_head
        )
        self.store.record_review(
            closure_review_id,
            campaign_id,
            node_id,
            canonical_json_digest(snapshot.spec.required_review_cohort),
            str(node_before.candidate_head),
            "CLOSED",
            {"receipts": list(receipts)},
        )
        node = snapshot.node(node_id)
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "CLOSURE_PASSED" if node.state is NodeState.READY_TO_PUBLISH else "CLOSURE_FAILED",
            node_id,
        )

    @staticmethod
    def _next_publication(snapshot: CampaignSnapshot, node: NodeSnapshot) -> str | None:
        sequence = tuple(snapshot.spec.publication_authority.get("required_effects", ()))
        completed = tuple(getattr(node, "completed_publication_effects", ()))
        return str(sequence[len(completed)]) if len(completed) < len(sequence) else None

    def start_publication(
        self,
        campaign_id: str,
        node_id: str,
        *,
        hosted_wakeup_id: str | None = None,
    ) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        node = snapshot.node(node_id)
        if (
            not bool(snapshot.spec.publication_authority.get("automated", False))
            and not node.publication_authorization_receipt_id
        ):
            raise SupervisorError(
                "manual publication requires a one-use human authorization receipt"
            )
        kind_text = self._next_publication(snapshot, node)
        if kind_text is None:
            raise SupervisorError("immutable publication sequence is empty or complete")
        kind = EffectKind(kind_text)
        required_checks = (
            tuple(
                str(item)
                for item in snapshot.spec.publication_authority.get(
                    "required_hosted_checks", ()
                )
            )
            if kind is EffectKind.MERGE
            else ()
        )
        hosted_checks: Mapping[str, Any] | None = None
        if required_checks:
            snapshot, reserved = self._reserve_attempt(
                snapshot,
                BudgetToken.HOSTED_CHECK_WAKEUP,
                node_id=node_id,
                attempt_identity=(
                    f"hosted-checks:{node.candidate_head}:"
                    f"{hosted_wakeup_id or 'initial'}"
                ),
            )
            if not reserved:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "HOSTED_CHECKS_PENDING",
                    node_id,
                    wait_event="hosted_checks",
                    details={"reason": "a new named hosted-check wakeup is required"},
                )
            repository = str(
                snapshot.spec.publication_authority.get("repository", "")
            ).strip()
            if not repository:
                remote = snapshot.spec.repository_remote.replace("\\", "/")
                remote = remote[:-4] if remote.casefold().endswith(".git") else remote
                repository = remote.rstrip("/").rsplit(":", 1)[-1]
                if "/" in repository:
                    repository = "/".join(repository.split("/")[-2:])
            try:
                hosted_checks = self.hosted_check_collector(
                    snapshot.spec.worktree,
                    repository=repository,
                    candidate_head=str(node.candidate_head),
                )
            except HostedEvidenceError as exc:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "HOSTED_CHECKS_UNAVAILABLE",
                    node_id,
                    wait_event="hosted_checks",
                    details={"reason": str(exc)},
                )
            except EvidenceError as exc:
                snapshot = self._apply(
                    self._event(
                        snapshot,
                        EventType.FAIL_NODE,
                        label="hosted-check-head-race",
                        node_id=node_id,
                        payload={"reason": f"hosted check collection failed: {exc}"},
                    )
                )
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "PUBLICATION_PREFLIGHT_FAILED",
                    node_id,
                    details={"reason": str(exc), "effect_kind": kind.value},
                )
            hosted_digest = str(hosted_checks.get("evidence_sha256", ""))
            self.store.record_evidence(
                Evidence(
                    evidence_id=_stable_id(
                        "hosted-checks",
                        campaign_id,
                        node_id,
                        node.candidate_head,
                        hosted_digest,
                    ),
                    campaign_id=campaign_id,
                    node_id=node_id,
                    kind=EvidenceKind.HOSTED_CHECK,
                    digest=hosted_digest,
                    payload=dict(hosted_checks),
                    candidate_head=str(node.candidate_head),
                )
            )
            by_name = {
                str(item.get("name")): item
                for item in hosted_checks.get("checks", ())
                if isinstance(item, Mapping)
            }
            missing_or_pending = [
                name
                for name in required_checks
                if name not in by_name or by_name[name].get("status") != "completed"
            ]
            if missing_or_pending:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "HOSTED_CHECKS_PENDING",
                    node_id,
                    wait_event="hosted_checks",
                    details={"pending": missing_or_pending},
                )
        snapshot = self._consume(
            snapshot,
            BudgetToken.VALIDATION_EXECUTION,
            node_id=node_id,
            label=f"publication-preflight:{kind.value}:{node.candidate_head}",
        )
        try:
            preflight = self.publication_preflight_runner(
                snapshot.spec.worktree,
                expected_remote=snapshot.spec.repository_remote,
                candidate_head=str(node.candidate_head),
                hosted_checks=hosted_checks,
                required_checks=required_checks,
            )
        except EvidenceError as exc:
            snapshot = self._apply(
                self._event(
                    snapshot,
                    EventType.FAIL_NODE,
                    label=f"publication-preflight:{kind.value}",
                    node_id=node_id,
                    payload={"reason": f"publication preflight failed: {exc}"},
                )
            )
            return SupervisorDecision(
                campaign_id,
                snapshot.revision,
                snapshot.state.value,
                "PUBLICATION_PREFLIGHT_FAILED",
                node_id,
                details={"reason": str(exc), "effect_kind": kind.value},
            )
        self.store.record_evidence(
            Evidence(
                evidence_id=_stable_id(
                    "publication-preflight", campaign_id, node_id, kind.value, node.candidate_head
                ),
                campaign_id=campaign_id,
                node_id=node_id,
                kind=EvidenceKind.PUBLICATION,
                digest=str(preflight.get("evidence_sha256", "")),
                payload=dict(preflight),
                candidate_head=str(node.candidate_head),
            )
        )
        payloads = snapshot.spec.publication_authority.get("effect_payloads", {})
        effect_payload = dict(payloads.get(kind.value, {}))
        effect_payload.update(
            {
                "root": snapshot.spec.worktree,
                "candidate_head": node.candidate_head,
                "head": node.candidate_head,
                "repository_remote": snapshot.spec.repository_remote,
            }
        )
        effect_payload.setdefault("branch", snapshot.spec.branch)
        effect_payload.setdefault("head_branch", snapshot.spec.branch)
        effect_payload.setdefault(
            "base", str(snapshot.spec.publication_authority.get("base_branch", "main"))
        )
        operation_id = _stable_id(
            "publish", campaign_id, node_id, kind.value, node.candidate_head
        )
        snapshot = self._apply(
            self._event(
                snapshot,
                EventType.START_PUBLISH,
                label=operation_id,
                node_id=node_id,
                payload={
                    "effect_kind": kind.value,
                    "budget_token": {
                        EffectKind.PUSH: BudgetToken.PUSH,
                        EffectKind.CREATE_PULL_REQUEST: BudgetToken.PULL_REQUEST_CREATION,
                        EffectKind.UPSERT_COMMENT: BudgetToken.COMMENT,
                        EffectKind.MERGE: BudgetToken.MERGE,
                    }[kind].value,
                    "operation_id": operation_id,
                    "effect_payload": effect_payload,
                },
            )
        )
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "PUBLICATION_PREPARED",
            node_id,
            details={"operation_id": operation_id, "effect_kind": kind.value},
        )

    def execute_publication(
        self, campaign_id: str, node_id: str, operation_id: str
    ) -> SupervisorDecision:
        if self.effect_driver is None:
            raise SupervisorError("external effect driver is unavailable")
        record = self.effect_driver.run(operation_id)
        return self._complete_publication_record(campaign_id, node_id, record)

    def _complete_publication_record(
        self,
        campaign_id: str,
        node_id: str,
        record: Mapping[str, Any],
    ) -> SupervisorDecision:
        operation_id = str(record["operation_id"])
        snapshot = self.store.get_snapshot(campaign_id)
        if record["state"] == EffectState.CONFIRMED.value:
            event_type = EventType.PUBLISH_CONFIRMED
            payload = {
                "candidate_head": snapshot.node(node_id).candidate_head,
                "effect_kind": record["kind"],
                "operation_id": operation_id,
                "result": record.get("result"),
            }
        elif record["state"] in {
            EffectState.PREPARED.value,
            EffectState.EXECUTING.value,
            EffectState.AMBIGUOUS.value,
        }:
            return SupervisorDecision(
                campaign_id,
                snapshot.revision,
                snapshot.state.value,
                "PUBLICATION_AMBIGUOUS",
                node_id,
                wait_event="reconcile",
                details={"operation_id": operation_id},
            )
        else:
            event_type = EventType.PUBLISH_FAILED
            payload = {"reason": str(record.get("result") or record["state"])}
        snapshot = self._apply(
            self._event(
                snapshot,
                event_type,
                label=operation_id,
                node_id=node_id,
                payload=payload,
            )
        )
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "PUBLICATION_CONFIRMED" if event_type is EventType.PUBLISH_CONFIRMED else "PUBLICATION_FAILED",
            node_id,
            details={"operation_id": operation_id},
        )

    def reconcile(self, operation_id: str) -> Mapping[str, Any]:
        if self.effect_driver is None:
            raise SupervisorError("external effect driver is unavailable")
        record = self.store.get_effect(operation_id)
        snapshot = self.store.get_snapshot(str(record["campaign_id"]))
        if record["state"] in {
            EffectState.CONFIRMED.value,
            EffectState.FAILED.value,
            EffectState.CANCELLED.value,
        }:
            if snapshot.state not in {
                CampaignState.COMPLETED,
                CampaignState.FAILED,
                CampaignState.CANCELLED,
            }:
                _, reserved = self._reserve_attempt(
                    snapshot,
                    BudgetToken.NO_OP_ATTEMPT,
                    node_id=record.get("node_id"),
                    attempt_identity=f"terminal-effect:{operation_id}",
                )
                if not reserved:
                    raise SupervisorError(
                        "terminal-effect reconciliation no-op already recorded"
                    )
            return record
        if snapshot.state not in {
            CampaignState.COMPLETED,
            CampaignState.FAILED,
            CampaignState.CANCELLED,
        }:
            _, reserved = self._reserve_attempt(
                snapshot,
                BudgetToken.RECONCILIATION,
                node_id=record.get("node_id"),
                attempt_identity=f"reconcile:{operation_id}:revision:{snapshot.revision}",
            )
            if not reserved:
                raise SupervisorError(
                    "reconciliation attempt is already executing or recorded"
                )
        return self.effect_driver.reconcile(operation_id)

    def cancel(self, campaign_id: str, *, reason: str = "STOP") -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        uncertain = self.store.list_outbox(
            state=EffectState.EXECUTING, campaign_id=campaign_id
        )
        owned_processes = self.store.list_actor_identities(
            campaign_id=campaign_id, active_only=True
        )
        snapshot = self.store.cancel_campaign(
            campaign_id,
            request_id=_stable_id("cancel", campaign_id, snapshot.revision, reason),
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            reason=reason,
        )
        for lease in list(self._workers.values()):
            if lease.lease.campaign_id == campaign_id:
                self.host.interrupt(lease.lease.lease_id)
                self._workers.pop(lease.lease.lease_id, None)
        for identity in owned_processes:
            pid = identity.get("host_pid")
            if isinstance(pid, int) and pid > 0:
                try:
                    terminate_verified_process_tree(
                        pid,
                        identity.get("native_identity")
                        if isinstance(identity.get("native_identity"), Mapping)
                        else None,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
        if self.effect_driver is not None:
            for record in uncertain:
                # STOP is a human cancellation command.  The store has already
                # fenced the campaign and converted an executing mutation to
                # AMBIGUOUS.  Reconciliation is query-only and must still run
                # even when the campaign exhausted its autonomous budget.
                self.effect_driver.reconcile(str(record["operation_id"]))
        return SupervisorDecision(
            campaign_id,
            snapshot.revision,
            snapshot.state.value,
            "CANCELLED",
        )

    def recover(self) -> Mapping[str, Any]:
        orphaned = self.store.list_active_actor_identities()
        affected = sorted(
            {
                str(item["actor"]["campaign_id"])
                for item in orphaned
                if isinstance(item.get("actor"), Mapping)
                and item["actor"].get("campaign_id")
            }
        )
        for identity in orphaned:
            pid = identity.get("host_pid")
            if isinstance(pid, int) and pid > 0:
                try:
                    terminate_verified_process_tree(
                        pid,
                        identity.get("native_identity")
                        if isinstance(identity.get("native_identity"), Mapping)
                        else None,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
        recovered = self.store.recover_after_restart()
        failed_nodes: list[dict[str, str]] = []
        for campaign_id in affected:
            snapshot = self.store.get_snapshot(campaign_id)
            if snapshot.state in {
                CampaignState.COMPLETED,
                CampaignState.FAILED,
                CampaignState.CANCELLED,
            } or not snapshot.active_node_id:
                continue
            node_id = snapshot.active_node_id
            snapshot = self._apply(
                self._event(
                    snapshot,
                    EventType.FAIL_NODE,
                    label="restart-orphaned-native-lease",
                    node_id=node_id,
                    payload={"reason": "native worker lease orphaned by supervisor restart"},
                )
            )
            failed_nodes.append({"campaign_id": campaign_id, "node_id": node_id})
        reconciled: list[Mapping[str, Any]] = []
        if self.effect_driver is not None:
            for item in self.store.list_outbox(state=EffectState.AMBIGUOUS):
                reconciled.append(self.reconcile(str(item["operation_id"])))
        return {
            **recovered,
            "orphaned_actor_identities": len(orphaned),
            "failed_nodes": failed_nodes,
            "reconciled": reconciled,
        }

    def step(self, campaign_id: str) -> SupervisorDecision:
        try:
            return self._step(campaign_id)
        except BudgetError as exc:
            snapshot = self.store.get_snapshot(campaign_id)
            if snapshot.state in {
                CampaignState.COMPLETED,
                CampaignState.FAILED,
                CampaignState.CANCELLED,
            }:
                failed = next(
                    (
                        item
                        for item in snapshot.nodes
                        if item.state is NodeState.FAILED_EXACT_NODE
                    ),
                    None,
                )
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "BUDGET_EXHAUSTED",
                    failed.node_id if failed is not None else None,
                    details={"reason": str(exc)},
                )
            return self._fail_budget(snapshot, exc)

    def _step(self, campaign_id: str) -> SupervisorDecision:
        snapshot = self.store.get_snapshot(campaign_id)
        try:
            self._check_deadline(snapshot)
        except SupervisorDeadlineExceeded:
            return self._fail_deadline(snapshot)
        if snapshot.state is CampaignState.APPROVED:
            snapshot = self._apply(
                self._event(snapshot, EventType.START, label="run")
            )
            return SupervisorDecision(
                campaign_id, snapshot.revision, snapshot.state.value, "CAMPAIGN_STARTED"
            )
        if snapshot.state in {
            CampaignState.DRAFT,
            CampaignState.WAITING_EXTERNAL,
            CampaignState.WAITING_HUMAN,
        }:
            event = "approve" if snapshot.state is CampaignState.DRAFT else "external_event"
            return SupervisorDecision(
                campaign_id,
                snapshot.revision,
                snapshot.state.value,
                "YIELD",
                snapshot.active_node_id,
                wait_event=event,
            )
        if snapshot.state in {
            CampaignState.COMPLETED,
            CampaignState.FAILED,
            CampaignState.CANCELLED,
        }:
            return SupervisorDecision(
                campaign_id, snapshot.revision, snapshot.state.value, "TERMINAL"
            )
        node = self.select_next_approved_node(snapshot)
        if node is not None:
            worktree = Path(snapshot.spec.worktree).expanduser().resolve(strict=True)
            start_head = subprocess.run(
                ("git", "rev-parse", "HEAD"),
                cwd=worktree,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=30,
            ).stdout.strip().casefold()
            snapshot = self._apply(
                self._event(
                    snapshot,
                    EventType.ADMIT_NODE,
                    label="next-approved",
                    node_id=node.node_id,
                    payload={"start_head": start_head},
                )
            )
            return SupervisorDecision(
                campaign_id,
                snapshot.revision,
                snapshot.state.value,
                "NODE_ADMITTED",
                node.node_id,
            )
        if not snapshot.active_node_id:
            raise SupervisorError("running campaign has no eligible node and is not terminal")
        node = snapshot.node(snapshot.active_node_id)
        try:
            self._check_deadline(snapshot, node)
        except SupervisorDeadlineExceeded:
            return self._fail_deadline(snapshot, node)
        if node.state is NodeState.ADMITTED:
            lease = self.dispatch_worker(
                campaign_id,
                node.node_id,
                ActorRole.IMPLEMENTER,
                lifecycle_event=EventType.START_IMPLEMENTATION,
            )
            current = self.store.get_snapshot(campaign_id)
            return SupervisorDecision(
                campaign_id,
                current.revision,
                current.state.value,
                "IMPLEMENTER_DISPATCHED",
                node.node_id,
                wait_event="worker_terminal",
                details={"lease_id": lease.lease_id},
            )
        if node.state in {NodeState.VALIDATING, NodeState.REVALIDATING}:
            return self.validate_node(campaign_id, node.node_id)
        if node.state is NodeState.CANDIDATE_FROZEN:
            return self.begin_review(campaign_id, node.node_id)
        if node.state is NodeState.FINDINGS_FROZEN:
            blockers = [item for item in node.findings if item.blocking]
            if blockers:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "YIELD",
                    node.node_id,
                    wait_event="repair_authorized",
                    details={"finding_ids": [item.finding_id for item in blockers]},
                )
            snapshot = self._apply(
                self._event(
                    snapshot,
                    EventType.MARK_READY_TO_PUBLISH,
                    label="clean-review",
                    node_id=node.node_id,
                )
            )
            return SupervisorDecision(
                campaign_id,
                snapshot.revision,
                snapshot.state.value,
                "READY_TO_PUBLISH",
                node.node_id,
            )
        if node.state is NodeState.REPAIR_AUTHORIZED:
            lease = self.dispatch_worker(
                campaign_id,
                node.node_id,
                ActorRole.REPAIRER,
                lifecycle_event=EventType.START_REPAIR,
            )
            current = self.store.get_snapshot(campaign_id)
            return SupervisorDecision(
                campaign_id,
                current.revision,
                current.state.value,
                "REPAIRER_DISPATCHED",
                node.node_id,
                wait_event="worker_terminal",
                details={"lease_id": lease.lease_id},
            )
        if node.state is NodeState.CLOSURE:
            prior_closure_actors = [
                item
                for item in self.store.list_actor_identities(
                    campaign_id=campaign_id, active_only=False
                )
                if item.get("actor", {}).get("role")
                == ActorRole.CLOSURE_REVIEWER.value
                and item.get("actor", {}).get("node_id") == node.node_id
            ]
            if prior_closure_actors:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "YIELD",
                    node.node_id,
                    wait_event="closure_complete",
                    details={"reason": "closure cohort already dispatched"},
                )
            leases = []
            try:
                for reviewer_id in snapshot.spec.required_review_cohort:
                    leases.append(
                        self.dispatch_worker(
                            campaign_id,
                            node.node_id,
                            ActorRole.CLOSURE_REVIEWER,
                            reviewer_id=reviewer_id,
                        ).lease_id
                    )
            except BudgetError as exc:
                self._fail_budget(self.store.get_snapshot(campaign_id), exc)
                raise
            except AttemptAlreadyReserved:
                raise
            except BaseException as exc:
                self._fail_dispatch(
                    campaign_id, node.node_id, ActorRole.CLOSURE_REVIEWER, exc
                )
                raise
            current = self.store.get_snapshot(campaign_id)
            return SupervisorDecision(
                campaign_id,
                current.revision,
                current.state.value,
                "CLOSURE_DISPATCHED",
                node.node_id,
                wait_event="closure_complete",
                details={"leases": leases},
            )
        if node.state is NodeState.READY_TO_PUBLISH:
            if (
                not snapshot.spec.publication_authority.get("automated", False)
                and not node.publication_authorization_receipt_id
            ):
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "YIELD",
                    node.node_id,
                    wait_event="publication_authorized",
                )
            return self.start_publication(campaign_id, node.node_id)
        if node.state is NodeState.PUBLISHING:
            outbox = self.store.list_outbox(campaign_id=campaign_id)
            pending = [
                item
                for item in outbox
                if item.get("node_id") == node.node_id
                and item.get("operation_id")
                == node.pending_publication_operation_id
            ]
            if len(pending) != 1:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "YIELD",
                    node.node_id,
                    wait_event="external_effect",
                )
            item = pending[0]
            state = EffectState(str(item["state"]))
            if state is EffectState.PREPARED:
                return self.execute_publication(
                    campaign_id, node.node_id, str(item["operation_id"])
                )
            if state in {EffectState.EXECUTING, EffectState.AMBIGUOUS}:
                return SupervisorDecision(
                    campaign_id,
                    snapshot.revision,
                    snapshot.state.value,
                    "PUBLICATION_AMBIGUOUS",
                    node.node_id,
                    wait_event="reconcile",
                    details={"operation_id": str(item["operation_id"])},
                )
            return self._complete_publication_record(campaign_id, node.node_id, item)
        wait_by_state = {
            NodeState.IMPLEMENTING: "worker_terminal",
            NodeState.CHECKS_AND_REVIEW: "review_complete",
            NodeState.REPAIRING: "worker_terminal",
        }
        if node.state in wait_by_state:
            return SupervisorDecision(
                campaign_id,
                snapshot.revision,
                snapshot.state.value,
                "YIELD",
                node.node_id,
                wait_event=wait_by_state[node.state],
            )
        raise SupervisorError(f"no deterministic action for node state {node.state.value}")
