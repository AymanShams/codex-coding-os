---
name: defensive-security-checklist
description: Use when the user asks for an authorized defensive cybersecurity checklist, control gap checklist, security review checklist, hardening checklist, NIST CSF or MITRE ATT&CK mapped defensive review, agent/MCP/harness safety checklist, supply-chain security checklist, cloud security checklist, incident-preparedness checklist, or non-executing security assessment plan. Use for defensive checklist mining and review only. Do not use for exploit development, offensive testing, third-party scanning, active incident command, broad Codex Security scans, language-specific secure coding reports, or repository-grounded AppSec threat models.
---

# Defensive Security Checklist

Use this skill to create defensive, authorized, evidence-based security checklists and review plans. It is intentionally a checklist and control-mapping skill, not an offensive testing skill and not a global import of any cybersecurity skill pack.

## Routing

Use this skill when the user asks for:

- defensive-only cybersecurity checklist
- security hardening checklist
- control gap review
- NIST CSF, MITRE ATT&CK, MITRE ATLAS, D3FEND, or NIST AI RMF mapping for defensive planning
- supply-chain, SBOM, dependency, or vendor software risk checklist
- agent, MCP, skill-pack, hook, or local AI-workbench security checklist
- cloud, IAM, API, logging, or incident-readiness checklist without active scanning

Use another skill instead when:

- `security-best-practices`: language or framework-specific secure coding review or vulnerability report
- `security-threat-model`: repository-grounded AppSec threat model with assets, trust boundaries, attackers, and abuse paths
- `codex-security:*`: broad security scan, finding discovery, or finding validation
- `crisis-command-center`: active security, data, privacy, or operational incident
- `security-ownership-map`: git history, ownership, bus-factor, and reviewer coverage risk

## Defensive Scope Gate

Before producing the checklist, identify:

1. Asset or workflow in scope.
2. Owner or authorization basis.
3. Environment: local, sandbox, staging, production, partner, third party, or unknown.
4. Data sensitivity: secrets, PII, PHI, payer/client data, financial data, source code, logs, or public-only.
5. Allowed actions: checklist only, passive review, log review, config review, local test, or active scan.

If authorization is unclear, keep the output to passive checklist questions and evidence requests. Do not give steps for exploitation or active testing.

## Hard Boundaries

Allowed by default:

- checklists
- evidence requests
- defensive control mapping
- remediation backlogs
- policy and governance recommendations
- safe review prompts for authorized internal systems

Require explicit authorization and safe environment:

- running scanners
- probing endpoints
- querying live infrastructure
- testing credentials or permissions
- inspecting production logs
- using vulnerability-intelligence APIs with sensitive project data

Do not provide:

- exploit code
- evasion instructions
- credential theft workflows
- persistence, lateral movement, or command-and-control setup
- scanning instructions for third-party systems
- instructions that bypass consent, access controls, monitoring, or rate limits

## Workflow

1. **Classify the defensive task**
   - Choose one or more domains: application/API, identity, cloud, data/privacy, supply chain, logging/detection, incident readiness, AI-agent/MCP, or healthcare governance.
   - If a source file, repo, architecture, or artifact is available, ground the checklist in that evidence. If not, label assumptions.

2. **Load reference details only if needed**
   - For a normal checklist, read `references/defensive-checklist-taxonomy.md`.
   - For a narrow code-security review, route to `security-best-practices` instead of loading this reference.

3. **Separate facts from assumptions**
   - Facts: evidence found in the repo, docs, config, logs, user-provided material, or verified sources.
   - Assumptions: environment, ownership, exposure, and data sensitivity not yet proven.
   - Unknowns: evidence needed before a high-confidence verdict.

4. **Build a prioritized checklist**
   - Group by control area.
   - Mark each item as `Required`, `Recommended`, or `Context-dependent`.
   - For each item, include the evidence to look for, failure mode, and remediation owner when known.

5. **Map frameworks carefully**
   - Use framework labels as orientation, not as fake compliance proof.
   - NIST CSF is useful for governance and control categories.
   - MITRE ATT&CK is useful for attacker technique awareness.
   - MITRE ATLAS and NIST AI RMF are useful for AI-agent and model-risk workflows.

6. **Output defensively**
   - Include a direct verdict when the user asks for one.
   - Separate high-impact gaps from nice-to-haves.
   - State what cannot be confirmed.
   - Add a short "Do not run yet" note for any action that needs authorization, sandboxing, or owner approval.

## Output Shape

Use this structure unless the user asks otherwise:

```markdown
## Direct Answer

## Scope And Assumptions

## Defensive Checklist

| Priority | Control Area | Checklist Item | Evidence Needed | Failure Mode | Owner |
|---|---|---|---|---|---|

## Highest-Risk Gaps

## Safe Next Actions

## Do Not Do Without Approval

## Sources Or Evidence Reviewed
```

## Source Mining Note

This skill was created after reviewing `mukul975/Anthropic-Cybersecurity-Skills` as a defensive checklist taxonomy source. The repo itself is not installed globally. Do not import its broad skill pack or execute its workflows unless a separate source, license, security, and authorization review is completed.
