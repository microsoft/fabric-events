# Design Guides

Design guides cover design decisions, platform behavior, and architectural considerations for working with Business Events. Unlike scenarios, which walk through a complete solution end to end, design guides focus on a single concept you can apply across any project.

## Available guides

| Guide | Description |
|---------|-------------|
| [Event-Driven Architectures in Microsoft Fabric](event-driven-architectures.md) | How event-driven patterns work in Fabric and when to use them over polling or direct calls |
| [Decision Guide](../introduction/decision-guide.md) | When to use Business Events vs. other Fabric capabilities |
| [Structuring Payloads](structuring-payloads.md) | How to design clean, meaningful event payloads and how CloudEvents works under the hood |
| [Handling Retries](handling-retries.md) | Built-in retry behavior and how to design idempotent consumers |
| [Schema Versioning](schema-versioning.md) | How to evolve a schema and what changes publishers and consumers need to make |
| [Multi-Consumer Fanout](multi-consumer-fanout.md) | Routing one event to multiple independent consumers |
