# Tech Stack Document

## Recommended Stack
| Layer | Choice | Reason |
|---|---|---|
| Frontend | {{frontend_framework}} | {{reason}} |
| Backend | {{backend_runtime}} | {{reason}} |
| Database | {{database}} | {{reason}} |
| Authentication | {{auth}} | {{reason}} |
| Hosting | {{hosting}} | {{reason}} |
| Testing | {{testing}} | {{reason}} |

## Constraints
- Beginner maintainability: {{constraint}}
- Cost limit: {{constraint}}
- Deployment limit: {{constraint}}
- Data sensitivity: {{constraint}}

## Alternatives Considered
| Option | Pros | Cons | Decision |
|---|---|---|---|
| {{option}} | {{pros}} | {{cons}} | Accept or reject |

## Environment Variables
| Name | Purpose | Required locally | Required in production |
|---|---|---|---|
| {{ENV_VAR}} | {{purpose}} | Yes or No | Yes or No |

## Package Policy
- Add dependencies only when they remove real complexity.
- Prefer maintained packages with active releases and clear documentation.
- Do not add a dependency for one small helper function.
- Record major dependency decisions here.

## Deployment Plan
1. {{deployment_step_1}}
2. {{deployment_step_2}}
3. {{deployment_step_3}}

## Local Development Commands
```powershell
{{install_command}}
{{dev_command}}
{{test_command}}
{{lint_command}}
```

