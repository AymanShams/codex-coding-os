# Component Classification

| Class | Components | Authority |
|---|---|---|
| Lifecycle | `campaign_engine/model.py`, `campaign_engine/reducer.py` | Defines immutable state and the only transition relation |
| Durable state | `campaign_engine/store.py` | Owns revisions, epochs, leases, operations, evidence, and outbox state |
| Execution | `campaign_engine/supervisor.py`, `campaign_engine/host.py`, `campaign_engine/evidence.py` | Executes only reducer-authorized work within exact boundaries |
| External effects | `campaign_engine/effects.py` | Executes and reconciles stable publication operations |
| Admission | `campaign_engine/admission.py` | Verifies exact source, worktree, scope, and installed runtime |
| Interface | `campaign_engine/cli.py`, thin hooks and repository adapters | Transports requests and results without adding lifecycle logic |
| Legacy evidence | `campaign_engine/legacy.py`, `case_state.py` retirement stub | Read-only archive access and deterministic command denial |
| Knowledge | Skills, templates, and documentation | Guidance only, never lifecycle authority |

Only the reducer decides transitions. Only the SQLite store persists current
lifecycle state. Every other component is a bounded client or effect executor.
