# Filter Events by Field Value

Configure an Activator alert that only triggers when a specific field in the event payload meets a condition.

## When to use this recipe

By default, an Activator alert set on a Business Event fires on every event received. Use field-level filtering when you only want to react to a subset of events — for example, only when `region == "West"` or `total_sales > 50000`.

## Set up the filter in Activator

When configuring the alert in Real-Time Hub → Business events → Set alert:

1. In the **Monitor** section, locate the **Filter** option
2. Select the field you want to filter on from the payload
3. Set the operator and value

### Example: trigger only for a specific region

| Field | Operator | Value |
|-------|----------|-------|
| `region` | Equals | `West` |

### Example: trigger only above a sales threshold

| Field | Operator | Value |
|-------|----------|-------|
| `total_sales` | Is greater than | `50000` |

### Example: combine multiple conditions

| Field | Operator | Value |
|-------|----------|-------|
| `region` | Equals | `West` |
| `total_sales` | Is greater than | `50000` |

When multiple conditions are set, all must be true for the alert to fire (AND logic).

## Filter vs Condition

Activator has two places where you can restrict when an alert fires:

| | Filter | Condition |
|-|--------|-----------|
| **Where** | Monitor section | Condition section |
| **Purpose** | Exclude events from consideration entirely | Evaluate a logical rule on each event |
| **Use for** | Simple field equality / range checks | Stateful logic — e.g., "value increased", "crossed threshold" |

For most field-value filtering, use the **Filter** in the Monitor section. Reserve the Condition section for stateful evaluations.
