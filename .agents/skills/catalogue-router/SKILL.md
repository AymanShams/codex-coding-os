---
name: catalogue-router
description: Use at the start of any non-trivial task, repo or project start, capability review, tool or skill selection, plugin or MCP selection, or when the user asks what local capability to use. Routes through the shared skills-and-plugins catalogue without loading the whole file.
metadata:
  short-description: Route tasks through the local skills and plugins catalogue
---

# Catalogue Router

Use this skill to produce one unified advisory route for non-trivial work. The parent Codex agent remains the execution owner by default. Named skill selection and eligible worker support are two distinct parts of the same decision.

Canonical live authority:

- `${CODEX_HOME}\capability-routing\active-capabilities.json`
- `${CODEX_HOME}\capability-routing\routing-policy.yaml`
- `${CODEX_HOME}\capability-routing\route-decision.schema.json`
- `${CODEX_HOME}\capability-routing\route-decisions.sqlite3` for 24-hour exact issuance receipts

Bundled `references/capability-catalogue.md` is historical discovery evidence only. It must not select a route or compete with the canonical policy.

Optional generated routing hints from hooks or index scripts are candidates, not
authority. A capability is valid only when it materially helps the actual task
after the task, non-goals, source of truth, allowed action, validation standard,
and stop rule are clear.

## Skip Gate

Skip the catalogue check for clearly trivial or self-contained tasks:

- current time/date
- simple translation
- one-line rewrite
- direct shell command answer
- casual conversation with no tool choice
- a factual answer where live web/source verification is the controlling issue

For everything else, do a quick routing pass.

## Fast Routing Workflow

1. Extract 2-5 keywords from the user request.
   Include domain, file type, tool names, company names, framework names, and task type.
2. Run the task gate silently:
   actual task, non-goals, example-only material, controlling scope, source of
   truth, allowed action, validation standard, and stop rule.
3. Treat generated hook or index results as candidate hints only. Reject matches
   based only on generic words such as file, edit, verify, audit log, skill,
   plugin, tool, issue, workflow, that, or why.
4. Query the canonical router. Use the bundled helper:

   ```powershell
   & "$HOME\.codex\skills\catalogue-router\scripts\query-catalogue.ps1" -Query "implement the existing repository feature" -TaskInputPath "C:\bounded-inputs\task-input.json" -Json
   ```

   Use `-Json` when the exact decision must be passed to a bounded execution adapter. Supply the full task input from a UTF-8 JSON file with `-TaskInputPath`, or call the canonical CLI directly with `--task-input-json -` and pipe JSON through stdin. Never put the full task input on the command line. Do not combine `-TaskInputPath` with legacy `-TaskText`. The raw prompt hook can emit only a conservative non-executable route. When structured classification materially changes worker, recipe, source, memory, or task boundaries, query this same router again with complete task input, `-ProjectId` or `-Cwd`, `-TaskType`, `-Complexity`, `-SourceNeed`, `-LocalStackPurpose`, exact source scopes, applicable flags, `-ExecutionDisposition`, and no more than one `-EligibleWorkerFamily`.

5. Read the returned unified decision. Keep `skills.primary` and `skills.supports` separate from `support_workers`.
6. Choose the narrowest active capability first:
   local skill, enabled plugin skill, configured MCP server, then local installed tool.
7. If active capabilities leave a material gap, the parent Codex agent continues directly. Historical candidate evidence may be inspected only as a second pass for session-only support.
   Candidate, project-local, and reference-only items require explicit user authorization before use, must never be primary owners, and must never be installed universally by default.
8. If no catalogue hit is useful, proceed normally and say nothing unless the missing capability affects the recommendation.

## Routing Hints

- For product ideas that are vague, too linear, or too tidy, use `create-prd`, `working-backwards`, `product-strategy`, and `customer-journey-map` to add real users, constraints, edge cases, and tradeoffs before coding.

## Project Start Workflow

When starting or reviewing a project:

1. Search by project domain and stack, for example `React|Next.js|Supabase|RAG|security`.
2. Pick only 3-7 relevant capabilities.
3. Add them to the project `AGENTS.md` or project context only when actively working in that project or when the user asks for project setup.
4. Keep candidate tools labeled as `project-local pilot`, `reference only`, or `skip/avoid`.
5. Do not promote candidates to global skills or plugins. Use them only as authorized session-only support after active capabilities have been checked.

## Decision Rules

- There is one router. Do not create a model router, Hermes router, or second skill router.
- The installed `%USERPROFILE%\.codex\coding-os\hooks\capability-router` package path is retired non-live evidence. Never execute, import, enable, or copy it. Only `%USERPROFILE%\.codex\hooks\user_prompt_skill_router.py` is the active prompt router.
- Default execution is `codex_parent` with `gpt-5.6-sol` and high reasoning.
- Bind every route to the bounded instruction and complete task input. Normalize only the instruction for `task_text_sha256`. Hash the full canonical JSON object without value normalization for `task_input_sha256`. Every executable input contains an `execution_request_id` matching `[A-Za-z0-9._:-]{1,160}` and the exact structured `execution_disposition`. The route and registry receipt carry both. Any input field change requires a new route.
- `execution_disposition` is exactly `codex_only` with no eligible family, or `worker_support` with exactly one of `local_agent_stack`, `terra`, or `antigravity`. The Task Gate classification and complete task input must match. Prompt phrases can disambiguate an already eligible worker family or role but cannot create worker eligibility. Generic critique, review, challenge, architecture, model, or worker wording remains Codex-only without affirmative Task Gate flags and disposition. Explicit user requests are preserved by setting the exact Task Gate flags and eligible family.
- `codex_only` forbids generative `support_workers` and generative local worker recipes. It still permits independently authorized non-generative `runtime_status`, `memory_recall`, `source_lookup`, `retrieval_bundle`, and `literal_extraction` operations with empty worker roles.
- Every executable worker or non-generative recipe requires one exact complete Task Gate tuple. Flags, project, task type, complexity, purpose, source need, explicit source scopes, memory mode, persistence intent, and execution disposition must agree. Missing, partial, cross-recipe, or contradictory tuples fail closed. Do not infer capture, default a source scope, downgrade `both`, or switch to a different recipe.
- Worker execution requires `task_input_mode=complete`, `issuance.status=registered`, registry schema version 2, `failure_code=null`, every recipe input requirement, both matching task hashes, and an exact unexpired receipt. The raw hook's `conservative_instruction_only` route can advise Codex but cannot run a worker.
- Registry canonical JSON uses `json.dumps` with `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, and `allow_nan=False`, encoded as UTF-8. The WAL registry stores the route, both task hashes, snapshots, and issuance times, but no prompt or task input. Migration from an obsolete schema atomically purges old receipts. It never prunes an unexpired issuance. At 10,000 simultaneous unexpired routes, new issuance fails closed. Count and age pruning apply only to expired audit rows.
- `generic` authorizes no source scope. A named project accepts only its exact scope allowlist. Unauthorized scopes and explicit project/cwd contradictions fail closed.
- Explicit user exclusion overrides positive matching. Do not select Antigravity, Terra, or local workers when the prompt negates that family or the Task Gate sets `antigravity_excluded`, `terra_excluded`, or `local_support_excluded`.
- `gpt-5.6-terra` is eligible only as a required bounded read-heavy Codex child when the Task Gate sets `terra_read_heavy`, `terra_support_required`, and eligible family `terra`.
- Local Ollama workers are eligible only when the Task Gate supplies an approved bounded role recipe, `local_support_required`, and eligible family `local_agent_stack`, and the active manifest proves either the stability gateway or the local-agent-stack execution surface is active.
- Antigravity is an optional route, eligible only as an independent Codex child using `gemini-3.1-pro-high` when the Task Gate sets `antigravity_eligible`, `antigravity_support_required`, and eligible family `antigravity`, and the active manifest proves either the stability gateway or a dedicated adapter surface is active. Once selected, its bounded support pass is required for that route and still falls back to Codex on failure.
- A selected required worker performs the slice. It is not a shadow observer. Its `required` flag applies to that route recipe, while the parent Codex fallback still owns recovery.
- `local_execution` is part of this same decision. It maps empty worker roles to `runtime_status`, `memory_recall`, `source_lookup`, `retrieval_bundle`, or `literal_extraction`, and maps `fast`, `fast+critic`, `coding+critic`, and `critic` to the approved generative recipes. A `retrieval_bundle` preserves both authorized memory and indexed-source directives with `source_need=both`, memory mode `recall` or `recall_and_capture`, authorized source scopes, and `worker_roles=[]`. It is not a second routing decision.
- Pass an admitted route unchanged to the stability gateway's local-agent-stack `plan_catalogue_route` bridge with caller `request_id` exactly equal to `task_input.execution_request_id` and the separate complete task input. The same identifier may replay the same operation. A different identifier requires a newly issued route. Do not reconstruct the LAS admission in prose.
- Local memory remains `none` unless the same admitted local recipe has a valid deployment-owned project profile and structured Task Gate project or working-directory evidence for its authorized physical scope. Do not infer a memory scope from words in the prompt. Capture is eligible only at a durable task outcome.
- Capability objects retain `source_path` and `sha256`. Project only the selected skill instructions into a local worker. Never grant the worker skill scripts or tool access.
- A capability with `requires_live_dependencies` is selectable only when the fresh active manifest contains the exact dependency entry and the current config value proves it enabled. A dated catalogue, installed plugin root, skill file, or stale tool-family row is not dependency evidence. If either live inventory source is unreadable or disagrees, fail the dependency closed.
- When `capability_fallbacks` is non-empty, execute its `chosen_fallback` directly within its recorded bounds. Do not probe the known-unavailable dependency first. The receipt records the requested capability, required and unavailable dependencies, actual selected fallback capability, and whether the fallback is equivalent. A non-equivalent fallback must never claim the unavailable workflow's artifacts or fidelity.
- Hard MCP-dependent Creative Production Intake or Produce, Codex Security Deep Scan, and Cloudflare Web Performance skills are never selected while their live dependency is unavailable. Supported prompt-only Standard and diff scan workflows, typed OpenAI API-key destination confirmation, and portable Data Analytics outputs remain routable through their explicit fallback receipts. Sites export falls back to a self-contained HTML artifact when the full Sites app lifecycle is not live.
- A complete task input whose `instruction` exceeds 50,000 Unicode characters cannot be sent to a local-agent-stack worker. Remove only local workers and local execution, record `LOCAL_INPUT_TOO_LARGE_RETURNED_TO_CODEX`, and continue with the Codex parent. This limit does not apply to Terra or Antigravity. Literal extraction may bind its separately validated `literal_text` field up to the LAS contract limit.
- Before issuing an Antigravity route, bind `task_input.workspace_root` as the exact canonical string of an existing absolute directory and `task_input.output_schema_sha256` as the lowercase SHA-256 of the caller's canonical JSON output schema. The adapter compares the separately supplied workspace, output schema, and request identifier before skill read, durable admission, or process start. A different workspace, schema, or request identifier requires a newly issued route.
- Worker timeout, error, or unavailability returns the slice to the parent Codex agent. Never retry a timed-out worker automatically.
- Skill supports and worker supports each have their own maximum of two. A skill supplies instructions. A worker performs a bounded execution slice.
- The route digest identifies the exact normalized decision. The separate 24-hour registry receipt proves issuance by this router to cooperating runtimes. Neither is permission or proof that a worker executed.
- Installed active capability beats a new repo.
- Project-local pilot beats global install when setup cost, licensing, API keys, installation footprint, or behavior drift are material.
- Reference-only is correct for lists, prompt packs, broad skill packs, and design inspiration sources.
- Candidate and reference-only items are never primary skills. Ask before using them and keep use bounded to the current session.
- Skip/avoid is correct when security, provenance, or execution risk dominates the likely benefit.

## Conflict Control

- Choose one primary skill for the requested output or workflow.
- Add supporting skills only when they materially change a defined phase.
- Apply Tree of Thoughts and Algorithm of Thoughts as deterministic routing
  controls. Generate competing route hypotheses, evaluate container/action,
  domain/risk, authority, denied families, candidate visibility, source/data
  tools, and final owner in order, then keep rejection reasons or decision-path
  metadata when available.
- Treat bare framework-adjacent words as noise unless they are exact framework
  identifiers, filenames, framework phrases, changed-file evidence, or ordinary
  words paired with material domain context. A single ambiguous token such as
  next, app, router, spring, go, rails, or flask must not select a domain family
  by itself.
- Keep skill and plugin selection active for non-trivial tasks, but do not
  accept a capability that only matched generic routing language.
- The primary orchestrator controls sequence, stop gates, and completion claims. Supporting skills must not bypass it.
- Do not stack critique, pre-mortem, evidence, validation, and interview skills unless the user requests a formal review or the risk justifies it.
- When two skills overlap, use the one whose description most directly matches the requested output.
- Prefer execution skills for implementation, critique skills for explicit review, and evidence checking when source quality or recency is the core risk.
- If instructions conflict, stop at the safer or more source-faithful gate and state the conflict.

## Response Pattern

Usually keep this invisible and proceed with the selected capability.

Mention the routing only when it changes the approach:

`I checked the catalogue by keyword and found X is already the best fit, so I am using that instead of adding Y.`

For new projects, summarize the selected capabilities in one short block:

`Project-local capabilities: X for docs, Y for frontend QA, Z as a candidate pilot.`
