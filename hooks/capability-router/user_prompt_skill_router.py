from __future__ import annotations

import re
from dataclasses import dataclass

from _hook_io import emit_additional_context, fail_open, get_prompt, load_input, no_output
from capability_index import query_index


@dataclass(frozen=True)
class RouteHypothesis:
    primary_family: str | None
    supporting_families: frozenset[str]
    source_tool_requirements: frozenset[str]
    score: int
    rejection_reasons: tuple[str, ...] = ()
    materiality_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptContext:
    authority_constraints: frozenset[str]
    permission_mode: str
    source_requirements: frozenset[str]
    project_context: str | None
    task_action: str
    task_object: str
    output_type: str
    risk_flags: frozenset[str]
    validation_requirements: frozenset[str]
    primary_family_candidates: frozenset[str] | None
    supporting_family_candidates: frozenset[str]
    source_tool_requirements: frozenset[str]
    denied_families: frozenset[str]
    candidate_visibility: str
    routing_confidence: str
    ambiguity_flags: frozenset[str]
    route_hypotheses: tuple[RouteHypothesis, ...] = ()

    @property
    def allowed_families(self) -> frozenset[str] | None:
        if self.primary_family_candidates is None:
            return None
        return frozenset(set(self.primary_family_candidates) | set(self.supporting_family_candidates))

    @property
    def explicit_capability_selection(self) -> bool:
        return self.candidate_visibility != "active_only"

    @property
    def action(self) -> str:
        return self.task_action

    @property
    def object_type(self) -> str:
        return self.task_object

    @property
    def permission(self) -> str:
        return self.permission_mode

    @property
    def example_status(self) -> str:
        return "example_only" if "example_only" in self.authority_constraints else "task"


@dataclass(frozen=True)
class DomainEvidenceRule:
    exact_terms: frozenset[str]
    phrases: tuple[str, ...]
    contextual_terms: frozenset[str] = frozenset()
    required_context_terms: frozenset[str] = frozenset()


NEGATIVE_ACTION_PHRASES = (
    "do not edit",
    "do not fix",
    "do not implement",
    "do not merge",
    "do not patch",
    "do not push",
    "don't edit",
    "don't fix",
    "don't implement",
    "don't merge",
    "don't patch",
    "don't push",
    "no fixes",
    "no implementation",
    "no merge",
    "no merges",
    "no patches",
    "no push",
    "no pushes",
    "read-only",
    "review only",
    "simulate only",
    "simulation only",
    "simulations only",
)

NO_EDIT_PHRASES = (
    "do not edit",
    "do not fix",
    "do not implement",
    "do not patch",
    "don't edit",
    "don't fix",
    "don't implement",
    "don't patch",
    "no fixes",
    "no implementation",
    "no patches",
)

SOURCE_LIMIT_PHRASES = (
    "based only on",
    "ignore prior memory",
    "ignore memory",
    "use only this file",
    "only from this file",
)

EXAMPLE_ONLY_PHRASES = (
    "example only",
    "for example",
    "just an example",
    "only an example",
    "the phrase",
)

APPROVAL_REQUIRED_PHRASES = (
    "do not implement till",
    "do not implement until",
    "wait for authorization",
    "wait for approval",
    "needs my approval",
)

CAPABILITY_TERMS = {
    "capability",
    "capabilities",
    "catalogue",
    "catalog",
    "mcp",
    "plugin",
    "plugins",
    "router",
    "routing",
    "skill",
    "skills",
    "tool",
    "tools",
}
CAPABILITY_ACTION_TERMS = {
    "available",
    "choose",
    "compare",
    "install",
    "installable",
    "list",
    "route",
    "routing",
    "select",
    "should",
    "use",
    "which",
}
CAPABILITY_SELECTION_PHRASES = (
    "available to install",
    "capability failure",
    "capability router",
    "capability routing",
    "installable plugin",
    "installable plugins",
    "plugin selection",
    "router reasoning failure",
    "routing failure",
    "skill selection",
    "tool selection",
    "which plugin",
    "which skill",
)
SKILL_AUTHORING_ACTION_TERMS = {"benchmark", "create", "edit", "evaluate", "improve", "modify", "test", "verify"}

IMPLEMENT_ACTION_TERMS = {"build", "code", "debug", "fix", "implement", "patch", "run", "ship"}
CREATE_ACTION_TERMS = {"build", "create", "draft", "generate", "make", "prepare", "write"}
REVIEW_ACTION_TERMS = {"audit", "challenge", "critique", "review", "stress", "stress-test", "stress-testing", "validate"}
SIMULATION_TERMS = {"simulate", "simulation", "simulations"}
INSTALL_ACTION_TERMS = {"configure", "install", "setup"}

CODE_OBJECT_TERMS = {
    "api",
    "bug",
    "bugfix",
    "class",
    "code",
    "codebase",
    "component",
    "function",
    "frontend",
    "module",
    "repo",
    "repository",
    "route",
    "test",
    "tests",
}
CODE_POLICY_TERMS = {"access", "auth", "authorization", "policy"}
CODING_CONTROL_PLANE_TERMS = {
    "active-slice",
    "code_allowed",
    "coding plan",
    "coding plans",
    "gate",
    "gated",
    "manifest",
    "permission manifest",
    "project gates",
    "session continuity",
    "workflow manifest",
}
CONTROLLED_DOCUMENT_TERMS = {"daci", "governance", "policy", "playbook", "raci", "sop"}
CONTROLLED_DOCUMENT_PHRASES = ("operating model", "process map")
PDF_TERMS = {"acrobat", "pdf"}
PRESENTATION_TERMS = {"deck", "powerpoint", "ppt", "pptx", "presentation", "presentations", "slide", "slides"}
DOCUMENT_TERMS = {"doc", "docx", "document", "documents", "word"}
SPREADSHEET_TERMS = {"excel", "sheet", "sheets", "spreadsheet", "spreadsheets", "workbook", "xlsx"}
QUANT_TERMS = {
    "calculate",
    "calculation",
    "forecast",
    "formula",
    "kpi",
    "metric",
    "model",
    "numbers",
    "pricing",
    "rate",
    "roi",
    "sensitivity",
    "statistic",
    "statistics",
    "unit",
}
STRONG_QUANT_OWNERSHIP_TERMS = {
    "calculate",
    "calculation",
    "estimate",
    "forecast",
    "formula",
    "model",
    "roi",
    "sensitivity",
    "statistic",
    "statistics",
}
OPERATIONAL_FAILURE_TERMS = {"failure", "failures", "operations", "prevention", "process", "rca", "recurring", "root", "support"}
GITHUB_TERMS = {"branch", "ci", "github", "merge", "pr", "pull"}
BUSINESS_WORKFLOW_TERMS = {"customer", "onboarding", "process", "service", "services", "team", "workflow"}
CREATIVE_TERMS = {"campaign", "concept", "image", "logo", "moodboard", "picture", "prototype", "visual"}
BUSINESS_STRATEGY_TERMS = {"business", "entry", "growth", "gtm", "market", "pricing", "sales", "stakeholder", "strategy"}
EVIDENCE_TERMS = {"citation", "claims", "fact", "latest", "source", "sources", "verify"}
HUMANIZER_TERMS = {"assignment", "essay", "humanize", "natural", "robotic"}
SECURITY_TERMS = {"auth", "authorization", "bypass", "privacy", "secret", "security", "threat", "vulnerability"}
HIGH_RISK_TERMS = {"clinical", "compliance", "financial", "legal", "medical", "phi", "privacy", "security"}
NEW_PROJECT_TERMS = {"agents.md", "handoff", "prd", "project", "tdd"}
SOFTWARE_PROJECT_TERMS = {
    "api",
    "architecture",
    "backend",
    "code",
    "codebase",
    "coding",
    "frontend",
    "module",
    "package",
    "repo",
    "repository",
    "software",
    "test",
    "tests",
}
SOFTWARE_PROJECT_PHRASES = (
    "ai-assisted coding",
    "architecture change",
    "before implementation",
    "coding workflow",
    "existing repo",
    "existing repository",
    "material repo",
    "new app",
    "new application",
    "new repo",
    "new repository",
    "new software project",
    "tech stack",
    "unclear existing repo",
    "unclear repo",
)
BROWSER_VERIFICATION_TERMS = {"browser", "localhost", "playwright", "screenshot", "ui", "visual", "viewport"}
FRONTEND_DOMAIN_TERMS = {
    "component",
    "components",
    "css",
    "frontend",
    "jsx",
    "next.js",
    "react",
    "tailwind",
    "tsx",
    "ui",
}
FRONTEND_DOMAIN_PHRASES = (
    "app router",
    "apps/web",
    "next app",
    "next-env.d.ts",
    "next.config",
    "nextjs",
    "pages router",
    "react app",
    "web app",
    "web application",
    "web scaffold",
)
FRONTEND_DOMAIN_CONTEXTUAL_TERMS = {"next"}
FRONTEND_DOMAIN_REQUIRED_CONTEXT_TERMS = {
    "app",
    "component",
    "components",
    "frontend",
    "page",
    "pages",
    "react",
    "route",
    "routes",
    "scaffold",
    "web",
}
FRONTEND_DOMAIN_EVIDENCE = DomainEvidenceRule(
    exact_terms=frozenset(FRONTEND_DOMAIN_TERMS),
    phrases=FRONTEND_DOMAIN_PHRASES,
    contextual_terms=frozenset(FRONTEND_DOMAIN_CONTEXTUAL_TERMS),
    required_context_terms=frozenset(FRONTEND_DOMAIN_REQUIRED_CONTEXT_TERMS),
)
CURRENT_SOURCE_TERMS = {"current", "latest", "recent", "today", "updated"}

CODING_PRIMARY_FAMILIES = {
    "browser_verification",
    "cloud_platform",
    "code",
    "code_orchestration",
    "frontend",
    "github",
    "mobile",
    "openai_platform",
    "security",
    "skill_authoring",
}

NONCODING_REVIEW_SUPPORT_FAMILIES = {"critique", "evidence"}
CONTAINER_REVIEW_PRIMARY_FAMILIES = {
    "controlled_document",
    "document",
    "github",
    "pdf",
    "presentation",
    "spreadsheet",
}


ROUTES = [
    {
        "family": "humanizer",
        "skill": "humanizer",
        "triggers": (
            "humanize",
            "make this sound natural",
            "rewrite naturally",
            "assignment",
            "essay",
            "less ai",
            "less robotic",
        ),
        "guidance": "For rewriting, assignments, or natural voice work, use the humanizer skill and preserve the user's structure unless asked to restructure.",
    },
    {
        "family": "evidence",
        "skill": "evidence-checker",
        "triggers": (
            "fact check",
            "sources",
            "citation",
            "latest",
            "documentation",
            "is this true",
            "source check",
            "source verification",
            "verify claims",
            "verify sources",
        ),
        "guidance": "For factual claims, verification, current docs, or source-quality review, use evidence-checker or the narrow official-doc skill if one applies.",
    },
    {
        "family": "capability_selection",
        "skill": "catalogue-router",
        "triggers": (
            "capability router",
            "capability routing",
            "router",
            "routing failure",
            "capability failure",
            "which skill",
            "which plugin",
            "skill selection",
            "plugin selection",
            "tool selection",
            "available to install",
            "installable plugin",
            "installable plugins",
        ),
        "guidance": "For capability, router, skill, plugin, or tool-selection work, use catalogue-router and treat index results as candidates, not authority.",
    },
    {
        "family": "code_orchestration",
        "skill": "codex-coding-os-master",
        "triggers": (
            "codex-coding-os",
            "codex coding os",
            "coding session",
            "vibe coding",
            "code_allowed",
            "coding plans",
            "existing repo",
            "existing repository",
            "new software project",
        ),
        "guidance": "For full coding-workflow orchestration, project gates, or code_allowed decisions, use codex-coding-os-master before narrower support skills.",
    },
    {
        "family": "github",
        "skill": "github:github",
        "triggers": (
            "github",
            "pull request",
            "pr #",
            " pr ",
            " pr.",
            " pr,",
            " pr:",
            "ci status",
            "merge rule",
            "merge protection",
            "branch protection",
            "review protection",
            "admin-merge",
            "admin merge",
        ),
        "guidance": "For GitHub, PR, CI, branch-protection, or merge-rule work, use the GitHub capability and do not merge, push, or rewrite history without explicit approval.",
    },
    {
        "family": "frontend",
        "skill": "build-web-apps:frontend-app-builder / frontend-testing-debugging / react-best-practices",
        "triggers": (
            "app router",
            "apps/web",
            "frontend",
            "next app",
            "next-env.d.ts",
            "next.config",
            "next.js",
            "nextjs",
            "pages router",
            "react",
            "web app",
            "web scaffold",
        ),
        "guidance": "For React, Next.js, web scaffold, or frontend UI material, add frontend support after the primary container/action owner. Do not let frontend support take over GitHub PR, repo, or project authority.",
    },
    {
        "family": "code",
        "skill": "ai-coding-discipline",
        "triggers": (
            "existing repo",
            "branch",
            "ci",
            "bug",
            "bugfix",
            "fix",
            "implement",
            "merge",
            "pr check",
            "checks",
            "workflow",
            "run tests",
            "health check",
            "winerror",
            "commit",
            "push",
        ),
        "guidance": "For repo changes, run-health debugging, PR checks, bug fixes, or implementation, use ai-coding-discipline and keep edits bounded and verified.",
    },
    {
        "family": "quantitative",
        "skill": "quant-review",
        "triggers": (
            "calculate",
            "forecast",
            "pricing model",
            "unit economics",
            "statistic",
            "roi",
            "sensitivity",
            "market size",
        ),
        "guidance": "For numbers, forecasts, or models, use quant-review and show formula, inputs, units, assumptions, and sensitivity when material.",
    },
    {
        "family": "critique",
        "skill": "deep-critic",
        "triggers": (
            "critique",
            "audit this",
            "audit the",
            "challenge",
            "review this",
            "what is wrong",
            "stress test",
        ),
        "exclude_phrases": (
            "audit log",
            "audit trail",
        ),
        "guidance": "For critique or audit requests, use deep-critic and challenge evidence quality, assumptions, recency, and failure modes.",
    },
    {
        "family": "operational_rca",
        "skill": "quality-improvement-problem-solving",
        "triggers": (
            "recurring failure",
            "recurring failures",
            "root cause",
            "rca",
            "prevention controls",
            "support operations",
        ),
        "guidance": "For recurring operational failures, use quality-improvement-problem-solving and separate process failure from statistical anomaly.",
    },
    {
        "family": "controlled_document",
        "skill": "controlled-document candidates",
        "candidate_skills": (
            "ssot-drafter",
            "ssot-auditor",
            "artifact-validation-workflow",
            "artifact-system-designer",
        ),
        "triggers": (
            "sop",
            "policy",
            "playbook",
            "operating model",
            "governance",
            "raci",
            "daci",
            "process map",
        ),
        "guidance": "For controlled artifacts, select candidate skills by action: drafter for creation, auditor for review, validation workflow for readiness, and artifact-system-designer for repository or lifecycle design.",
    },
    {
        "family": "contract",
        "skill": "contract-review",
        "triggers": (
            "contract",
            "agreement",
            "clause",
            "msa",
            "sla",
            "legal draft",
            "termination",
            "handover",
        ),
        "guidance": "For contracts, use contract-review and compare package promises, ownership, handover, termination, and raw/source-file rights.",
    },
    {
        "family": "security",
        "skill": "security-best-practices or security-threat-model",
        "triggers": (
            "security",
            "threat model",
            "vulnerability",
            "secret",
            "api key",
            "bypass",
            "auth",
            "authorization",
            "privacy",
        ),
        "guidance": "For security or privacy work, use the narrow security skill. For code diffs, include security-diff-scan when available.",
    },
    {
        "family": "new_project_doc",
        "skill": "new-project-documentation-system or technical-docs-pack",
        "triggers": (
            "new project",
            "project documentation",
            "prd",
            "tdd",
            "technical documentation",
            "repo docs",
            "agents.md",
            "handoff note",
        ),
        "guidance": "For new-project documentation systems, route orchestration to new-project-documentation-system and detailed repo templates to technical-docs-pack.",
    },
    {
        "family": "presentation",
        "skill": "Presentations or document-skills:pptx",
        "triggers": (
            "deck",
            "powerpoint",
            "presentation",
            "ppt",
            "pptx",
            "slide deck",
            "slides",
        ),
        "guidance": "For presentation or PPTX work, use the presentation capability and avoid document or product-planning skills unless source extraction or PRD work is explicitly requested.",
    },
    {
        "family": "browser_verification",
        "skill": "playwright",
        "triggers": (
            "browser test",
            "screenshot",
            "localhost",
            "visual check",
            "ui verification",
            "playwright",
        ),
        "guidance": "For browser or UI verification, use playwright/browser tooling and report exact rendering or browser errors.",
    },
]


def tokenize(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ").replace("/", " ")
    return {
        token.strip(".")
        for token in re.findall(r"[a-z0-9][a-z0-9+.-]*", normalized)
        if len(token.strip(".")) > 1 and not token.strip(".").isdigit()
    }


def has_term(prompt_lower: str, prompt_tokens: set[str], terms: set[str]) -> bool:
    for term in terms:
        if " " in term or "-" in term:
            if term in prompt_lower:
                return True
            continue
        if term in prompt_tokens:
            return True
    return False


def has_phrase(prompt_lower: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in prompt_lower for phrase in phrases)


def has_domain_evidence(prompt_lower: str, prompt_tokens: set[str], rule: DomainEvidenceRule) -> bool:
    if has_term(prompt_lower, prompt_tokens, set(rule.exact_terms)):
        return True
    if has_phrase(prompt_lower, rule.phrases):
        return True
    return bool(prompt_tokens & rule.contextual_terms and prompt_tokens & rule.required_context_terms)


def has_capability_selection_evidence(prompt_lower: str, prompt_tokens: set[str]) -> bool:
    if has_phrase(prompt_lower, CAPABILITY_SELECTION_PHRASES):
        return True
    capability_terms = set(prompt_tokens & CAPABILITY_TERMS)
    if "router" in capability_terms and has_phrase(prompt_lower, ("app router", "pages router")):
        capability_terms.discard("router")
    if "router" in capability_terms and has_term(prompt_lower, prompt_tokens, OPERATIONAL_FAILURE_TERMS):
        return True
    return len(capability_terms) >= 2


def is_software_project_context(prompt_lower: str, prompt_tokens: set[str]) -> bool:
    if has_phrase(prompt_lower, SOFTWARE_PROJECT_PHRASES):
        return True
    return bool(
        ("new project" in prompt_lower or "project" in prompt_tokens)
        and has_term(prompt_lower, prompt_tokens, SOFTWARE_PROJECT_TERMS)
    )


def has_frontend_domain_evidence(prompt_lower: str, prompt_tokens: set[str]) -> bool:
    return has_domain_evidence(prompt_lower, prompt_tokens, FRONTEND_DOMAIN_EVIDENCE)


def project_context(prompt_lower: str) -> str | None:
    project_terms = (
        "codebase",
        "codex",
        "codex-coding-os",
        "coding os",
        "project",
        "repo",
        "repository",
    )
    if any(term in prompt_lower for term in project_terms):
        return "project"
    return None


def detect_authority_constraints(prompt_lower: str, prompt_tokens: set[str]) -> frozenset[str]:
    constraints = {"latest_user_request_controls"}
    if has_phrase(prompt_lower, NEGATIVE_ACTION_PHRASES):
        constraints.add("read_only")
    if has_phrase(prompt_lower, NO_EDIT_PHRASES):
        constraints.add("no_implementation")
    if has_term(prompt_lower, prompt_tokens, SIMULATION_TERMS):
        constraints.add("simulation_only")
    if has_phrase(prompt_lower, SOURCE_LIMIT_PHRASES):
        constraints.add("source_limited")
    if has_phrase(prompt_lower, EXAMPLE_ONLY_PHRASES):
        constraints.add("example_only")
    if has_phrase(prompt_lower, APPROVAL_REQUIRED_PHRASES):
        constraints.add("approval_required")
    return frozenset(constraints)


def detect_permission_mode(authority_constraints: frozenset[str], prompt_lower: str, prompt_tokens: set[str]) -> str:
    if "approval_required" in authority_constraints:
        return "approval_required"
    if authority_constraints & {"read_only", "no_implementation", "simulation_only"}:
        return "read_only"
    if has_term(prompt_lower, prompt_tokens, INSTALL_ACTION_TERMS) and "approval" in prompt_tokens:
        return "approval_required"
    return "may_edit"


def detect_candidate_visibility(prompt_lower: str, prompt_tokens: set[str]) -> str:
    explicit_capability_selection = bool(
        has_term(prompt_lower, prompt_tokens, CAPABILITY_TERMS)
        and (
            has_term(prompt_lower, prompt_tokens, CAPABILITY_ACTION_TERMS)
            or "which skill" in prompt_lower
            or "which plugin" in prompt_lower
            or "available to install" in prompt_lower
        )
    )
    if not explicit_capability_selection:
        return "active_only"
    if "install" in prompt_tokens or "installable" in prompt_tokens or "candidate" in prompt_tokens:
        return "include_install_candidates"
    if "reference" in prompt_tokens or "reference-only" in prompt_tokens or "reference only" in prompt_lower:
        return "include_reference"
    return "active_plus_catalogue_routes"


def detect_source_requirements(
    prompt_lower: str,
    prompt_tokens: set[str],
    context: str | None,
    authority_constraints: frozenset[str],
) -> frozenset[str]:
    requirements = set()
    if "source_limited" in authority_constraints:
        requirements.add("user_provided_only")
    if context == "project":
        requirements.add("project_docs")
    if has_term(prompt_lower, prompt_tokens, CURRENT_SOURCE_TERMS):
        requirements.add("current_web")
    if "attached" in prompt_tokens or "file" in prompt_tokens or "folder" in prompt_tokens:
        requirements.add("local_files")
    if not requirements:
        requirements.add("none")
    return frozenset(requirements)


def detect_task_action(prompt_lower: str, prompt_tokens: set[str], permission_mode: str) -> str:
    if has_term(prompt_lower, prompt_tokens, SIMULATION_TERMS):
        return "simulate"
    if has_term(prompt_lower, prompt_tokens, REVIEW_ACTION_TERMS):
        return "review"
    if has_term(prompt_lower, prompt_tokens, INSTALL_ACTION_TERMS):
        return "install"
    if permission_mode == "read_only":
        if has_term(prompt_lower, prompt_tokens, IMPLEMENT_ACTION_TERMS):
            return "review"
        return "answer"
    if has_term(prompt_lower, prompt_tokens, IMPLEMENT_ACTION_TERMS) and has_term(prompt_lower, prompt_tokens, CODE_OBJECT_TERMS):
        return "implement"
    if has_term(prompt_lower, prompt_tokens, CREATE_ACTION_TERMS):
        return "create"
    return "answer"


def detect_task_object(prompt_lower: str, prompt_tokens: set[str]) -> str:
    controlled_document = has_term(prompt_lower, prompt_tokens, CONTROLLED_DOCUMENT_TERMS) or has_phrase(
        prompt_lower, CONTROLLED_DOCUMENT_PHRASES
    )
    if has_term(prompt_lower, prompt_tokens, IMPLEMENT_ACTION_TERMS) and (
        has_term(prompt_lower, prompt_tokens, CODE_OBJECT_TERMS)
        or has_term(prompt_lower, prompt_tokens, BROWSER_VERIFICATION_TERMS)
    ):
        return "codebase"
    if has_term(prompt_lower, prompt_tokens, PDF_TERMS):
        return "pdf"
    if has_term(prompt_lower, prompt_tokens, PRESENTATION_TERMS):
        return "presentation"
    if has_term(prompt_lower, prompt_tokens, SPREADSHEET_TERMS):
        return "spreadsheet"
    if has_term(prompt_lower, prompt_tokens, GITHUB_TERMS) or "pull request" in prompt_lower or "pr #" in prompt_lower:
        return "github"
    if controlled_document:
        return "controlled_document"
    if has_term(prompt_lower, prompt_tokens, DOCUMENT_TERMS):
        return "document"
    if "new project" in prompt_lower or has_term(prompt_lower, prompt_tokens, NEW_PROJECT_TERMS):
        return "project_setup"
    if has_term(prompt_lower, prompt_tokens, CODE_OBJECT_TERMS):
        return "codebase"
    if has_term(prompt_lower, prompt_tokens, CREATIVE_TERMS):
        return "creative_asset"
    if has_term(prompt_lower, prompt_tokens, BUSINESS_STRATEGY_TERMS):
        return "strategy"
    if has_term(prompt_lower, prompt_tokens, QUANT_TERMS):
        return "quantitative_model"
    if has_term(prompt_lower, prompt_tokens, EVIDENCE_TERMS):
        return "source_claim"
    if has_term(prompt_lower, prompt_tokens, BUSINESS_WORKFLOW_TERMS):
        return "workflow"
    return "general"


def detect_output_type(prompt_lower: str, prompt_tokens: set[str], task_object: str) -> str:
    if "csv" in prompt_tokens:
        return "csv"
    if task_object == "pdf":
        return "pdf"
    if task_object == "presentation":
        return "pptx"
    if task_object == "spreadsheet":
        return "xlsx"
    if task_object in {"document", "controlled_document"}:
        return "document"
    if task_object == "codebase":
        return "patch"
    if has_term(prompt_lower, prompt_tokens, SIMULATION_TERMS):
        return "simulation_matrix"
    if "plan" in prompt_tokens:
        return "plan"
    return "prose_answer"


def detect_risk_flags(prompt_lower: str, prompt_tokens: set[str], task_object: str) -> frozenset[str]:
    risks = set()
    if has_term(prompt_lower, prompt_tokens, HIGH_RISK_TERMS):
        risks.add("high_stakes")
    if has_term(prompt_lower, prompt_tokens, SECURITY_TERMS):
        risks.add("security_or_privacy")
    if task_object in {"controlled_document", "codebase"}:
        risks.add("control_surface")
    if "failure" in prompt_tokens or "failures" in prompt_tokens:
        risks.add("failure_analysis")
    return frozenset(risks)


def detect_validation_requirements(prompt_lower: str, prompt_tokens: set[str], output_type: str) -> frozenset[str]:
    requirements = set()
    if "test" in prompt_tokens or "tests" in prompt_tokens:
        requirements.add("tests")
    if has_term(prompt_lower, prompt_tokens, BROWSER_VERIFICATION_TERMS):
        requirements.add("browser_verification")
    if has_term(prompt_lower, prompt_tokens, EVIDENCE_TERMS):
        requirements.add("source_verification")
    if has_term(prompt_lower, prompt_tokens, QUANT_TERMS):
        requirements.add("calculation_check")
    if output_type == "simulation_matrix":
        requirements.add("simulation")
    if not requirements:
        requirements.add("task_acceptance")
    return frozenset(requirements)


def derive_source_tool_requirements(source_requirements: frozenset[str], context: str | None) -> frozenset[str]:
    return frozenset()


def derive_denied_families(
    authority_constraints: frozenset[str],
    permission_mode: str,
    task_object: str,
) -> frozenset[str]:
    denied = set()
    if permission_mode in {"read_only", "approval_required"}:
        denied.update({"code", "code_orchestration"})
    if permission_mode == "read_only":
        denied.add("code")
    if "source_limited" in authority_constraints:
        denied.add("project_continuity")
    if "example_only" in authority_constraints:
        denied.update({"document", "pdf", "presentation", "spreadsheet"})
    if task_object == "codebase":
        denied.add("controlled_document")
    if task_object == "quantitative_model":
        denied.add("operational_rca")
    return frozenset(denied)


def primary_options_from_frame(
    prompt_lower: str,
    prompt_tokens: set[str],
    task_action: str,
    task_object: str,
    permission_mode: str,
    authority_constraints: frozenset[str],
    candidate_visibility: str,
) -> list[str | None]:
    if "example_only" in authority_constraints and candidate_visibility == "active_only":
        return []
    if candidate_visibility != "active_only":
        return ["capability_selection"]
    if has_capability_selection_evidence(prompt_lower, prompt_tokens) and (
        task_action in {"review", "simulate"}
        or has_term(prompt_lower, prompt_tokens, OPERATIONAL_FAILURE_TERMS)
    ):
        return ["capability_selection"]
    if has_term(prompt_lower, prompt_tokens, CODING_CONTROL_PLANE_TERMS):
        return ["code_orchestration"]
    if task_object == "pdf":
        return ["pdf"]
    if task_object == "presentation":
        return ["presentation"]
    if task_object == "spreadsheet":
        return ["spreadsheet"]
    if task_object == "document":
        return ["document"]
    if task_object == "github":
        return ["github"]
    if task_object == "project_setup":
        if is_software_project_context(prompt_lower, prompt_tokens):
            return ["code_orchestration"]
        return ["new_project_doc"]
    if "skill" in prompt_tokens and has_term(prompt_lower, prompt_tokens, SKILL_AUTHORING_ACTION_TERMS):
        return ["skill_authoring"]
    if task_object == "codebase":
        if permission_mode == "read_only":
            if has_term(prompt_lower, prompt_tokens, SECURITY_TERMS):
                return ["security"]
            if has_term(prompt_lower, prompt_tokens, REVIEW_ACTION_TERMS):
                return ["critique"]
            return []
        if (
            "existing repo" in prompt_lower
            or "existing repository" in prompt_lower
            or has_term(prompt_lower, prompt_tokens, {"codebase", "repo", "repository"})
        ):
            return ["code_orchestration"]
        return ["code"]
    if task_object == "controlled_document":
        return ["controlled_document"]
    if task_object == "strategy" and not has_term(prompt_lower, prompt_tokens, STRONG_QUANT_OWNERSHIP_TERMS):
        return ["business_strategy"]
    if has_term(prompt_lower, prompt_tokens, QUANT_TERMS):
        return ["quantitative"]
    operational_failure = has_term(prompt_lower, prompt_tokens, OPERATIONAL_FAILURE_TERMS) and (
        "recurring" in prompt_tokens or "root cause" in prompt_lower or "operations" in prompt_tokens or "support" in prompt_tokens
    )
    if operational_failure:
        return ["operational_rca"]
    if task_object == "workflow":
        return ["process"]
    if has_term(prompt_lower, prompt_tokens, HUMANIZER_TERMS) or "less ai" in prompt_lower:
        return ["humanizer"]
    if has_term(prompt_lower, prompt_tokens, CREATIVE_TERMS):
        return ["creative"]
    if task_object == "strategy":
        return ["business_strategy"]
    if has_term(prompt_lower, prompt_tokens, EVIDENCE_TERMS) or "fact check" in prompt_lower:
        return ["evidence"]
    if has_term(prompt_lower, prompt_tokens, SECURITY_TERMS):
        return ["security"]
    if task_action == "review" and has_term(prompt_lower, prompt_tokens, REVIEW_ACTION_TERMS):
        return ["critique"]
    return []


def material_support_families(
    prompt_lower: str,
    prompt_tokens: set[str],
    primary: str | None,
    task_object: str,
    risk_flags: frozenset[str],
    validation_requirements: frozenset[str],
) -> set[str]:
    support = set()
    if primary and has_term(prompt_lower, prompt_tokens, REVIEW_ACTION_TERMS) and primary not in {"critique", "security"}:
        support.add("critique")
    if primary and has_term(prompt_lower, prompt_tokens, EVIDENCE_TERMS) and primary != "evidence":
        support.add("evidence")
    if primary and has_term(prompt_lower, prompt_tokens, QUANT_TERMS) and primary not in {"quantitative"}:
        support.add("quantitative")
    if primary == "code" and (
        has_term(prompt_lower, prompt_tokens, SECURITY_TERMS)
        or has_term(prompt_lower, prompt_tokens, CODE_POLICY_TERMS)
        or "security_or_privacy" in risk_flags
    ):
        support.add("security")
    if primary == "code_orchestration" and task_object in {"codebase", "project_setup"}:
        support.add("code")
        if task_object == "project_setup":
            support.update({"new_project_doc", "project_continuity"})
        if (
            has_term(prompt_lower, prompt_tokens, SECURITY_TERMS)
            or has_term(prompt_lower, prompt_tokens, CODE_POLICY_TERMS)
            or "security_or_privacy" in risk_flags
        ):
            support.add("security")
    if primary == "github" and (
        has_term(prompt_lower, prompt_tokens, {"branch", "ci", "commit", "merge", "push", "status", "test", "tests"})
        or "check" in prompt_tokens
        or "checks" in prompt_tokens
        or "workflow" in prompt_tokens
    ):
        support.add("code")
    if primary in {"code", "frontend"} and "browser_verification" in validation_requirements:
        support.add("browser_verification")
    if task_object == "strategy" and "high_stakes" in risk_flags and primary != "critique":
        support.add("critique")
    if primary:
        support.discard(primary)
    return support


def artifact_domain_support_families(
    prompt_lower: str,
    prompt_tokens: set[str],
    primary: str | None,
    task_action: str,
    task_object: str,
    risk_flags: frozenset[str],
    validation_requirements: frozenset[str],
) -> set[str]:
    if not primary or primary not in CONTAINER_REVIEW_PRIMARY_FAMILIES:
        return set()
    if task_action not in {"review", "simulate"}:
        return set()

    support = set()
    if has_frontend_domain_evidence(prompt_lower, prompt_tokens):
        support.add("frontend")
        if "browser_verification" in validation_requirements:
            support.add("browser_verification")
    if has_term(prompt_lower, prompt_tokens, SECURITY_TERMS) or "security_or_privacy" in risk_flags:
        support.add("security")
    if has_term(prompt_lower, prompt_tokens, QUANT_TERMS) or "calculation_check" in validation_requirements:
        support.add("quantitative")
    if has_term(prompt_lower, prompt_tokens, CONTROLLED_DOCUMENT_TERMS) or has_phrase(
        prompt_lower, CONTROLLED_DOCUMENT_PHRASES
    ):
        support.add("controlled_document")
    if has_term(prompt_lower, prompt_tokens, EVIDENCE_TERMS) or "source_verification" in validation_requirements:
        support.add("evidence")
    support.discard(primary)
    return support


def master_review_support_families(
    prompt_lower: str,
    prompt_tokens: set[str],
    primary: str | None,
    task_object: str,
    task_action: str,
    risk_flags: frozenset[str],
    validation_requirements: frozenset[str],
) -> set[str]:
    if not primary or primary in CODING_PRIMARY_FAMILIES:
        return set()
    if primary == "capability_selection" or task_action == "install":
        return set()
    if task_object == "general" and task_action == "answer" and not risk_flags:
        return set()

    support = set(NONCODING_REVIEW_SUPPORT_FAMILIES)
    if task_object == "controlled_document":
        support.add("controlled_document")
    if task_object in {"document", "controlled_document", "workflow"}:
        support.add("process")
    if task_object in {"strategy", "workflow"}:
        support.add("business_strategy")
    if task_object == "creative_asset":
        support.add("creative")
    if has_term(prompt_lower, prompt_tokens, OPERATIONAL_FAILURE_TERMS) or "failure_analysis" in risk_flags:
        support.add("operational_rca")
    if has_term(prompt_lower, prompt_tokens, QUANT_TERMS) or "calculation_check" in validation_requirements:
        support.add("quantitative")
    if "financial" in prompt_tokens or "finance" in prompt_tokens:
        support.add("quantitative")
    if has_term(prompt_lower, prompt_tokens, HUMANIZER_TERMS):
        support.add("humanizer")
    if "source_verification" in validation_requirements:
        support.add("evidence")
    support.discard(primary)
    return support


def detect_ambiguity_flags(
    primary_options: list[str | None],
    support: set[str],
    risk_flags: frozenset[str],
    authority_constraints: frozenset[str],
    source_requirements: frozenset[str],
) -> frozenset[str]:
    flags = set()
    if len([item for item in primary_options if item]) > 1:
        flags.add("multiple_primary_candidates")
    if len(support) > 2:
        flags.add("multi_support")
    if risk_flags:
        flags.add("risk_sensitive")
    if authority_constraints & {"read_only", "no_implementation", "simulation_only", "source_limited"}:
        flags.add("negative_constraints")
    if source_requirements & {"current_web"}:
        flags.add("source_gated")
    return frozenset(flags)


def score_route_hypotheses(
    primary_options: list[str | None],
    support: set[str],
    source_tools: frozenset[str],
    denied_families: frozenset[str],
    ambiguity_flags: frozenset[str],
) -> tuple[RouteHypothesis, ...]:
    if not primary_options:
        primary_options = [None]
    hypotheses = []
    for primary in primary_options:
        rejection_reasons = []
        candidate_support = set(support)
        if primary in denied_families:
            rejection_reasons.append("primary_denied")
        candidate_support = candidate_support - set(denied_families)
        if primary:
            candidate_support.discard(primary)
        score = 0
        if primary:
            score += 30
        score += min(len(candidate_support), 5) * 8
        score += len(source_tools) * 6
        if "source_gated" in ambiguity_flags and source_tools:
            score += 12
        if "negative_constraints" in ambiguity_flags and not rejection_reasons:
            score += 6
        score -= len(rejection_reasons) * 100
        if len(candidate_support) > 5:
            rejection_reasons.append("too_many_supporting_families")
            score -= 20
        hypotheses.append(
            RouteHypothesis(
                primary_family=primary,
                supporting_families=frozenset(sorted(candidate_support)),
                source_tool_requirements=source_tools,
                score=score,
                rejection_reasons=tuple(rejection_reasons),
                materiality_reasons=tuple(sorted(candidate_support)),
            )
        )
    return tuple(sorted(hypotheses, key=lambda item: item.score, reverse=True))


def choose_route_family_sets(hypotheses: tuple[RouteHypothesis, ...]) -> tuple[frozenset[str] | None, frozenset[str], str]:
    if not hypotheses:
        return frozenset(), frozenset(), "low"
    accepted = [hypothesis for hypothesis in hypotheses if not hypothesis.rejection_reasons]
    best = accepted[0] if accepted else hypotheses[0]
    primary = frozenset({best.primary_family}) if best.primary_family else frozenset()
    confidence = "high" if accepted and best.score >= 30 else "medium" if accepted else "low"
    return primary, best.supporting_families, confidence


def classify_prompt(prompt: str) -> PromptContext:
    prompt_lower = prompt.lower()
    prompt_tokens = tokenize(prompt)
    authority_constraints = detect_authority_constraints(prompt_lower, prompt_tokens)
    permission_mode = detect_permission_mode(authority_constraints, prompt_lower, prompt_tokens)
    candidate_visibility = detect_candidate_visibility(prompt_lower, prompt_tokens)
    context = project_context(prompt_lower)
    source_requirements = detect_source_requirements(prompt_lower, prompt_tokens, context, authority_constraints)
    task_action = detect_task_action(prompt_lower, prompt_tokens, permission_mode)
    task_object = detect_task_object(prompt_lower, prompt_tokens)
    output_type = detect_output_type(prompt_lower, prompt_tokens, task_object)
    risk_flags = detect_risk_flags(prompt_lower, prompt_tokens, task_object)
    validation_requirements = detect_validation_requirements(prompt_lower, prompt_tokens, output_type)
    source_tools = derive_source_tool_requirements(source_requirements, context)
    denied_families = derive_denied_families(authority_constraints, permission_mode, task_object)
    primary_options = primary_options_from_frame(
        prompt_lower,
        prompt_tokens,
        task_action,
        task_object,
        permission_mode,
        authority_constraints,
        candidate_visibility,
    )
    first_primary = primary_options[0] if primary_options else None
    support = material_support_families(
        prompt_lower,
        prompt_tokens,
        first_primary,
        task_object,
        risk_flags,
        validation_requirements,
    )
    support.update(
        artifact_domain_support_families(
            prompt_lower,
            prompt_tokens,
            first_primary,
            task_action,
            task_object,
            risk_flags,
            validation_requirements,
        )
    )
    support.update(
        master_review_support_families(
            prompt_lower,
            prompt_tokens,
            first_primary,
            task_object,
            task_action,
            risk_flags,
            validation_requirements,
        )
    )
    ambiguity_flags = detect_ambiguity_flags(primary_options, support, risk_flags, authority_constraints, source_requirements)
    hypotheses = score_route_hypotheses(primary_options, support, source_tools, denied_families, ambiguity_flags)
    primary_families, supporting_families, confidence = choose_route_family_sets(hypotheses)

    if "source_limited" in authority_constraints and not primary_families and candidate_visibility == "active_only":
        primary_families = frozenset()
        supporting_families = frozenset()

    return PromptContext(
        authority_constraints=authority_constraints,
        permission_mode=permission_mode,
        source_requirements=source_requirements,
        project_context=context,
        task_action=task_action,
        task_object=task_object,
        output_type=output_type,
        risk_flags=risk_flags,
        validation_requirements=validation_requirements,
        primary_family_candidates=primary_families,
        supporting_family_candidates=supporting_families,
        source_tool_requirements=source_tools,
        denied_families=denied_families,
        candidate_visibility=candidate_visibility,
        routing_confidence=confidence,
        ambiguity_flags=ambiguity_flags,
        route_hypotheses=hypotheses,
    )


def route_matches(route, lowered, context=None):
    context = context or classify_prompt(lowered)
    family = route.get("family")
    selected = set(context.primary_family_candidates or set()) | set(context.supporting_family_candidates)
    if any(phrase in lowered for phrase in route.get("exclude_phrases", ())):
        return False
    if family in context.denied_families:
        return False
    if context.primary_family_candidates is not None and family not in selected:
        return False
    return any(trigger in lowered for trigger in route["triggers"])


def route_role(route, context: PromptContext) -> str:
    family = route.get("family")
    if family in set(context.primary_family_candidates or set()):
        return "Primary candidate"
    if family in context.supporting_family_candidates:
        return "Supporting candidate"
    return "Candidate"


def route_priority(route, context: PromptContext | None = None):
    base_priorities = {
        "humanizer": 10,
        "evidence-checker": 20,
        "deep-critic": 30,
        "catalogue-router": 40,
        "github:github": 50,
        "security-best-practices or security-threat-model": 55,
        "ai-coding-discipline": 60,
        "codex-coding-os-master": 80,
    }
    priority = base_priorities.get(route["skill"], 100)
    if context and route.get("family") in set(context.primary_family_candidates or set()):
        priority -= 20
    if context and route.get("family") in context.supporting_family_candidates:
        priority -= 5
    return priority


def route_candidate_text(route) -> str:
    candidates = route.get("candidate_skills")
    if candidates:
        return f"{route['skill']} ({', '.join(candidates)})"
    return route["skill"]


def matched_routes_for_prompt(prompt, context=None):
    lowered = prompt.lower()
    context = context or classify_prompt(prompt)
    return sorted(
        [route for route in ROUTES if route_matches(route, lowered, context=context)],
        key=lambda route: route_priority(route, context),
    )


def candidate_visibility_includes_candidates(candidate_visibility: str) -> bool:
    return candidate_visibility in {"include_install_candidates", "include_project_local_pilots", "include_reference"}


SESSION_ONLY_ENTRY_STATUSES = {
    "catalogue-plugin-available",
    "conditional-future-skill",
    "install-or-run-candidate",
    "project-local-pilot",
    "reference-only",
}
SESSION_ONLY_ENTRY_ROLES = {"project-local-only", "reference-only"}
ACTIVE_AUTOMATIC_ENTRY_STATUSES = {
    "active-local",
    "active-mcp",
    "active-pack",
    "active-plugin",
    "active-user",
    "catalogue-plugin-active",
    "catalogue-route",
    "session-available-plugin",
}


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def requires_session_authorization(entry) -> bool:
    if entry.get("kind") != "candidate" and entry.get("status", "") in ACTIVE_AUTOMATIC_ENTRY_STATUSES:
        return False
    return (
        entry.get("kind") == "candidate"
        or entry.get("status", "") in SESSION_ONLY_ENTRY_STATUSES
        or entry.get("routing_role", "") in SESSION_ONLY_ENTRY_ROLES
    )


def entry_role(entry, context: PromptContext) -> str:
    name = entry.get("name", "")
    primary_families = set()
    support_families = set()
    raw_primary = entry.get("primary_families") or entry.get("family") or entry.get("families") or ""
    raw_support = entry.get("support_families") or ""
    if isinstance(raw_primary, str):
        primary_families = {item.strip().replace("-", "_") for item in re.split(r"[,| ]+", raw_primary) if item.strip()}
    if isinstance(raw_support, str):
        support_families = {item.strip().replace("-", "_") for item in re.split(r"[,| ]+", raw_support) if item.strip()}
    source_tools = {normalized(tool) for tool in context.source_tool_requirements}
    if entry.get("kind") == "mcp" and normalized(name) in source_tools:
        return "Source/data access"
    if requires_session_authorization(entry):
        return "Gated session-only support"
    if primary_families & set(context.primary_family_candidates or set()):
        return "Primary candidate"
    if support_families & set(context.supporting_family_candidates):
        return "Supporting candidate"
    if primary_families & set(context.supporting_family_candidates):
        return "Supporting candidate"
    return "Candidate"


def main():
    data = load_input()
    prompt = get_prompt(data)
    if not prompt:
        no_output()

    context = classify_prompt(prompt)
    matched = matched_routes_for_prompt(prompt, context=context)

    selected_families = set(context.primary_family_candidates or set()) | set(context.supporting_family_candidates)
    indexed_limit = 5 if selected_families or context.source_tool_requirements else 0
    indexed = (
        query_index(
            prompt,
            limit=indexed_limit,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            source_tool_requirements=context.source_tool_requirements,
            denied_families=context.denied_families,
            candidate_visibility=context.candidate_visibility,
            include_candidates=candidate_visibility_includes_candidates(context.candidate_visibility),
        )
        if indexed_limit
        else []
    )
    if not matched and not indexed and not context.source_tool_requirements:
        no_output()

    lines = [
        "Capability routing candidates detected. These are candidate capabilities, not authority. Follow system/developer instructions, the latest user request, global AGENTS.md, project AGENTS.md, source-of-truth rules, safety gates, and explicit no-edit/no-implementation limits first.",
        "Route through five layers: container, action, domain, risk/validation, and authority. The primary family owns the container/action. Supporting families cover material domain or risk evidence from the prompt or from later artifact inspection. Ignore any candidate that conflicts with the actual task, controlling instructions, source limits, project rules, or user-stated non-goals.",
    ]

    seen = set()
    for tool in sorted(context.source_tool_requirements):
        lines.append(f"- Source/data access `{tool}`: Required source gate when relevant. This is not a primary or supporting skill.")
        seen.add(normalized(tool))

    for route in matched:
        key = route_candidate_text(route)
        if key in seen:
            continue
        lines.append(f"- {route_role(route, context)} `{route_candidate_text(route)}`: {route['guidance']}")
        seen.add(key)
        if len(lines) >= 7:
            break

    gated_indexed = [entry for entry in indexed if requires_session_authorization(entry)]
    regular_indexed = [entry for entry in indexed if not requires_session_authorization(entry)]
    reserve_gated_line = context.explicit_capability_selection and bool(gated_indexed)
    regular_line_limit = 7 if reserve_gated_line else 8

    for entry in regular_indexed:
        name = entry.get("name", "")
        if not name or name in seen or normalized(name) in seen:
            continue
        role = entry_role(entry, context)
        description = entry.get("description", "").replace("\n", " ")[:220]
        status = entry.get("status", "")
        kind = entry.get("kind", "capability")
        if role == "Gated session-only support":
            lines.append(f"- Gated session-only support `{name}` [{status}]: {description} Requires explicit authorization before use in this session only. Never install universally or treat as primary.")
        else:
            lines.append(f"- {role} {kind} `{name}` [{status}]: {description}")
        seen.add(name)
        if len(lines) >= regular_line_limit:
            break

    if reserve_gated_line and len(lines) < 8:
        for entry in gated_indexed:
            name = entry.get("name", "")
            if not name or name in seen or normalized(name) in seen:
                continue
            description = entry.get("description", "").replace("\n", " ")[:220]
            status = entry.get("status", "")
            lines.append(f"- Gated session-only support `{name}` [{status}]: {description} Requires explicit authorization before use in this session only. Never install universally or treat as primary.")
            break

    emit_additional_context("UserPromptSubmit", "\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        fail_open("user_prompt_skill_router", exc)
