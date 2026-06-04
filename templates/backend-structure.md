# Backend Structure

## Architecture
{{short_backend_architecture_summary}}

## Domains
| Domain | Responsibility | Files or modules |
|---|---|---|
| {{domain}} | {{responsibility}} | {{files}} |

## Data Model
| Entity | Fields | Relationships | Notes |
|---|---|---|---|
| {{entity}} | {{fields}} | {{relationships}} | {{notes}} |

## API Routes
| Method | Route | Purpose | Auth required |
|---|---|---|---|
| GET | {{route}} | {{purpose}} | Yes or No |

## Validation
| Input | Validation rule | Where enforced |
|---|---|---|
| {{input}} | {{rule}} | Client, server, database |

## Errors
| Error type | HTTP status or behavior | Response shape |
|---|---|---|
| {{error_type}} | {{status}} | {{shape}} |

## Jobs and Background Work
| Job | Trigger | Retry behavior |
|---|---|---|
| {{job}} | {{trigger}} | {{retry_behavior}} |

## Observability
- Log important lifecycle events.
- Do not log secrets or sensitive personal data.
- Add basic request failure visibility.
- Record migration and deployment changes.

