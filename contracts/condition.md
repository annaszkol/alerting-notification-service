# Condition JSON contract (owner A, spec §4, §5, §13)

Code: [`condition.py`](condition.py). Evaluation lives in `libs/conditions/`.
The same AST is used to decide whether an event matches a **rule** and whether a
payload matches a **schema** (`schema.detect`).

## Grammar

```
Condition  = { "all": [Condition, ...] }
           | { "any": [Condition, ...] }
           | { "not": Condition }
           | Comparison

Comparison = { "field": <path>, "op": <Op>, "value": <JSON value> }
```

`all`, `any`, `not` and `field/op/value` are **mutually exclusive**: a valid condition
object has exactly one of those shapes. `field` is a path (see `letter.md`). `value`
is a JSON value: string, number, boolean, null, list or dict.

## Operators

| Op                | Applies to                   | Meaning                                                                         |
| ----------------- | ---------------------------- | ------------------------------------------------------------------------------- |
| `eq`            | any                          | `actual == value`. Numbers compare numerically (`1 == 1.0`).                |
| `ne`            | any                          | `actual != value`.                                                            |
| `gt/gte/lt/lte` | numbers, strings, severities | See note below.                                                                 |
| `in`            | scalar vs list               | `actual in value`.                                                            |
| `not_in`        | scalar vs list               | `actual not in value`.                                                        |
| `glob`          | string vs string             | fnmatch-style: `*`, `?`.                                                    |
| `regex`         | string vs string             | `re.search`. Pattern length capped (ReDoS).                                   |
| `contains`      | string or list               | Substring for strings, membership for lists.                                    |
| `exists`        | any                          | `value` is `true` or `false`. `false` is true when the field is absent. |

`gt/gte/lt/lte` rules:

- If `value` is a number, `actual` is coerced with `float()`. A `TypeError` or
  `ValueError` -> `false`.
- If `value` is a string and the field name is in `SEVERITY_FIELD_NAMES`, both sides
  are compared through `SEVERITY_ORDER`. Unknown severity strings -> `false`.
- If `value` is a string otherwise, `actual` and `value` are compared with `<`/`<=`/
  `>`/`>=` (lexicographic).
- Booleans are not comparable.

## Missing and malformed fields

The evaluator never raises. Any of the following evaluates the comparison to `false`:

- field path resolves to `None` or is absent,
- field path hits a scalar before the end,
- type mismatch between `actual` and `value`,
- unknown `op` or a comparison whose shape is invalid (missing `value`, `value` not a
  list for `in`/`not_in`, `exists` value not a boolean),
- regex that fails to compile.

`not` is the only operator that inverts a `false` into a `true`. `exists: false` is
the only comparison that produces `true` on an absent field.

## Examples

"any WORKFLOW_FAILED where workflowName = order-settlement and environment = prod"

```json
{
  "all": [
    {"field": "type", "op": "eq", "value": "WORKFLOW_FAILED"},
    {"field": "workflowName", "op": "eq", "value": "order-settlement"},
    {"field": "environment", "op": "eq", "value": "prod"}
  ]
}
```

"log lines from pricing-engine-* at WARN or above"

```json
{
  "all": [
    {"field": "service", "op": "glob", "value": "pricing-engine-*"},
    {"field": "severity", "op": "gte", "value": "warning"}
  ]
}
```

"trade.rejected.* where desk = rates and notional > 10,000,000"

```json
{
  "all": [
    {"field": "$schema", "op": "glob", "value": "trade.rejected.*"},
    {"field": "payload.desk", "op": "eq", "value": "rates"},
    {"field": "payload.notional", "op": "gt", "value": 10000000}
  ]
}
```

"anything critical from payments-api"

```json
{
  "all": [
    {"field": "labels.service", "op": "eq", "value": "payments-api"},
    {"field": "labels.severity", "op": "eq", "value": "critical"}
  ]
}
```

"error.code in [TIMEOUT, REJECTED]"

```json
{"field": "error_code", "op": "in", "value": ["TIMEOUT", "REJECTED"]}
```

The same AST is used by `schema.detect`. A schema that only recognises its own envelope
declares it directly:

```json
{"all": [
    {"field": "$schema", "op": "eq", "value": "trade.rejected.v2"},
    {"field": "meta.producer", "op": "eq", "value": "trade-gateway"}
]}
```
