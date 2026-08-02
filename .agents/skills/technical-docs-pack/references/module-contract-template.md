# Module Contracts

Use this consolidated template only when at least one project module has a
stable interface, an independent lifecycle, meaningful dependencies, or
material failure behavior. Do not create module contracts for arbitrary folder
boundaries or short-lived implementation groupings.

## Applicability Evidence

| Condition | Value | Controlling evidence |
|---|---|---|
| Stable interfaces exist | `<true-or-false>` | `<source>` |
| Independent module lifecycles exist | `<true-or-false>` | `<source>` |
| Meaningful module dependencies exist | `<true-or-false>` | `<source>` |
| Material module failure behavior exists | `<true-or-false>` | `<source>` |

## Qualifying Modules

| Module | Qualification reason | Owner | Lifecycle | Contract source |
|---|---|---|---|---|
| `<module>` | `<one or more trigger conditions>` | `<owner>` | `<lifecycle>` | `<source>` |

## Contract: `<Module Name>`

### Responsibility

`<single bounded responsibility and explicit non-responsibilities>`

### Stable Interfaces

| Interface | Consumer | Input | Output | Compatibility rule |
|---|---|---|---|---|
| `<interface>` | `<consumer>` | `<input>` | `<output>` | `<rule>` |

### Dependencies

| Dependency | Direction | Purpose | Availability assumption | Fallback |
|---|---|---|---|---|
| `<dependency>` | `<inbound-or-outbound>` | `<purpose>` | `<assumption>` | `<fallback>` |

### Lifecycle

- Creation or initialization: `<behavior>`
- Change and compatibility: `<behavior>`
- Shutdown or removal: `<behavior>`
- Independent release or migration needs: `<behavior>`

### Failure Behavior

| Failure | Observable effect | Containment | Recovery | Verification |
|---|---|---|---|---|
| `<failure>` | `<effect>` | `<boundary>` | `<recovery>` | `<check>` |

## Validation Checklist

- Every module listed meets at least one qualification condition.
- Interfaces name real producers, consumers, inputs, and outputs.
- Dependencies and lifecycle assumptions match the current architecture.
- Material failures have containment, recovery, and verification behavior.
