# Philosophy

Codex Coding OS separates decisions from execution.

The user approves one complete immutable contract. A pure reducer decides every
lifecycle transition. A durable store serializes those decisions. A
deterministic supervisor chooses only from the approved finite graph. Workers
receive the smallest effective execution boundary. Evidence and external
effects remain explicit and exact-head bound.

The system favors:

- exact identity over path-shaped assumptions
- immutable scope over model-created work
- finite budgets over retry loops
- native bind-before-turn over caller-declared roles
- trusted execution evidence over assertion-shaped records
- query-before-repeat over uncertain external mutations
- durable STOP over chat-local intent
- one engine over compatibility or shadow authority

Repository mirrors remain useful for human context, but current lifecycle state
belongs outside Git. Product evidence, engine status, and process status are
reported separately so one cannot impersonate another.
