# Event Schema

Every Business Event in Microsoft Fabric Real-Time Hub is defined by a schema before any data is published. The schema describes the structure and types of the event payload.

## What you define vs. what Real-Time Hub handles

Business Events are built on **[CloudEvents 1.0](https://cloudevents.io/)**, an industry-standard specification for describing event data. However, you do not need to interact with the CloudEvents envelope directly. Real-Time Hub handles the transport layer for you.

Your responsibility is to define only the **business properties** of the event: what data it carries and what each field means.

| Layer | Managed by | Example |
|---|---|---|
| Transport envelope | Real-Time Hub | CloudEvents headers, routing, delivery |
| Business payload | You | `store_id`, `deviation_pct`, `severity` |

## Schema format

Business Events use a **record schema** with the following top-level structure:

```json
{
  "type": "record",
  "name": "<EventName>",
  "fields": [
    {
      "name": "<field_name>",
      "type": "<field_type>",
      "doc": "<description>"
    }
  ]
}
```

| Property | Required | Description |
|----------|----------|-------------|
| `type` | Yes | Always `"record"` |
| `name` | Yes | PascalCase name identifying the event type |
| `fields` | Yes | Array of field definitions |

## Field definition

Each field in the `fields` array has the following properties:

| Property | Required | Description |
|----------|----------|-------------|
| `name` | Yes | Field name (snake_case recommended) |
| `type` | Yes | Data type of the field |
| `doc` | No | Human-readable description of the field |

## Supported field types

| Type | Description | Example value |
|------|-------------|---------------|
| `string` | UTF-8 text | `"store-mx-042"` |
| `int` | 32-bit integer | `1250` |
| `float` | 32-bit floating point | `-40.6` |
| `boolean` | True or false | `true` |

## Example schema

The following schema defines a `Retail.Sales.VolumeAlert` event with six fields. This JSON represents the schema you define when creating a Business Event in [Real-Time Hub → Business Events](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/create-business-events). During creation, you can paste this directly into the code editor. If you need a new Event Schema Set, you can create one inline as part of the Business Event creation flow, or select an existing one.

```json
{
  "type": "record",
  "name": "Retail.Sales.VolumeAlert",
  "fields": [
    {
      "name": "store_id",
      "type": "string",
      "doc": "Unique identifier of the store reporting the alert"
    },
    {
      "name": "expected_transactions",
      "type": "int",
      "doc": "Expected number of transactions based on historical average"
    },
    {
      "name": "actual_transactions",
      "type": "int",
      "doc": "Actual transactions recorded in the current monitoring window"
    },
    {
      "name": "deviation_pct",
      "type": "float",
      "doc": "Percentage deviation from expected volume. Negative means below expected."
    },
    {
      "name": "window_start",
      "type": "string",
      "doc": "Start of the monitoring window in ISO 8601 format"
    },
    {
      "name": "severity",
      "type": "string",
      "doc": "Alert severity level: low, medium, high"
    }
  ]
}
```

## Schema naming conventions

| Convention | Example |
|------------|---------|
| `name` (event) | PascalCase: `VolumeAlert`, `LowStockThreshold` |
| `name` (field) | snake_case: `store_id`, `deviation_pct` |
| `doc` (field) | Complete sentence describing the field's purpose and expected values |

### Namespaces (optional but recommended)

For projects with multiple event types across different business domains, prefixing the event name with a namespace prevents naming collisions and makes the event's origin immediately clear.

The recommended pattern is `Domain.Subdomain.EventName` in PascalCase:

| Without namespace | With namespace |
|---|---|
| `VolumeAlert` | `Retail.Sales.VolumeAlert` |
| `LowStockThreshold` | `Retail.Inventory.LowStockThreshold` |
| `RunCompleted` | `DataOps.Pipeline.RunCompleted` |

All examples in this repository follow the namespace convention.

