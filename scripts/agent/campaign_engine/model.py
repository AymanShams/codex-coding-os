"""Immutable domain model for the single Coding OS campaign engine.

The model deliberately contains no ambient time, filesystem, process, network,
Git, or identifier generation.  Every value that can affect authority or a
lifecycle decision is supplied by the caller and included in canonical JSON.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, TypeVar


PROTOCOL_VERSION = "ccos-campaign-v1"
SCHEMA_COMPATIBILITY = "campaign-store-v1"
HOST_CAPABILITY_PROBE_VERSION = "native-bind-before-turn-scoped-tools-v3"


class CampaignError(RuntimeError):
    """Base class for deterministic campaign failures."""


class ModelValidationError(CampaignError, ValueError):
    """A model value violates the campaign contract."""


class TransitionError(CampaignError):
    """An event is not legal from the current lifecycle state."""


class RevisionConflict(CampaignError):
    """The caller did not target the exact current revision."""


class AuthorityError(CampaignError):
    """The caller did not target the exact authority epoch."""


class CancellationError(CampaignError):
    """The caller did not target the exact cancellation epoch."""


class FencingError(CampaignError):
    """The caller's worker lease fence is stale or absent."""


class BudgetError(CampaignError):
    """An autonomous operation has no remaining durable budget."""


class StoreError(CampaignError):
    """Persistent storage failed a campaign invariant."""


class RequestConflict(StoreError):
    """A request identifier was rebound to a different payload."""


class CampaignMode(str, Enum):
    MANUAL = "MANUAL"
    AUTOMATED = "AUTOMATED"


class ObjectiveKind(str, Enum):
    PRODUCT_CODE = "PRODUCT_CODE"
    PRODUCT_DOCUMENTATION = "PRODUCT_DOCUMENTATION"
    CONTROL_RUNTIME = "CONTROL_RUNTIME"


class CampaignState(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeState(str, Enum):
    PENDING = "PENDING"
    ADMITTED = "ADMITTED"
    IMPLEMENTING = "IMPLEMENTING"
    VALIDATING = "VALIDATING"
    CANDIDATE_FROZEN = "CANDIDATE_FROZEN"
    CHECKS_AND_REVIEW = "CHECKS_AND_REVIEW"
    FINDINGS_FROZEN = "FINDINGS_FROZEN"
    REPAIR_AUTHORIZED = "REPAIR_AUTHORIZED"
    REPAIRING = "REPAIRING"
    REVALIDATING = "REVALIDATING"
    CLOSURE = "CLOSURE"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHING = "PUBLISHING"
    DONE = "DONE"
    FAILED_EXACT_NODE = "FAILED_EXACT_NODE"
    CANCELLED = "CANCELLED"


class ActorRole(str, Enum):
    IMPLEMENTER = "IMPLEMENTER"
    VALIDATOR = "VALIDATOR"
    REVIEWER = "REVIEWER"
    REPAIRER = "REPAIRER"
    CLOSURE_REVIEWER = "CLOSURE_REVIEWER"
    SUPERVISOR = "SUPERVISOR"
    PARENT = "PARENT"


class LeaseState(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class EvidenceKind(str, Enum):
    REPOSITORY = "REPOSITORY"
    GIT = "GIT"
    VALIDATION = "VALIDATION"
    HOSTED_CHECK = "HOSTED_CHECK"
    REVIEW = "REVIEW"
    PUBLICATION = "PUBLICATION"
    INSTALLATION = "INSTALLATION"
    LEGACY_ARCHIVE = "LEGACY_ARCHIVE"


class FindingOrigin(str, Enum):
    REVIEW = "REVIEW"
    CLOSURE = "CLOSURE"


class EffectKind(str, Enum):
    DISPATCH_WORKER = "DISPATCH_WORKER"
    INTERRUPT_WORKER = "INTERRUPT_WORKER"
    RECONCILE_EFFECT = "RECONCILE_EFFECT"
    PUSH = "PUSH"
    CREATE_PULL_REQUEST = "CREATE_PULL_REQUEST"
    UPSERT_COMMENT = "UPSERT_COMMENT"
    MERGE = "MERGE"
    EXACT_FILE_REPLACE = "EXACT_FILE_REPLACE"


class EffectState(str, Enum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"


class CancellationState(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


class BudgetToken(str, Enum):
    CHILD_CREATION = "CHILD_CREATION"
    CHILD_START = "CHILD_START"
    VALIDATION_EXECUTION = "VALIDATION_EXECUTION"
    REVIEW_DISPATCH = "REVIEW_DISPATCH"
    REPAIR_DISPATCH = "REPAIR_DISPATCH"
    CLOSURE_DISPATCH = "CLOSURE_DISPATCH"
    HOSTED_CHECK_WAKEUP = "HOSTED_CHECK_WAKEUP"
    TRANSPORT_RETRY = "TRANSPORT_RETRY"
    RECONCILIATION = "RECONCILIATION"
    PUSH = "PUSH"
    PULL_REQUEST_CREATION = "PULL_REQUEST_CREATION"
    COMMENT = "COMMENT"
    MERGE = "MERGE"
    REJECTED_ATTEMPT = "REJECTED_ATTEMPT"
    NO_OP_ATTEMPT = "NO_OP_ATTEMPT"


class EventType(str, Enum):
    APPROVE = "APPROVE"
    START = "START"
    WAIT_EXTERNAL = "WAIT_EXTERNAL"
    WAIT_HUMAN = "WAIT_HUMAN"
    RESUME = "RESUME"
    ADMIT_NODE = "ADMIT_NODE"
    ACQUIRE_LEASE = "ACQUIRE_LEASE"
    RELEASE_LEASE = "RELEASE_LEASE"
    CONSUME_BUDGET = "CONSUME_BUDGET"
    START_IMPLEMENTATION = "START_IMPLEMENTATION"
    IMPLEMENTATION_COMPLETED = "IMPLEMENTATION_COMPLETED"
    REQUEST_VALIDATION_CORRECTION = "REQUEST_VALIDATION_CORRECTION"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    START_REVIEW = "START_REVIEW"
    FREEZE_FINDINGS = "FREEZE_FINDINGS"
    MARK_READY_TO_PUBLISH = "MARK_READY_TO_PUBLISH"
    AUTHORIZE_REPAIR = "AUTHORIZE_REPAIR"
    AUTHORIZE_PUBLICATION = "AUTHORIZE_PUBLICATION"
    START_REPAIR = "START_REPAIR"
    REPAIR_COMPLETED = "REPAIR_COMPLETED"
    REVALIDATION_PASSED = "REVALIDATION_PASSED"
    REVALIDATION_FAILED = "REVALIDATION_FAILED"
    START_CLOSURE = "START_CLOSURE"
    COMPLETE_CLOSURE = "COMPLETE_CLOSURE"
    START_PUBLISH = "START_PUBLISH"
    PUBLISH_CONFIRMED = "PUBLISH_CONFIRMED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    FAIL_NODE = "FAIL_NODE"
    ADVANCE_AUTHORITY = "ADVANCE_AUTHORITY"
    CANCEL = "CANCEL"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def canonical_json(value: Any) -> str:
    """Return the protocol's stable JSON representation."""

    return json.dumps(
        _primitive(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


E = TypeVar("E", bound=Enum)


def _enum(enum_type: type[E], value: E | str) -> E:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ModelValidationError(f"invalid {enum_type.__name__}: {value!r}") from exc


def _nonempty(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelValidationError(f"{name} must not be empty")
    return text


@dataclass(frozen=True)
class ValidationCommand:
    command_id: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    environment_allowlist: tuple[str, ...]
    timeout_seconds: int
    output_limit_bytes: int
    expected_worktree_condition: str
    expected_status_sha256: str | None = None
    required_exit_code: int = 0

    def __post_init__(self) -> None:
        _nonempty(self.command_id, "command_id")
        _nonempty(self.executable, "executable")
        _nonempty(self.working_directory, "working_directory")
        if self.expected_worktree_condition not in {"CLEAN", "EXACT_STATUS"}:
            raise ModelValidationError(
                "expected_worktree_condition must be CLEAN or EXACT_STATUS"
            )
        if self.expected_worktree_condition == "EXACT_STATUS":
            digest = str(self.expected_status_sha256 or "").casefold()
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ModelValidationError(
                    "EXACT_STATUS validation requires expected_status_sha256"
                )
            object.__setattr__(self, "expected_status_sha256", digest)
        elif self.expected_status_sha256 is not None:
            raise ModelValidationError(
                "CLEAN validation must not declare expected_status_sha256"
            )
        if self.timeout_seconds <= 0 or self.output_limit_bytes <= 0:
            raise ModelValidationError("validation timeout and output limit must be positive")
        if len(set(self.environment_allowlist)) != len(self.environment_allowlist):
            raise ModelValidationError("environment allowlist contains duplicates")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationCommand":
        return cls(
            command_id=str(value["command_id"]),
            executable=str(value["executable"]),
            arguments=tuple(str(item) for item in value.get("arguments", ())),
            working_directory=str(value["working_directory"]),
            environment_allowlist=tuple(
                str(item) for item in value.get("environment_allowlist", ())
            ),
            timeout_seconds=int(value["timeout_seconds"]),
            output_limit_bytes=int(value["output_limit_bytes"]),
            expected_worktree_condition=str(value["expected_worktree_condition"]),
            expected_status_sha256=(
                str(value["expected_status_sha256"])
                if value.get("expected_status_sha256") is not None
                else None
            ),
            required_exit_code=int(value.get("required_exit_code", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "executable": self.executable,
            "arguments": list(self.arguments),
            "working_directory": self.working_directory,
            "environment_allowlist": list(self.environment_allowlist),
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "expected_worktree_condition": self.expected_worktree_condition,
            "expected_status_sha256": self.expected_status_sha256,
            "required_exit_code": self.required_exit_code,
        }


@dataclass(frozen=True)
class AttemptBudget:
    token: BudgetToken
    limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "token", _enum(BudgetToken, self.token))
        if self.limit < 0:
            raise ModelValidationError("attempt budget limit must not be negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttemptBudget":
        return cls(_enum(BudgetToken, value["token"]), int(value["limit"]))

    def to_dict(self) -> dict[str, Any]:
        return {"token": self.token.value, "limit": self.limit}


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    objective: str
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    validation_command_ids: tuple[str, ...] = ()
    deadline_utc: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.node_id, "node_id")
        _nonempty(self.objective, "node objective")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ModelValidationError(f"node {self.node_id} has duplicate dependencies")
        if len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ModelValidationError(f"node {self.node_id} has duplicate allowed paths")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NodeSpec":
        if value.get("requires_review", True) is not True:
            raise ModelValidationError("every campaign node requires the frozen review cohort")
        return cls(
            node_id=str(value["node_id"]),
            objective=str(value["objective"]),
            dependencies=tuple(str(item) for item in value.get("dependencies", ())),
            allowed_paths=tuple(str(item) for item in value.get("allowed_paths", ())),
            validation_command_ids=tuple(
                str(item) for item in value.get("validation_command_ids", ())
            ),
            deadline_utc=(str(value["deadline_utc"]) if value.get("deadline_utc") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "objective": self.objective,
            "dependencies": list(self.dependencies),
            "allowed_paths": list(self.allowed_paths),
            "validation_command_ids": list(self.validation_command_ids),
            "deadline_utc": self.deadline_utc,
        }


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    specification_revision: int
    specification_digest: str
    authority_epoch: int
    cancellation_epoch: int
    mode: CampaignMode
    objective: str
    objective_kind: ObjectiveKind
    repository_remote: str
    git_root: str
    worktree: str
    branch: str
    base_sha: str
    allowed_paths: tuple[str, ...]
    nodes: tuple[NodeSpec, ...]
    required_validation_commands: tuple[ValidationCommand, ...]
    required_review_cohort: tuple[str, ...]
    publication_authority: Mapping[str, Any]
    attempt_budgets: tuple[AttemptBudget, ...]
    stop_conditions: tuple[str, ...]
    installed_source_commit: str
    installed_bundle_digest: str
    install_transaction: str
    protocol_version: str = PROTOCOL_VERSION
    schema_compatibility: str = SCHEMA_COMPATIBILITY
    host_capability_probe_version: str = HOST_CAPABILITY_PROBE_VERSION
    autonomous_rank: int = 1
    deadline_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum(CampaignMode, self.mode))
        object.__setattr__(self, "objective_kind", _enum(ObjectiveKind, self.objective_kind))
        object.__setattr__(self, "publication_authority", _freeze(self.publication_authority))
        for name in (
            "campaign_id",
            "objective",
            "repository_remote",
            "git_root",
            "worktree",
            "branch",
            "base_sha",
            "installed_source_commit",
            "installed_bundle_digest",
            "install_transaction",
            "protocol_version",
            "schema_compatibility",
            "host_capability_probe_version",
        ):
            _nonempty(getattr(self, name), name)
        if self.specification_revision <= 0:
            raise ModelValidationError("specification revision must be positive")
        if self.authority_epoch < 0 or self.cancellation_epoch < 0:
            raise ModelValidationError("authority and cancellation epochs must not be negative")
        if self.autonomous_rank <= 0:
            raise ModelValidationError("autonomous rank must be positive")
        if not self.nodes:
            raise ModelValidationError("campaign graph must contain at least one node")
        if not self.required_review_cohort:
            raise ModelValidationError("required review cohort must not be empty")
        if not self.stop_conditions:
            raise ModelValidationError("stop conditions must not be empty")
        if not self.publication_authority:
            raise ModelValidationError("publication authority must not be empty")
        allowed_effects = tuple(self.publication_authority.get("allowed_effects", ()))
        required_effects = tuple(
            self.publication_authority.get("required_effects", allowed_effects)
        )
        if not allowed_effects or not required_effects:
            raise ModelValidationError("publication authority requires a finite effect sequence")
        if len(set(allowed_effects)) != len(allowed_effects) or len(
            set(required_effects)
        ) != len(required_effects):
            raise ModelValidationError("publication effects must not contain duplicates")
        if not set(required_effects).issubset(set(allowed_effects)):
            raise ModelValidationError("required publication effects exceed allowed effects")
        try:
            parsed_effects = tuple(EffectKind(str(item)) for item in allowed_effects)
        except ValueError as exc:
            raise ModelValidationError("publication authority contains an invalid effect") from exc
        publication_effects = {
            EffectKind.PUSH,
            EffectKind.CREATE_PULL_REQUEST,
            EffectKind.UPSERT_COMMENT,
            EffectKind.MERGE,
        }
        allowed_external_effects = publication_effects | {
            EffectKind.EXACT_FILE_REPLACE
        }
        parsed_required = {EffectKind(str(item)) for item in required_effects}
        if not set(parsed_effects).issubset(allowed_external_effects):
            raise ModelValidationError(
                "publication authority may contain only external publication effects"
            )
        if not parsed_required.issubset(publication_effects):
            raise ModelValidationError(
                "required publication sequence contains a non-publication effect"
            )
        required_hosted_checks = tuple(
            str(item) for item in self.publication_authority.get("required_hosted_checks", ())
        )
        if any(not item.strip() for item in required_hosted_checks) or len(
            set(required_hosted_checks)
        ) != len(required_hosted_checks):
            raise ModelValidationError(
                "required hosted checks must be unique nonempty check names"
            )
        human_authorization = self.publication_authority.get(
            "human_authorization"
        )
        if human_authorization is not None:
            if not isinstance(human_authorization, Mapping):
                raise ModelValidationError(
                    "human_authorization must be an immutable verifier object"
                )
            if human_authorization.get("algorithm") != "ED25519":
                raise ModelValidationError(
                    "human authorization verifier algorithm must be ED25519"
                )
            try:
                public_key = base64.b64decode(
                    str(human_authorization.get("public_key_base64", "")),
                    validate=True,
                )
            except (ValueError, binascii.Error) as exc:
                raise ModelValidationError(
                    "human authorization public key is not canonical base64"
                ) from exc
            if len(public_key) != 32:
                raise ModelValidationError(
                    "human authorization ED25519 public key must be 32 bytes"
                )
        self._validate_uniqueness_and_graph()

    def _validate_uniqueness_and_graph(self) -> None:
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ModelValidationError("campaign graph has duplicate node identifiers")
        commands = {command.command_id for command in self.required_validation_commands}
        if len(commands) != len(self.required_validation_commands):
            raise ModelValidationError("validation command identifiers must be unique")
        budget_tokens = {budget.token for budget in self.attempt_budgets}
        if len(budget_tokens) != len(self.attempt_budgets):
            raise ModelValidationError("attempt budget tokens must be unique")
        total_budget_capacity = sum(budget.limit for budget in self.attempt_budgets)
        if self.autonomous_rank > total_budget_capacity:
            raise ModelValidationError(
                "autonomous rank exceeds declared durable attempt-budget capacity"
            )
        if len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ModelValidationError("allowed paths must be unique")
        if len(set(self.required_review_cohort)) != len(self.required_review_cohort):
            raise ModelValidationError("review cohort identifiers must be unique")
        known = set(node_ids)
        global_paths = set(self.allowed_paths)
        worktree_identity = os.path.normcase(
            str(Path(self.worktree).expanduser().resolve(strict=False))
        )
        for command in self.required_validation_commands:
            command_root = os.path.normcase(
                str(
                    Path(command.working_directory)
                    .expanduser()
                    .resolve(strict=False)
                )
            )
            if command_root != worktree_identity:
                raise ModelValidationError(
                    f"validation command {command.command_id} working_directory "
                    "must be the exact campaign worktree"
                )
        for node in self.nodes:
            missing = set(node.dependencies) - known
            if missing:
                raise ModelValidationError(
                    f"node {node.node_id} has unknown dependencies: {sorted(missing)}"
                )
            if node.node_id in node.dependencies:
                raise ModelValidationError(f"node {node.node_id} depends on itself")
            unknown_commands = set(node.validation_command_ids) - commands
            if unknown_commands:
                raise ModelValidationError(
                    f"node {node.node_id} uses unknown validation commands: "
                    f"{sorted(unknown_commands)}"
                )
            if not node.validation_command_ids:
                raise ModelValidationError(
                    f"node {node.node_id} must admit at least one validation command"
                )
            if len(set(node.validation_command_ids)) != len(node.validation_command_ids):
                raise ModelValidationError(
                    f"node {node.node_id} has duplicate validation commands"
                )
            if not set(node.allowed_paths).issubset(global_paths):
                raise ModelValidationError(
                    f"node {node.node_id} allowed paths exceed campaign scope"
                )
        graph = {node.node_id: node.dependencies for node in self.nodes}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ModelValidationError("campaign graph must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in graph[node_id]:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in sorted(graph):
            visit(node_id)

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, verify_digest: bool = True
    ) -> "CampaignSpec":
        supplied_digest = str(value.get("specification_digest", ""))
        spec = cls(
            campaign_id=str(value["campaign_id"]),
            specification_revision=int(value["specification_revision"]),
            specification_digest="",
            authority_epoch=int(value["authority_epoch"]),
            cancellation_epoch=int(value["cancellation_epoch"]),
            mode=_enum(CampaignMode, value["mode"]),
            objective=str(value["objective"]),
            objective_kind=_enum(ObjectiveKind, value["objective_kind"]),
            repository_remote=str(value["repository_remote"]),
            git_root=str(value["git_root"]),
            worktree=str(value["worktree"]),
            branch=str(value["branch"]),
            base_sha=str(value["base_sha"]),
            allowed_paths=tuple(str(item) for item in value.get("allowed_paths", ())),
            nodes=tuple(NodeSpec.from_dict(item) for item in value["nodes"]),
            required_validation_commands=tuple(
                ValidationCommand.from_dict(item)
                for item in value.get("required_validation_commands", ())
            ),
            required_review_cohort=tuple(
                str(item) for item in value.get("required_review_cohort", ())
            ),
            publication_authority=value["publication_authority"],
            attempt_budgets=tuple(
                AttemptBudget.from_dict(item) for item in value.get("attempt_budgets", ())
            ),
            stop_conditions=tuple(str(item) for item in value.get("stop_conditions", ())),
            installed_source_commit=str(value["installed_source_commit"]),
            installed_bundle_digest=str(value["installed_bundle_digest"]),
            install_transaction=str(value["install_transaction"]),
            protocol_version=str(value.get("protocol_version", PROTOCOL_VERSION)),
            schema_compatibility=str(
                value.get("schema_compatibility", SCHEMA_COMPATIBILITY)
            ),
            host_capability_probe_version=str(
                value.get(
                    "host_capability_probe_version", HOST_CAPABILITY_PROBE_VERSION
                )
            ),
            autonomous_rank=int(value.get("autonomous_rank", 1)),
            deadline_utc=(str(value["deadline_utc"]) if value.get("deadline_utc") else None),
        )
        computed = spec.compute_digest()
        if supplied_digest and verify_digest and supplied_digest != computed:
            raise ModelValidationError(
                "specification digest does not match the immutable specification"
            )
        return replace(spec, specification_digest=supplied_digest or computed)

    def compute_digest(self) -> str:
        payload = self.to_dict()
        payload.pop("specification_digest", None)
        return canonical_json_digest(payload)

    def verify_digest(self) -> None:
        if not self.specification_digest or self.specification_digest != self.compute_digest():
            raise ModelValidationError(
                "specification digest does not match the immutable specification"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "specification_revision": self.specification_revision,
            "specification_digest": self.specification_digest,
            "authority_epoch": self.authority_epoch,
            "cancellation_epoch": self.cancellation_epoch,
            "mode": self.mode.value,
            "objective": self.objective,
            "objective_kind": self.objective_kind.value,
            "repository_remote": self.repository_remote,
            "git_root": self.git_root,
            "worktree": self.worktree,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "allowed_paths": list(self.allowed_paths),
            "nodes": [node.to_dict() for node in self.nodes],
            "required_validation_commands": [
                command.to_dict() for command in self.required_validation_commands
            ],
            "required_review_cohort": list(self.required_review_cohort),
            "publication_authority": _primitive(self.publication_authority),
            "attempt_budgets": [budget.to_dict() for budget in self.attempt_budgets],
            "stop_conditions": list(self.stop_conditions),
            "installed_source_commit": self.installed_source_commit,
            "installed_bundle_digest": self.installed_bundle_digest,
            "install_transaction": self.install_transaction,
            "protocol_version": self.protocol_version,
            "schema_compatibility": self.schema_compatibility,
            "host_capability_probe_version": self.host_capability_probe_version,
            "autonomous_rank": self.autonomous_rank,
            "deadline_utc": self.deadline_utc,
        }


@dataclass(frozen=True)
class Actor:
    actor_id: str
    campaign_id: str
    node_id: str | None
    role: ActorRole
    native_thread_id: str
    authority_epoch: int
    can_write: bool
    native_identity: Mapping[str, Any] = field(default_factory=dict)
    host_pid: int | None = None
    principal_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _enum(ActorRole, self.role))
        object.__setattr__(self, "native_identity", _freeze(self.native_identity))
        for name in ("actor_id", "campaign_id", "native_thread_id"):
            _nonempty(getattr(self, name), name)
        if self.authority_epoch < 0:
            raise ModelValidationError("actor authority epoch must not be negative")
        if self.host_pid is not None and self.host_pid <= 0:
            raise ModelValidationError("actor host PID must be positive")
        if self.role in {
            ActorRole.PARENT,
            ActorRole.REVIEWER,
            ActorRole.CLOSURE_REVIEWER,
            ActorRole.VALIDATOR,
            ActorRole.SUPERVISOR,
        }:
            if self.can_write:
                raise ModelValidationError(f"{self.role.value} actors must be write denied")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Actor":
        return cls(
            actor_id=str(value["actor_id"]),
            campaign_id=str(value["campaign_id"]),
            node_id=str(value["node_id"]) if value.get("node_id") is not None else None,
            role=_enum(ActorRole, value["role"]),
            native_thread_id=str(value["native_thread_id"]),
            authority_epoch=int(value["authority_epoch"]),
            can_write=bool(value["can_write"]),
            native_identity=value.get("native_identity", {}),
            host_pid=(int(value["host_pid"]) if value.get("host_pid") is not None else None),
            principal_id=(
                str(value["principal_id"]) if value.get("principal_id") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "campaign_id": self.campaign_id,
            "node_id": self.node_id,
            "role": self.role.value,
            "native_thread_id": self.native_thread_id,
            "authority_epoch": self.authority_epoch,
            "can_write": self.can_write,
            "native_identity": _primitive(self.native_identity),
            "host_pid": self.host_pid,
            "principal_id": self.principal_id,
        }


@dataclass(frozen=True)
class Lease:
    lease_id: str
    campaign_id: str
    node_id: str
    resource_key: str
    actor_id: str
    fencing_epoch: int
    cancellation_epoch: int
    state: LeaseState = LeaseState.ACTIVE
    expires_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _enum(LeaseState, self.state))
        for name in ("lease_id", "campaign_id", "node_id", "resource_key", "actor_id"):
            _nonempty(getattr(self, name), name)
        if self.fencing_epoch < 0 or self.cancellation_epoch < 0:
            raise ModelValidationError("lease epochs are invalid")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Lease":
        return cls(
            lease_id=str(value["lease_id"]),
            campaign_id=str(value["campaign_id"]),
            node_id=str(value["node_id"]),
            resource_key=str(value["resource_key"]),
            actor_id=str(value["actor_id"]),
            fencing_epoch=int(value["fencing_epoch"]),
            cancellation_epoch=int(value["cancellation_epoch"]),
            state=_enum(LeaseState, value.get("state", LeaseState.ACTIVE.value)),
            expires_at=(str(value["expires_at"]) if value.get("expires_at") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "campaign_id": self.campaign_id,
            "node_id": self.node_id,
            "resource_key": self.resource_key,
            "actor_id": self.actor_id,
            "fencing_epoch": self.fencing_epoch,
            "cancellation_epoch": self.cancellation_epoch,
            "state": self.state.value,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    campaign_id: str
    node_id: str | None
    kind: EvidenceKind
    digest: str
    payload: Mapping[str, Any]
    candidate_head: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(EvidenceKind, self.kind))
        object.__setattr__(self, "payload", _freeze(self.payload))
        for name in ("evidence_id", "campaign_id", "digest"):
            _nonempty(getattr(self, name), name)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Evidence":
        return cls(
            evidence_id=str(value["evidence_id"]),
            campaign_id=str(value["campaign_id"]),
            node_id=str(value["node_id"]) if value.get("node_id") is not None else None,
            kind=_enum(EvidenceKind, value["kind"]),
            digest=str(value["digest"]),
            payload=value.get("payload", {}),
            candidate_head=(
                str(value["candidate_head"]) if value.get("candidate_head") else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "campaign_id": self.campaign_id,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "digest": self.digest,
            "payload": _primitive(self.payload),
            "candidate_head": self.candidate_head,
        }


@dataclass(frozen=True)
class Finding:
    finding_id: str
    title: str
    blocking: bool
    origin: FindingOrigin = FindingOrigin.REVIEW
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _enum(FindingOrigin, self.origin))
        object.__setattr__(self, "details", _freeze(self.details))
        _nonempty(self.finding_id, "finding_id")
        _nonempty(self.title, "finding title")

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, default_origin: FindingOrigin = FindingOrigin.REVIEW
    ) -> "Finding":
        return cls(
            finding_id=str(value["finding_id"]),
            title=str(value["title"]),
            blocking=bool(value.get("blocking", True)),
            origin=_enum(FindingOrigin, value.get("origin", default_origin.value)),
            details=value.get("details", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "blocking": self.blocking,
            "origin": self.origin.value,
            "details": _primitive(self.details),
        }


@dataclass(frozen=True)
class ExternalEffectIntent:
    operation_id: str
    campaign_id: str
    node_id: str | None
    kind: EffectKind
    state: EffectState
    payload: Mapping[str, Any]
    authority_epoch: int
    cancellation_epoch: int
    fencing_epoch: int | None
    external: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(EffectKind, self.kind))
        object.__setattr__(self, "state", _enum(EffectState, self.state))
        object.__setattr__(self, "payload", _freeze(self.payload))
        _nonempty(self.operation_id, "operation_id")
        _nonempty(self.campaign_id, "campaign_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalEffectIntent":
        return cls(
            operation_id=str(value["operation_id"]),
            campaign_id=str(value["campaign_id"]),
            node_id=str(value["node_id"]) if value.get("node_id") is not None else None,
            kind=_enum(EffectKind, value["kind"]),
            state=_enum(EffectState, value.get("state", EffectState.PREPARED.value)),
            payload=value.get("payload", {}),
            authority_epoch=int(value["authority_epoch"]),
            cancellation_epoch=int(value["cancellation_epoch"]),
            fencing_epoch=(
                int(value["fencing_epoch"])
                if value.get("fencing_epoch") is not None
                else None
            ),
            external=bool(value.get("external", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "campaign_id": self.campaign_id,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "payload": _primitive(self.payload),
            "authority_epoch": self.authority_epoch,
            "cancellation_epoch": self.cancellation_epoch,
            "fencing_epoch": self.fencing_epoch,
            "external": self.external,
        }


@dataclass(frozen=True)
class BudgetBalance:
    token: BudgetToken
    limit: int
    consumed: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "token", _enum(BudgetToken, self.token))
        if self.limit < 0 or self.consumed < 0 or self.consumed > self.limit:
            raise ModelValidationError("invalid budget balance")

    @property
    def remaining(self) -> int:
        return self.limit - self.consumed

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BudgetBalance":
        return cls(
            token=_enum(BudgetToken, value["token"]),
            limit=int(value["limit"]),
            consumed=int(value.get("consumed", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token.value,
            "limit": self.limit,
            "consumed": self.consumed,
            "remaining": self.remaining,
        }


@dataclass(frozen=True)
class BudgetReceipt:
    request_id: str
    token: BudgetToken
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "token", _enum(BudgetToken, self.token))
        _nonempty(self.request_id, "budget request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BudgetReceipt":
        return cls(str(value["request_id"]), _enum(BudgetToken, value["token"]), int(value["revision"]))

    def to_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "token": self.token.value, "revision": self.revision}


@dataclass(frozen=True)
class NodeSnapshot:
    node_id: str
    state: NodeState = NodeState.PENDING
    fencing_epoch: int = 0
    lease_actor_id: str | None = None
    implementation_attempts: int = 0
    validation_corrections: int = 0
    review_generations: int = 0
    repair_attempts: int = 0
    closure_generations: int = 0
    start_head: str | None = None
    candidate_head: str | None = None
    candidate_tree: str | None = None
    candidate_diff_digest: str | None = None
    candidate_node_diff_digest: str | None = None
    findings: tuple[Finding, ...] = ()
    closure_findings: tuple[Finding, ...] = ()
    resolved_finding_ids: tuple[str, ...] = ()
    completed_publication_effects: tuple[str, ...] = ()
    publication_operation_ids: tuple[str, ...] = ()
    pending_publication_effect: str | None = None
    pending_publication_operation_id: str | None = None
    publication_authorization_receipt_id: str | None = None
    publication_authorization_receipt_digest: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _enum(NodeState, self.state))
        _nonempty(self.node_id, "node_id")
        for value in (
            self.fencing_epoch,
            self.implementation_attempts,
            self.validation_corrections,
            self.review_generations,
            self.repair_attempts,
            self.closure_generations,
        ):
            if value < 0:
                raise ModelValidationError("node counters must not be negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NodeSnapshot":
        return cls(
            node_id=str(value["node_id"]),
            state=_enum(NodeState, value.get("state", NodeState.PENDING.value)),
            fencing_epoch=int(value.get("fencing_epoch", 0)),
            lease_actor_id=(str(value["lease_actor_id"]) if value.get("lease_actor_id") else None),
            implementation_attempts=int(value.get("implementation_attempts", 0)),
            validation_corrections=int(value.get("validation_corrections", 0)),
            review_generations=int(value.get("review_generations", 0)),
            repair_attempts=int(value.get("repair_attempts", 0)),
            closure_generations=int(value.get("closure_generations", 0)),
            start_head=(str(value["start_head"]) if value.get("start_head") else None),
            candidate_head=(str(value["candidate_head"]) if value.get("candidate_head") else None),
            candidate_tree=(str(value["candidate_tree"]) if value.get("candidate_tree") else None),
            candidate_diff_digest=(str(value["candidate_diff_digest"]) if value.get("candidate_diff_digest") else None),
            candidate_node_diff_digest=(
                str(value["candidate_node_diff_digest"])
                if value.get("candidate_node_diff_digest")
                else None
            ),
            findings=tuple(Finding.from_dict(item) for item in value.get("findings", ())),
            closure_findings=tuple(
                Finding.from_dict(item, default_origin=FindingOrigin.CLOSURE)
                for item in value.get("closure_findings", ())
            ),
            resolved_finding_ids=tuple(str(item) for item in value.get("resolved_finding_ids", ())),
            completed_publication_effects=tuple(
                str(item) for item in value.get("completed_publication_effects", ())
            ),
            publication_operation_ids=tuple(
                str(item) for item in value.get("publication_operation_ids", ())
            ),
            pending_publication_effect=(
                str(value["pending_publication_effect"])
                if value.get("pending_publication_effect")
                else None
            ),
            pending_publication_operation_id=(
                str(value["pending_publication_operation_id"])
                if value.get("pending_publication_operation_id")
                else None
            ),
            publication_authorization_receipt_id=(
                str(value["publication_authorization_receipt_id"])
                if value.get("publication_authorization_receipt_id")
                else None
            ),
            publication_authorization_receipt_digest=(
                str(value["publication_authorization_receipt_digest"])
                if value.get("publication_authorization_receipt_digest")
                else None
            ),
            failure_reason=(str(value["failure_reason"]) if value.get("failure_reason") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self.state.value,
            "fencing_epoch": self.fencing_epoch,
            "lease_actor_id": self.lease_actor_id,
            "implementation_attempts": self.implementation_attempts,
            "validation_corrections": self.validation_corrections,
            "review_generations": self.review_generations,
            "repair_attempts": self.repair_attempts,
            "closure_generations": self.closure_generations,
            "start_head": self.start_head,
            "candidate_head": self.candidate_head,
            "candidate_tree": self.candidate_tree,
            "candidate_diff_digest": self.candidate_diff_digest,
            "candidate_node_diff_digest": self.candidate_node_diff_digest,
            "findings": [finding.to_dict() for finding in self.findings],
            "closure_findings": [finding.to_dict() for finding in self.closure_findings],
            "resolved_finding_ids": list(self.resolved_finding_ids),
            "completed_publication_effects": list(self.completed_publication_effects),
            "publication_operation_ids": list(self.publication_operation_ids),
            "pending_publication_effect": self.pending_publication_effect,
            "pending_publication_operation_id": self.pending_publication_operation_id,
            "publication_authorization_receipt_id": self.publication_authorization_receipt_id,
            "publication_authorization_receipt_digest": self.publication_authorization_receipt_digest,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class CampaignSnapshot:
    spec: CampaignSpec
    revision: int
    state: CampaignState
    authority_epoch: int
    cancellation_epoch: int
    cancellation_state: CancellationState
    nodes: tuple[NodeSnapshot, ...]
    budgets: tuple[BudgetBalance, ...]
    budget_receipts: tuple[BudgetReceipt, ...]
    autonomous_rank_remaining: int
    active_node_id: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _enum(CampaignState, self.state))
        object.__setattr__(
            self, "cancellation_state", _enum(CancellationState, self.cancellation_state)
        )
        if self.revision < 0 or self.authority_epoch < 0 or self.cancellation_epoch < 0:
            raise ModelValidationError("snapshot revisions and epochs must not be negative")
        if self.autonomous_rank_remaining < 0:
            raise ModelValidationError("autonomous rank must not be negative")
        if {node.node_id for node in self.nodes} != {node.node_id for node in self.spec.nodes}:
            raise ModelValidationError("snapshot node set differs from immutable graph")

    @classmethod
    def initial(cls, spec: CampaignSpec) -> "CampaignSnapshot":
        spec.verify_digest()
        return cls(
            spec=spec,
            revision=0,
            state=CampaignState.DRAFT,
            authority_epoch=spec.authority_epoch,
            cancellation_epoch=spec.cancellation_epoch,
            cancellation_state=CancellationState.ACTIVE,
            nodes=tuple(NodeSnapshot(node.node_id) for node in spec.nodes),
            budgets=tuple(
                BudgetBalance(item.token, item.limit)
                for item in sorted(spec.attempt_budgets, key=lambda item: item.token.value)
            ),
            budget_receipts=(),
            autonomous_rank_remaining=spec.autonomous_rank,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CampaignSnapshot":
        return cls(
            spec=CampaignSpec.from_dict(value["spec"]),
            revision=int(value["revision"]),
            state=_enum(CampaignState, value["state"]),
            authority_epoch=int(value["authority_epoch"]),
            cancellation_epoch=int(value["cancellation_epoch"]),
            cancellation_state=_enum(CancellationState, value["cancellation_state"]),
            nodes=tuple(NodeSnapshot.from_dict(item) for item in value["nodes"]),
            budgets=tuple(BudgetBalance.from_dict(item) for item in value.get("budgets", ())),
            budget_receipts=tuple(
                BudgetReceipt.from_dict(item) for item in value.get("budget_receipts", ())
            ),
            autonomous_rank_remaining=int(value["autonomous_rank_remaining"]),
            active_node_id=(str(value["active_node_id"]) if value.get("active_node_id") else None),
            failure_reason=(str(value["failure_reason"]) if value.get("failure_reason") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "revision": self.revision,
            "state": self.state.value,
            "authority_epoch": self.authority_epoch,
            "cancellation_epoch": self.cancellation_epoch,
            "cancellation_state": self.cancellation_state.value,
            "nodes": [node.to_dict() for node in self.nodes],
            "budgets": [budget.to_dict() for budget in self.budgets],
            "budget_receipts": [receipt.to_dict() for receipt in self.budget_receipts],
            "autonomous_rank_remaining": self.autonomous_rank_remaining,
            "active_node_id": self.active_node_id,
            "failure_reason": self.failure_reason,
        }

    def node(self, node_id: str) -> NodeSnapshot:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise TransitionError(f"unknown node: {node_id}")

    def node_spec(self, node_id: str) -> NodeSpec:
        for node in self.spec.nodes:
            if node.node_id == node_id:
                return node
        raise TransitionError(f"unknown node: {node_id}")


@dataclass(frozen=True)
class Event:
    event_id: str
    campaign_id: str
    event_type: EventType
    expected_revision: int
    authority_epoch: int
    cancellation_epoch: int
    node_id: str | None = None
    actor_id: str | None = None
    fencing_epoch: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _enum(EventType, self.event_type))
        object.__setattr__(self, "payload", _freeze(self.payload))
        _nonempty(self.event_id, "event_id")
        _nonempty(self.campaign_id, "campaign_id")
        if self.expected_revision < 0 or self.authority_epoch < 0 or self.cancellation_epoch < 0:
            raise ModelValidationError("event revisions and epochs must not be negative")
        if self.fencing_epoch is not None and self.fencing_epoch <= 0:
            raise ModelValidationError("event fencing epoch must be positive")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Event":
        return cls(
            event_id=str(value["event_id"]),
            campaign_id=str(value["campaign_id"]),
            event_type=_enum(EventType, value.get("event_type", value.get("type"))),
            expected_revision=int(value["expected_revision"]),
            authority_epoch=int(value["authority_epoch"]),
            cancellation_epoch=int(value["cancellation_epoch"]),
            node_id=str(value["node_id"]) if value.get("node_id") is not None else None,
            actor_id=str(value["actor_id"]) if value.get("actor_id") is not None else None,
            fencing_epoch=(
                int(value["fencing_epoch"])
                if value.get("fencing_epoch") is not None
                else None
            ),
            payload=value.get("payload", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "campaign_id": self.campaign_id,
            "event_type": self.event_type.value,
            "expected_revision": self.expected_revision,
            "authority_epoch": self.authority_epoch,
            "cancellation_epoch": self.cancellation_epoch,
            "node_id": self.node_id,
            "actor_id": self.actor_id,
            "fencing_epoch": self.fencing_epoch,
            "payload": _primitive(self.payload),
        }


def replace_node(snapshot: CampaignSnapshot, updated: NodeSnapshot) -> CampaignSnapshot:
    nodes = tuple(updated if node.node_id == updated.node_id else node for node in snapshot.nodes)
    return replace(snapshot, nodes=nodes)


def unique_findings(
    values: Iterable[Mapping[str, Any]], *, origin: FindingOrigin
) -> tuple[Finding, ...]:
    findings = tuple(Finding.from_dict(item, default_origin=origin) for item in values)
    identifiers = [item.finding_id for item in findings]
    if len(set(identifiers)) != len(identifiers):
        raise TransitionError("finding identifiers must be unique within a frozen set")
    return findings
