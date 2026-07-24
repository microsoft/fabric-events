# Scenarios

Each scenario is a complete, end-to-end walkthrough of a real business problem solved with Business Events. Every scenario includes a business context, an architecture diagram, an Avro event schema, publisher code, and consumer configuration.

---

## Detect & Alert

Use this pattern when you want to notify a person or system the moment a condition is met. The publisher detects a condition and emits an event. Activator evaluates a rule and immediately notifies a team or triggers a response. These scenarios are the fastest path from a condition to an action.

| Scenario | Industry | Publisher | Consumer | Level |
|----------|----------|-----------|----------|-------|
| [Sales Volume Alert](sales-volume-alert.md) | Retail | Notebook | Activator | Beginner |
| [Real-Time Stream Alert](real-time-stream-alert.md) | Finance | Eventstream | Activator | Intermediate |

!!! tip "Good first scenario"
    Start with [Sales Volume Alert](sales-volume-alert.md) if you are new to Business Events. It covers the full publish-subscribe cycle with the minimum number of moving parts.

---

## Decide & Act

Use this pattern when you want one event to trigger multiple automated actions across independent systems. The publisher evaluates context — customer history, fraud rules, inventory levels, SLA data — makes a decision, and publishes an event that carries the outcome. Multiple independent consumers act on it simultaneously without knowing about each other. Adding a new action means adding a new subscription, not changing the publisher.

| Scenario | Industry | Publisher | Consumer | Level |
|----------|----------|-----------|----------|-------|
| [Business Automation Loop](business-automation-loop.md) | Retail | Activator | Activator | Intermediate |
| [Abandoned Cart Recovery](abandoned-cart-recovery.md) | Ecommerce | User Data Function | Activator, Eventhouse | Intermediate |
| [Fraud Response Before Fulfillment](fraud-response-before-fulfillment.md) | Finance | User Data Function | Activator, Eventhouse | Advanced |
| [Real-Time Viewer Recommendations](real-time-viewer-recommendations.md) | Media | User Data Function | Activator, Eventhouse | Advanced |
| [Shipment Delay Auto-Mitigation](shipment-delay-auto-mitigation.md) | Logistics | User Data Function | Activator, Eventhouse | Advanced |

!!! note "The fan-out advantage"
    **Fraud Response Before Fulfillment** and **Shipment Delay Auto-Mitigation** each trigger three independent Activator rules from a single event. This is the fan-out pattern: one publisher, many independent consumers, zero coordination overhead.

---

## Track & Analyze

Use this pattern when you want every event stored as a queryable, auditable record. Every event is automatically persisted in Eventhouse as a structured, queryable record. These scenarios show how to build operational dashboards, audit trails, and reorder pipelines using KQL — without writing a single line of ingestion code.

| Scenario | Industry | Publisher | Consumer | Level |
|----------|----------|-----------|----------|-------|
| [Low Stock Threshold](low-stock-threshold.md) | Supply Chain | User Data Function | Eventhouse | Beginner |
| [Data Lineage Audit](data-lineage-audit.md) | DataOps | Notebook | Eventhouse | Intermediate |

!!! tip "Combine patterns"
    Most production solutions combine patterns. **Abandoned Cart Recovery**, **Fraud Response Before Fulfillment**, **Real-Time Viewer Recommendations**, and **Shipment Delay Auto-Mitigation** pair Decide & Act with Track & Analyze: Activator takes the immediate action while Eventhouse stores the event for reporting, model retraining, or compliance.

---

## How to read a scenario

Each scenario follows the same structure:

1. **Business context** — the real-world problem and why it matters
2. **Architecture** — Mermaid flow diagram of the full solution
3. **Step 1: Create the Business Event** — Avro schema definition
4. **Step 2: Publish from the source workload** — publisher code (Notebook, User Data Function, Eventstream, or Activator)
5. **Step 3: Configure the consumer** — Activator rule setup and/or KQL queries for Eventhouse
6. **Step 4: Test end to end** — how to verify the full flow
7. **What happens next** — extension ideas and related scenarios
