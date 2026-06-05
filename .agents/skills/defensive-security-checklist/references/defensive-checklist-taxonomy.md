# Defensive Checklist Taxonomy

Use this reference to build authorized defensive security checklists. Keep output grounded in the user's system, evidence, and authorization limits.

## 1. Authorization And Scope

Required checks:

- Confirm asset owner and authorization.
- Confirm environment: local, sandbox, staging, production, partner, third party, or unknown.
- Confirm allowed actions: checklist only, passive review, config review, log review, local test, active scan, or incident response.
- Confirm data boundaries: secrets, PII, PHI, payer/client data, private repos, financial data, logs, or public-only.
- Confirm stop conditions: production risk, third-party system, possible patient/client data, unclear owner, or unclear authorization.

Failure modes:

- Defensive checklist turns into unauthorized scanning.
- Agent receives sensitive data it does not need.
- Remediation advice assumes a deployment model that is wrong.

## 2. Identity, Authentication, And Authorization

Checklist items:

- Authentication is required for non-public functions.
- Authorization is enforced server-side for every sensitive action.
- Admin, user, clinician, operator, payer, support, and service-account roles are separated where relevant.
- Least privilege applies to humans, services, tokens, and agents.
- Privileged actions require logging and, where appropriate, step-up approval.
- Session, token, and password reset flows cannot leak account state or bypass review.
- Service tokens are scoped, rotated, stored outside source control, and not shared through screenshots or bug reports.

Evidence to request:

- Role matrix, route guards, API permission checks, token scopes, admin action logs, config files, and test cases.

## 3. Data, Privacy, And PHI Boundaries

Checklist items:

- Sensitive data classes are explicitly defined.
- PHI, payer data, client data, and production secrets are excluded from sandbox pilots.
- Data minimization is applied to prompts, logs, exports, analytics, and support workflows.
- Audit logs capture sensitive access without storing unnecessary sensitive content.
- Retention, deletion, and access-review rules exist.
- External AI, MCP, search, and analytics tools are reviewed before receiving sensitive data.

Evidence to request:

- Data inventory, retention policy, logging schema, access-control docs, DPA or vendor terms, and prompt/data-handling rules.

## 4. Application And API Security

Checklist items:

- Inputs are validated at trust boundaries.
- Output encoding and safe rendering protect against XSS.
- SQL, NoSQL, shell, template, and path inputs use safe APIs instead of string concatenation.
- File uploads check type, size, storage path, scanning, and access rules.
- APIs enforce authentication, authorization, rate limiting, request size limits, and error redaction.
- Webhooks verify signatures and replay windows.
- CORS, redirects, host headers, and security headers match the deployment model.
- Multi-tenant systems prevent cross-tenant reads, writes, exports, and logs.

Evidence to request:

- API routes, middleware, schemas, upload handlers, auth helpers, tests, deployment config, and edge/proxy rules.

## 5. Cloud, Infrastructure, And Configuration

Checklist items:

- Public exposure is deliberate and documented.
- Storage buckets, databases, queues, and dashboards are private by default.
- IAM permissions are scoped by role and environment.
- Secrets are stored in a secret manager or environment system, not source files.
- Infrastructure as code is reviewed for public access, broad roles, unmanaged encryption, and insecure defaults.
- Backups, restores, and environment separation are tested.
- Production changes have an approval and rollback path.

Evidence to request:

- IAM policies, bucket policies, Terraform or IaC, environment variables, deployment settings, network rules, and backup/restore records.

## 6. Supply Chain, SBOM, And Dependency Risk

Checklist items:

- Dependencies are pinned or locked.
- Security-critical dependencies are monitored and updated.
- SBOM exists for production releases or regulated deliveries when needed.
- Dependency vulnerability results are triaged by reachability, severity, exploitability, and business impact.
- Build scripts, package lifecycle hooks, CI actions, and installer scripts are treated as executable supply chain risk.
- Third-party skill packs, MCP servers, browser extensions, local agents, and CLIs are reviewed before install.
- Licenses are checked before commercial reuse.

Evidence to request:

- Lockfiles, SBOM, package manifests, CI workflows, GitHub advisories, OSV/NVD results, license report, and release process.

## 7. Logging, Detection, And Incident Readiness

Checklist items:

- Logs capture authentication, authorization, admin, data export, config change, failed access, and suspicious automation events.
- Logs avoid unnecessary PHI, secrets, and raw credentials.
- Alerts exist for privilege changes, anomalous access, failed auth spikes, token misuse, unexpected network egress, and production config changes.
- Incident roles, severity levels, evidence handling, containment, recovery, and communication paths are defined.
- Post-incident review updates controls, tests, runbooks, and training.

Evidence to request:

- Logging schema, alert rules, SIEM/dashboard config, incident runbook, escalation contacts, evidence log template, and postmortem template.

## 8. AI Agent, MCP, Hook, And Skill-Pack Safety

Checklist items:

- Agent permissions are scoped by task.
- Shell, network, off-repo writes, deployment, and secret reads require approval.
- MCP tools are reviewed for tool poisoning, overbroad schemas, secret exposure, and unexpected network egress.
- Local MCP ports are verified against expected processes before trust.
- Hooks, install scripts, workflow files, memory files, and skill packs are treated as supply chain artifacts.
- Untrusted repos, emails, PDFs, screenshots, diffs, and web pages are treated as hostile context.
- Persistent memory is narrow, project-local, and cleared after untrusted runs.
- Background agents have logs, process IDs, time bounds, and stop paths.

Evidence to request:

- Agent settings, MCP config, hook definitions, permission policy, memory files, tool call logs, process list, and network policy.

## 9. Healthcare And Regulated Workflow Lens

Use when the system or workflow touches healthcare, chronic care, utilization management, payer/provider workflows, clinical content, or patient-facing operations.

Checklist items:

- Clinical, operational, and administrative functions are separated.
- PHI handling is minimized and authorized.
- Patient-facing or clinical content has human clinical review before use.
- AI outputs are not treated as diagnosis, treatment, or coverage decisions without governed review.
- Consent, audit trail, role access, data retention, and escalation paths are documented.
- Vendor tools and local AI pilots are blocked from PHI until privacy, legal, security, and clinical safety review is complete.

Evidence to request:

- Workflow diagram, data-flow map, role matrix, clinical review policy, audit logging approach, consent language, and vendor/data-processing terms.

## 10. Prioritization Guide

Critical:

- Unauthorized access to sensitive data.
- Cross-tenant data exposure.
- Production secrets exposed.
- Privileged agent can read secrets, run shell, and communicate externally.
- Authentication or authorization bypass.
- Public production systems with unclear owner or logging.

High:

- Missing role separation for privileged operations.
- Unreviewed MCP, hook, or skill-pack execution.
- Public storage or broad IAM permissions.
- No audit logs for sensitive access.
- No incident containment path.

Medium:

- Incomplete dependency monitoring.
- Weak rate limits or upload controls.
- Partial logging without alerting.
- Framework mapping exists but evidence is thin.

Low:

- Documentation gaps that do not change control effectiveness.
- Cosmetic framework labels without operational impact.
