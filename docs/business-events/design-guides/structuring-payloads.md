# Structuring Payloads

A Business Event is more than a data record — it is a **signal that something meaningful happened** in your business. Designing a good payload means capturing enough context for any consumer to act on the event without needing to query additional systems.

## What makes a good business signal

A well-structured Business Event answers three questions:

| Question | Example field |
|----------|--------------|
| **What happened?** | `event_type: "Retail.Sales.VolumeAlert"` |
| **What was involved?** | `store_id`, `product_id`, `threshold_qty` |
| **When and where?** | `occurred_at`, `region` |

Keep payloads **factual and immutable** — describe what happened, not what should happen next. Decisions belong to the consumer, not the event.

## Anatomy of a Business Event schema

When you create a Business Event in Real-Time Hub, you define a schema with typed fields. These fields become the payload that publishers send and consumers receive.

```json
{
  "store_id": "STR-001",
  "product_id": "SKU-9821",
  "current_qty": 4,
  "threshold_qty": 10,
  "occurred_at": "2024-11-15T14:32:00Z"
}
```

### Field type reference

| Type | Use for |
|------|---------|
| `string` | IDs, names, categories, status values |
| `integer` | Counts, quantities, ranks |
| `number` | Prices, percentages, measurements |
| `boolean` | Flags, toggles |
| `datetime` | Timestamps — always use ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`) |

## CloudEvents under the hood

Business Events uses the [CloudEvents](https://cloudevents.io/) specification as its envelope format. When you publish a payload, the platform wraps it automatically with standard metadata:

```json
{
  "specversion": "1.0",
  "type": "Retail.Inventory.LowStockThreshold",
  "source": "/tenants/{tenant-id}/workspaces/{workspace-id}/items/{item-id}",
  "id": "a8f3c1d2-...",
  "time": "2024-11-15T14:32:00Z",
  "dataversion": "v1",
  "data": {
    "store_id": "STR-001",
    "product_id": "SKU-9821",
    "current_qty": 4,
    "threshold_qty": 10,
    "occurred_at": "2024-11-15T14:32:00Z"
  }
}
```

You only define and send the `data` block. The CloudEvents envelope (`specversion`, `source`, `id`, `time`) is added by the platform.

### What this means for consumers

- **Activator** receives the full CloudEvents envelope and lets you reference fields inside `data` directly in your alert conditions
- **Eventhouse** stores the `data` fields as columns in the KQL table, with the envelope metadata available as additional columns

## Design guidelines

**Include a timestamp in your payload.** Even though the envelope has a `time` field, having `occurred_at` inside `data` lets you capture the business moment precisely — for example, when a transaction was processed, not when the event was published.

**Use stable, opaque IDs.** Field values like `store_id` or `run_id` should be identifiers that consumers can use to look up additional context if needed. Avoid embedding mutable state.

**Keep payloads flat when possible.** Nested objects are supported but make KQL queries and Activator field mapping more complex. Prefer flat structures unless nesting is semantically meaningful.

**Version from the start.** Always publish with `dataVersion="v1"`. When the schema evolves, increment the version rather than changing existing fields. See [Schema Versioning](schema-versioning.md) for details.
