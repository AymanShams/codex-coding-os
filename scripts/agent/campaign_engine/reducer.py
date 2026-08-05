"""The single pure lifecycle reducer for Coding OS campaigns."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .model import (
    AuthorityError,
    BudgetBalance,
    BudgetError,
    BudgetReceipt,
    BudgetToken,
    CampaignMode,
    CampaignSnapshot,
    CampaignState,
    CancellationError,
    CancellationState,
    EffectKind,
    EffectState,
    Event,
    EventType,
    ExternalEffectIntent,
    FindingOrigin,
    FencingError,
    ModelValidationError,
    NodeSnapshot,
    NodeState,
    RevisionConflict,
    TransitionError,
    replace_node,
    unique_findings,
)


TERMINAL_CAMPAIGN_STATES = {
    CampaignState.COMPLETED,
    CampaignState.FAILED,
    CampaignState.CANCELLED,
}
TERMINAL_NODE_STATES = {
    NodeState.DONE,
    NodeState.FAILED_EXACT_NODE,
    NodeState.CANCELLED,
}


_DISPATCH_TOKEN = {
    EventType.START_IMPLEMENTATION: BudgetToken.CHILD_START,
    EventType.START_REVIEW: BudgetToken.REVIEW_DISPATCH,
    EventType.START_REPAIR: BudgetToken.REPAIR_DISPATCH,
    EventType.REVALIDATION_PASSED: BudgetToken.CLOSURE_DISPATCH,
    EventType.START_CLOSURE: BudgetToken.CLOSURE_DISPATCH,
}


_PUBLISH_TOKEN = {
    EffectKind.PUSH: BudgetToken.PUSH,
    EffectKind.CREATE_PULL_REQUEST: BudgetToken.PULL_REQUEST_CREATION,
    EffectKind.UPSERT_COMMENT: BudgetToken.COMMENT,
    EffectKind.MERGE: BudgetToken.MERGE,
}


_FENCED_EVENTS = {
    EventType.START_IMPLEMENTATION,
    EventType.IMPLEMENTATION_COMPLETED,
    EventType.REQUEST_VALIDATION_CORRECTION,
    EventType.VALIDATION_PASSED,
    EventType.VALIDATION_FAILED,
    EventType.START_REPAIR,
    EventType.REPAIR_COMPLETED,
    EventType.REVALIDATION_PASSED,
    EventType.REVALIDATION_FAILED,
    EventType.START_CLOSURE,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TransitionError(message)


def _node_for(snapshot: CampaignSnapshot, event: Event) -> NodeSnapshot:
    if not event.node_id:
        raise TransitionError(f"{event.event_type.value} requires node_id")
    return snapshot.node(event.node_id)


def _replace_node(snapshot: CampaignSnapshot, node: NodeSnapshot) -> CampaignSnapshot:
    return replace_node(snapshot, node)


def _check_fence(snapshot: CampaignSnapshot, event: Event, node: NodeSnapshot) -> None:
    if event.event_type not in _FENCED_EVENTS:
        return
    if snapshot.spec.mode is CampaignMode.MANUAL and node.lease_actor_id is None:
        return
    if node.lease_actor_id is None:
        raise FencingError("automated node action requires an active actor lease")
    if event.actor_id != node.lease_actor_id:
        raise FencingError("event actor does not own the active node lease")
    if event.fencing_epoch != node.fencing_epoch:
        raise FencingError("event fencing epoch is stale or absent")


def _consume_budget(
    snapshot: CampaignSnapshot, token: BudgetToken, request_id: str
) -> CampaignSnapshot:
    if any(receipt.request_id == request_id for receipt in snapshot.budget_receipts):
        raise BudgetError("budget request identifier was already consumed")
    if snapshot.autonomous_rank_remaining <= 0:
        raise BudgetError("autonomous-operation rank is exhausted")
    updated: list[BudgetBalance] = []
    found = False
    for balance in snapshot.budgets:
        if balance.token is token:
            found = True
            if balance.remaining <= 0:
                raise BudgetError(f"attempt budget is exhausted: {token.value}")
            updated.append(replace(balance, consumed=balance.consumed + 1))
        else:
            updated.append(balance)
    if not found:
        raise BudgetError(f"attempt budget is not declared: {token.value}")
    return replace(
        snapshot,
        budgets=tuple(updated),
        budget_receipts=snapshot.budget_receipts
        + (BudgetReceipt(request_id, token, snapshot.revision + 1),),
        autonomous_rank_remaining=snapshot.autonomous_rank_remaining - 1,
    )


def _consume_automated(
    snapshot: CampaignSnapshot, event: Event, token: BudgetToken
) -> CampaignSnapshot:
    if snapshot.spec.mode is CampaignMode.MANUAL:
        return snapshot
    supplied = event.payload.get("budget_token")
    if supplied != token.value:
        raise BudgetError(
            f"{event.event_type.value} requires budget token {token.value}"
        )
    return _consume_budget(snapshot, token, event.event_id)


def _effect(
    snapshot: CampaignSnapshot,
    event: Event,
    kind: EffectKind,
    *,
    external: bool,
    payload: Mapping[str, Any] | None = None,
    operation_id: str | None = None,
) -> ExternalEffectIntent:
    identity = operation_id or str(event.payload.get("operation_id", "")).strip()
    if not identity:
        raise TransitionError(f"{event.event_type.value} requires stable operation_id")
    return ExternalEffectIntent(
        operation_id=identity,
        campaign_id=snapshot.spec.campaign_id,
        node_id=event.node_id,
        kind=kind,
        state=EffectState.PREPARED,
        payload=payload if payload is not None else event.payload.get("effect_payload", {}),
        authority_epoch=snapshot.authority_epoch,
        cancellation_epoch=snapshot.cancellation_epoch,
        fencing_epoch=event.fencing_epoch,
        external=external,
    )


def _dispatch_effect(
    snapshot: CampaignSnapshot, event: Event, role: str
) -> tuple[CampaignSnapshot, tuple[ExternalEffectIntent, ...]]:
    token = _DISPATCH_TOKEN[event.event_type]
    next_snapshot = _consume_automated(snapshot, event, token)
    operation_id = str(event.payload.get("operation_id", "")).strip()
    if not operation_id:
        if snapshot.spec.mode is CampaignMode.AUTOMATED:
            raise TransitionError("automated dispatch requires stable operation_id")
        return next_snapshot, ()
    payload = dict(event.payload.get("effect_payload", {}))
    payload["role"] = role
    payload["campaign_id"] = snapshot.spec.campaign_id
    payload["node_id"] = event.node_id
    return next_snapshot, (
        _effect(
            next_snapshot,
            event,
            EffectKind.DISPATCH_WORKER,
            external=False,
            payload=payload,
            operation_id=operation_id,
        ),
    )


def _fail_exact_node(
    snapshot: CampaignSnapshot, node: NodeSnapshot, reason: str
) -> CampaignSnapshot:
    failed = replace(
        node,
        state=NodeState.FAILED_EXACT_NODE,
        failure_reason=reason,
        fencing_epoch=node.fencing_epoch + 1,
        lease_actor_id=None,
    )
    return replace(
        _replace_node(snapshot, failed),
        state=CampaignState.FAILED,
        active_node_id=None,
        failure_reason=reason,
    )


def _finish_revision(
    previous: CampaignSnapshot,
    next_snapshot: CampaignSnapshot,
    effects: tuple[ExternalEffectIntent, ...],
) -> tuple[CampaignSnapshot, tuple[ExternalEffectIntent, ...]]:
    if next_snapshot.spec is not previous.spec and next_snapshot.spec != previous.spec:
        raise TransitionError("approved campaign specification is immutable")
    if next_snapshot.spec.specification_digest != previous.spec.specification_digest:
        raise TransitionError("approved campaign digest is immutable")
    if next_snapshot.autonomous_rank_remaining > previous.autonomous_rank_remaining:
        raise TransitionError("autonomous-operation rank may never increase")
    receipt_delta = len(next_snapshot.budget_receipts) - len(previous.budget_receipts)
    rank_delta = previous.autonomous_rank_remaining - next_snapshot.autonomous_rank_remaining
    if receipt_delta != rank_delta or receipt_delta not in {0, 1}:
        raise TransitionError(
            "each autonomous operation must consume exactly one rank and budget token"
        )
    next_snapshot = replace(next_snapshot, revision=previous.revision + 1)
    return next_snapshot, effects


def reduce(
    snapshot: CampaignSnapshot, event: Event
) -> tuple[CampaignSnapshot, tuple[ExternalEffectIntent, ...]]:
    """Apply one event without consulting any ambient authority or doing I/O."""

    if event.campaign_id != snapshot.spec.campaign_id:
        raise TransitionError("event campaign identifier does not match snapshot")
    if event.expected_revision != snapshot.revision:
        raise RevisionConflict(
            f"expected revision {event.expected_revision}, current {snapshot.revision}"
        )
    if event.authority_epoch != snapshot.authority_epoch:
        raise AuthorityError(
            f"expected authority epoch {snapshot.authority_epoch}, got {event.authority_epoch}"
        )
    if event.cancellation_epoch != snapshot.cancellation_epoch:
        raise CancellationError(
            f"expected cancellation epoch {snapshot.cancellation_epoch}, "
            f"got {event.cancellation_epoch}"
        )
    if snapshot.cancellation_state is CancellationState.CANCELLED:
        raise CancellationError("cancelled campaigns cannot resume or accept late results")
    if snapshot.state in TERMINAL_CAMPAIGN_STATES:
        raise TransitionError(f"campaign is terminal: {snapshot.state.value}")

    event_type = event.event_type
    effects: tuple[ExternalEffectIntent, ...] = ()
    next_snapshot = snapshot

    if event_type is EventType.APPROVE:
        _require(snapshot.state is CampaignState.DRAFT, "only draft campaigns can be approved")
        snapshot.spec.verify_digest()
        supplied = event.payload.get("specification_digest")
        _require(
            supplied == snapshot.spec.specification_digest,
            "approval must bind the exact specification digest",
        )
        next_snapshot = replace(snapshot, state=CampaignState.APPROVED)

    elif event_type is EventType.START:
        _require(snapshot.state is CampaignState.APPROVED, "campaign is not approved")
        next_snapshot = replace(snapshot, state=CampaignState.RUNNING)

    elif event_type in {EventType.WAIT_EXTERNAL, EventType.WAIT_HUMAN}:
        _require(snapshot.state is CampaignState.RUNNING, "only running campaigns may wait")
        state = (
            CampaignState.WAITING_EXTERNAL
            if event_type is EventType.WAIT_EXTERNAL
            else CampaignState.WAITING_HUMAN
        )
        next_snapshot = replace(snapshot, state=state)

    elif event_type is EventType.RESUME:
        _require(
            snapshot.state in {CampaignState.WAITING_EXTERNAL, CampaignState.WAITING_HUMAN},
            "campaign is not waiting",
        )
        next_snapshot = replace(snapshot, state=CampaignState.RUNNING)

    elif event_type is EventType.ADVANCE_AUTHORITY:
        _require(
            snapshot.state is CampaignState.WAITING_HUMAN,
            "authority may rotate only while waiting for a human event",
        )
        requested = int(event.payload.get("new_authority_epoch", -1))
        _require(requested == snapshot.authority_epoch + 1, "authority epoch must advance by one")
        nodes = tuple(replace(node, lease_actor_id=None) for node in snapshot.nodes)
        next_snapshot = replace(snapshot, authority_epoch=requested, nodes=nodes)

    elif event_type is EventType.CANCEL:
        cancelled_nodes = tuple(
            node
            if node.state in TERMINAL_NODE_STATES
            else replace(node, state=NodeState.CANCELLED, lease_actor_id=None)
            for node in snapshot.nodes
        )
        next_snapshot = replace(
            snapshot,
            state=CampaignState.CANCELLED,
            cancellation_state=CancellationState.CANCELLED,
            cancellation_epoch=snapshot.cancellation_epoch + 1,
            nodes=cancelled_nodes,
            active_node_id=None,
            failure_reason=str(event.payload.get("reason", "STOP")),
        )
        effects = (
            _effect(
                snapshot,
                event,
                EffectKind.INTERRUPT_WORKER,
                external=False,
                payload={"reason": next_snapshot.failure_reason},
                operation_id=f"{event.event_id}:interrupt",
            ),
            _effect(
                snapshot,
                event,
                EffectKind.RECONCILE_EFFECT,
                external=False,
                payload={"reason": "campaign_cancelled"},
                operation_id=f"{event.event_id}:reconcile",
            ),
        )

    elif event_type is EventType.CONSUME_BUDGET:
        _require(snapshot.state is CampaignState.RUNNING, "campaign is not running")
        try:
            token = BudgetToken(str(event.payload["token"]))
        except (KeyError, ValueError) as exc:
            raise BudgetError("consume-budget event has an invalid token") from exc
        next_snapshot = _consume_budget(snapshot, token, event.event_id)

    elif event_type is EventType.ADMIT_NODE:
        _require(snapshot.state is CampaignState.RUNNING, "campaign is not running")
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.PENDING, "node is not pending")
        _require(
            snapshot.active_node_id in {None, node.node_id},
            "deterministic supervisor already has an active node",
        )
        node_spec = snapshot.node_spec(node.node_id)
        for dependency in node_spec.dependencies:
            _require(
                snapshot.node(dependency).state is NodeState.DONE,
                f"dependency is not done: {dependency}",
            )
        start_head = str(event.payload.get("start_head", "")).strip().casefold()
        _require(
            len(start_head) == 40
            and all(character in "0123456789abcdef" for character in start_head),
            "node admission requires the exact 40-character start_head",
        )
        _require(node.start_head is None, "node start head is already frozen")
        next_snapshot = replace(
            _replace_node(
                snapshot,
                replace(node, state=NodeState.ADMITTED, start_head=start_head),
            ),
            active_node_id=node.node_id,
        )

    elif event_type is EventType.ACQUIRE_LEASE:
        _require(snapshot.state is CampaignState.RUNNING, "campaign is not running")
        node = _node_for(snapshot, event)
        _require(node.state not in TERMINAL_NODE_STATES, "terminal node cannot be leased")
        _require(node.lease_actor_id is None, "node already has an active actor lease")
        _require(bool(event.actor_id), "lease acquisition requires actor_id")
        _require(
            event.fencing_epoch is not None
            and event.fencing_epoch > node.fencing_epoch,
            "lease fencing epoch must advance beyond the node fence",
        )
        next_snapshot = _replace_node(
            snapshot,
            replace(
                node,
                fencing_epoch=int(event.fencing_epoch),
                lease_actor_id=event.actor_id,
            ),
        )

    elif event_type is EventType.RELEASE_LEASE:
        node = _node_for(snapshot, event)
        _require(node.lease_actor_id is not None, "node has no active actor lease")
        if event.actor_id != node.lease_actor_id or event.fencing_epoch != node.fencing_epoch:
            raise FencingError("only the exact fenced lease owner can release a lease")
        next_snapshot = _replace_node(snapshot, replace(node, lease_actor_id=None))

    elif event_type is EventType.START_IMPLEMENTATION:
        _require(snapshot.state is CampaignState.RUNNING, "campaign is not running")
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.ADMITTED, "node is not admitted")
        _require(bool(node.start_head), "node has no immutable admitted start head")
        _require(node.implementation_attempts == 0, "implementation generation already used")
        next_snapshot, effects = _dispatch_effect(snapshot, event, "IMPLEMENTER")
        node = next_snapshot.node(node.node_id)
        next_snapshot = _replace_node(
            next_snapshot,
            replace(
                node,
                state=NodeState.IMPLEMENTING,
                implementation_attempts=1,
            ),
        )

    elif event_type is EventType.IMPLEMENTATION_COMPLETED:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.IMPLEMENTING, "node is not implementing")
        next_snapshot = _replace_node(snapshot, replace(node, state=NodeState.VALIDATING))

    elif event_type is EventType.REQUEST_VALIDATION_CORRECTION:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.VALIDATING, "node is not validating")
        _require(
            node.validation_corrections == 0,
            "pre-review validation correction has already been used",
        )
        _require(node.candidate_head is None, "frozen candidate cannot return to implementation")
        next_snapshot = _replace_node(
            snapshot,
            replace(
                node,
                state=NodeState.IMPLEMENTING,
                validation_corrections=1,
            ),
        )

    elif event_type is EventType.VALIDATION_PASSED:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.VALIDATING, "node is not validating")
        head = str(event.payload.get("candidate_head", "")).strip()
        tree = str(event.payload.get("candidate_tree", "")).strip()
        diff_digest = str(event.payload.get("candidate_diff_digest", "")).strip()
        node_diff_digest = str(
            event.payload.get("candidate_node_diff_digest", "")
        ).strip()
        _require(
            bool(head and tree and diff_digest and node_diff_digest),
            "candidate identity must be complete and include campaign and node-local diff evidence",
        )
        next_snapshot = _replace_node(
            snapshot,
            replace(
                node,
                state=NodeState.CANDIDATE_FROZEN,
                candidate_head=head,
                candidate_tree=tree,
                candidate_diff_digest=diff_digest,
                candidate_node_diff_digest=node_diff_digest,
            ),
        )

    elif event_type is EventType.VALIDATION_FAILED:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.VALIDATING, "node is not validating")
        next_snapshot = _fail_exact_node(
            snapshot, node, str(event.payload.get("reason", "validation_failed"))
        )

    elif event_type is EventType.START_REVIEW:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.CANDIDATE_FROZEN, "candidate is not frozen")
        _require(node.review_generations == 0, "review generation already used")
        cohort = tuple(str(item) for item in event.payload.get("review_cohort", ()))
        _require(
            cohort == snapshot.spec.required_review_cohort,
            "review dispatch must bind the exact required cohort",
        )
        next_snapshot, effects = _dispatch_effect(snapshot, event, "REVIEWER")
        node = next_snapshot.node(node.node_id)
        next_snapshot = _replace_node(
            next_snapshot,
            replace(node, state=NodeState.CHECKS_AND_REVIEW, review_generations=1),
        )

    elif event_type is EventType.FREEZE_FINDINGS:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.CHECKS_AND_REVIEW, "node is not in review")
        _require(not node.findings, "finding set is already frozen")
        findings = unique_findings(
            event.payload.get("findings", ()), origin=FindingOrigin.REVIEW
        )
        next_snapshot = _replace_node(
            snapshot,
            replace(node, state=NodeState.FINDINGS_FROZEN, findings=findings),
        )

    elif event_type is EventType.MARK_READY_TO_PUBLISH:
        node = _node_for(snapshot, event)
        _require(
            node.state is NodeState.FINDINGS_FROZEN,
            "node is not eligible for clean publication before findings freeze",
        )
        _require(not any(item.blocking for item in node.findings), "blocking findings require repair")
        next_snapshot = _replace_node(
            snapshot, replace(node, state=NodeState.READY_TO_PUBLISH)
        )

    elif event_type is EventType.AUTHORIZE_REPAIR:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.FINDINGS_FROZEN, "findings are not frozen")
        _require(any(item.blocking for item in node.findings), "there are no blockers to repair")
        _require(node.repair_attempts == 0, "combined repair already used")
        frozen_ids = tuple(item.finding_id for item in node.findings if item.blocking)
        requested = tuple(str(item) for item in event.payload.get("finding_ids", ()))
        _require(requested == frozen_ids, "repair must bind the exact frozen blocker set")
        _require(
            bool(str(event.payload.get("authorization_receipt_id", "")).strip())
            and bool(str(event.payload.get("authorization_receipt_digest", "")).strip()),
            "repair requires a consumed one-use human authorization receipt",
        )
        next_snapshot = _replace_node(
            snapshot, replace(node, state=NodeState.REPAIR_AUTHORIZED)
        )

    elif event_type is EventType.START_REPAIR:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.REPAIR_AUTHORIZED, "repair is not authorized")
        _require(node.repair_attempts == 0, "combined repair already used")
        next_snapshot, effects = _dispatch_effect(snapshot, event, "REPAIRER")
        node = next_snapshot.node(node.node_id)
        next_snapshot = _replace_node(
            next_snapshot,
            replace(node, state=NodeState.REPAIRING, repair_attempts=1),
        )

    elif event_type is EventType.REPAIR_COMPLETED:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.REPAIRING, "node is not repairing")
        next_snapshot = _replace_node(snapshot, replace(node, state=NodeState.REVALIDATING))

    elif event_type is EventType.REVALIDATION_PASSED:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.REVALIDATING, "node is not revalidating")
        _require(node.closure_generations == 0, "closure generation already used")
        head = str(event.payload.get("candidate_head", "")).strip()
        tree = str(event.payload.get("candidate_tree", "")).strip()
        diff_digest = str(event.payload.get("candidate_diff_digest", "")).strip()
        node_diff_digest = str(
            event.payload.get("candidate_node_diff_digest", "")
        ).strip()
        _require(
            bool(head and tree and diff_digest and node_diff_digest),
            "revalidated candidate identity must be complete and include campaign and node-local diff evidence",
        )
        next_snapshot, effects = _dispatch_effect(snapshot, event, "CLOSURE_REVIEWER")
        node = next_snapshot.node(node.node_id)
        next_snapshot = _replace_node(
            next_snapshot,
            replace(
                node,
                state=NodeState.CLOSURE,
                closure_generations=1,
                candidate_head=head,
                candidate_tree=tree,
                candidate_diff_digest=diff_digest,
                candidate_node_diff_digest=node_diff_digest,
            ),
        )

    elif event_type is EventType.START_CLOSURE:
        raise TransitionError(
            "closure can begin only through trusted REVALIDATION_PASSED evidence"
        )

    elif event_type is EventType.REVALIDATION_FAILED:
        node = _node_for(snapshot, event)
        _check_fence(snapshot, event, node)
        _require(node.state is NodeState.REVALIDATING, "node is not revalidating")
        next_snapshot = _fail_exact_node(
            snapshot, node, str(event.payload.get("reason", "revalidation_failed"))
        )

    elif event_type is EventType.COMPLETE_CLOSURE:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.CLOSURE, "node is not in closure")
        new_findings = unique_findings(
            event.payload.get("findings", ()), origin=FindingOrigin.CLOSURE
        )
        original_ids = {item.finding_id for item in node.findings if item.blocking}
        resolved = tuple(str(item) for item in event.payload.get("resolved_finding_ids", ()))
        _require(set(resolved).issubset(original_ids), "closure resolved unknown finding identifiers")
        remaining = original_ids - set(resolved)
        closed = replace(
            node,
            closure_findings=new_findings,
            resolved_finding_ids=resolved,
        )
        if remaining or new_findings:
            reasons: list[str] = []
            if remaining:
                reasons.append("remaining=" + ",".join(sorted(remaining)))
            if new_findings:
                reasons.append(
                    "new=" + ",".join(item.finding_id for item in new_findings)
                )
            next_snapshot = _fail_exact_node(
                snapshot, closed, "closure_failed:" + ";".join(reasons)
            )
        else:
            next_snapshot = _replace_node(
                snapshot, replace(closed, state=NodeState.READY_TO_PUBLISH)
            )

    elif event_type is EventType.AUTHORIZE_PUBLICATION:
        node = _node_for(snapshot, event)
        _require(
            not bool(snapshot.spec.publication_authority.get("automated", False)),
            "automated publication does not accept a human authorization event",
        )
        _require(node.state is NodeState.READY_TO_PUBLISH, "node is not ready to publish")
        _require(
            node.publication_authorization_receipt_id is None,
            "publication authorization receipt was already consumed",
        )
        receipt_id = str(event.payload.get("authorization_receipt_id", "")).strip()
        receipt_digest = str(
            event.payload.get("authorization_receipt_digest", "")
        ).strip()
        _require(
            bool(receipt_id and receipt_digest),
            "publication requires a consumed one-use human authorization receipt",
        )
        next_snapshot = _replace_node(
            snapshot,
            replace(
                node,
                publication_authorization_receipt_id=receipt_id,
                publication_authorization_receipt_digest=receipt_digest,
            ),
        )

    elif event_type is EventType.START_PUBLISH:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.READY_TO_PUBLISH, "node is not ready to publish")
        if not bool(snapshot.spec.publication_authority.get("automated", False)):
            _require(
                bool(
                    node.publication_authorization_receipt_id
                    and node.publication_authorization_receipt_digest
                ),
                "manual publication requires a consumed human authorization receipt",
            )
        _require(
            node.pending_publication_operation_id is None,
            "node already has a prepared publication effect",
        )
        try:
            kind = EffectKind(str(event.payload["effect_kind"]))
        except (KeyError, ValueError) as exc:
            raise TransitionError("publication effect kind is invalid") from exc
        _require(kind in _PUBLISH_TOKEN, "effect is not a publication mutation")
        allowed = tuple(snapshot.spec.publication_authority.get("allowed_effects", ()))
        _require(kind.value in allowed, "publication effect lacks recorded authority")
        required = tuple(
            snapshot.spec.publication_authority.get("required_effects", allowed)
        )
        position = len(node.completed_publication_effects)
        _require(position < len(required), "publication sequence is already complete")
        _require(
            kind.value == required[position],
            f"publication effect is out of order; expected {required[position]}",
        )
        operation_id = str(event.payload.get("operation_id", "")).strip()
        _require(bool(operation_id), "publication requires stable operation_id")
        _require(
            operation_id not in node.publication_operation_ids,
            "publication operation identity was already confirmed",
        )
        next_snapshot = _consume_automated(snapshot, event, _PUBLISH_TOKEN[kind])
        effect_payload = dict(event.payload.get("effect_payload", {}))
        effect_payload.setdefault("candidate_head", node.candidate_head)
        _require(
            effect_payload.get("candidate_head") == node.candidate_head,
            "publication must bind the exact frozen candidate head",
        )
        effects = (
            _effect(
                next_snapshot,
                event,
                kind,
                external=True,
                payload=effect_payload,
            ),
        )
        next_snapshot = _replace_node(
            next_snapshot,
            replace(
                next_snapshot.node(node.node_id),
                state=NodeState.PUBLISHING,
                pending_publication_effect=kind.value,
                pending_publication_operation_id=operation_id,
            ),
        )

    elif event_type is EventType.PUBLISH_CONFIRMED:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.PUBLISHING, "node is not publishing")
        _require(
            event.payload.get("candidate_head") == node.candidate_head,
            "publication confirmation head differs from frozen candidate",
        )
        _require(
            event.payload.get("operation_id") == node.pending_publication_operation_id,
            "publication confirmation operation differs from prepared effect",
        )
        _require(
            bool(node.pending_publication_effect),
            "publishing node has no pending effect identity",
        )
        completed = node.completed_publication_effects + (
            str(node.pending_publication_effect),
        )
        operation_ids = node.publication_operation_ids + (
            str(node.pending_publication_operation_id),
        )
        allowed = tuple(snapshot.spec.publication_authority.get("allowed_effects", ()))
        required = tuple(
            snapshot.spec.publication_authority.get("required_effects", allowed)
        )
        publication_complete = completed == required
        published_node = replace(
            node,
            state=(NodeState.DONE if publication_complete else NodeState.READY_TO_PUBLISH),
            completed_publication_effects=completed,
            publication_operation_ids=operation_ids,
            pending_publication_effect=None,
            pending_publication_operation_id=None,
        )
        next_snapshot = _replace_node(snapshot, published_node)
        if publication_complete and all(
            item.state is NodeState.DONE for item in next_snapshot.nodes
        ):
            next_snapshot = replace(
                next_snapshot, state=CampaignState.COMPLETED, active_node_id=None
            )
        elif publication_complete:
            next_snapshot = replace(next_snapshot, active_node_id=None)

    elif event_type is EventType.PUBLISH_FAILED:
        node = _node_for(snapshot, event)
        _require(node.state is NodeState.PUBLISHING, "node is not publishing")
        next_snapshot = _fail_exact_node(
            snapshot, node, str(event.payload.get("reason", "publication_failed"))
        )

    elif event_type is EventType.FAIL_NODE:
        node = _node_for(snapshot, event)
        _require(node.state not in TERMINAL_NODE_STATES, "node is already terminal")
        next_snapshot = _fail_exact_node(
            snapshot, node, str(event.payload.get("reason", "node_failed"))
        )

    else:  # pragma: no cover - Enum exhaustiveness defense
        raise ModelValidationError(f"unimplemented event type: {event_type.value}")

    return _finish_revision(snapshot, next_snapshot, effects)
