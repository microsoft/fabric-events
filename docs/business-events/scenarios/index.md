# Scenarios

Each scenario is a complete, end-to-end walkthrough of a real business problem solved with Business Events. Every scenario includes a business context, an architecture diagram, an Avro event schema, publisher code or UI setup, and consumer configuration.

---

## Detect & Alert

Use this pattern when you want to notify a person or system the moment a condition is met. The publisher detects a condition and emits an event. Activator evaluates a rule and immediately notifies a team or triggers a response. Eventhouse independently stores the same published findings for historical analysis, so these scenarios pair an immediate response with a queryable history.

| Scenario | Industry | Publisher | Consumer(s) | Level |
|----------|----------|-----------|-------------|-------|
| [Sales Volume Alert](sales-volume-alert.md) | Retail | Notebook | Activator, Eventhouse | Beginner |
| [Real-Time Stream Alert](real-time-stream-alert.md) | Finance | Eventstream | Activator, Eventhouse | Intermediate |
| [Revenue Below Target](revenue-below-target.md) | Sales | Activator | Activator, Eventhouse | Intermediate |

!!! tip "Good first scenario"
    Start with [Sales Volume Alert](sales-volume-alert.md) if you are new to Business Events. It covers the full publish-subscribe cycle with the minimum number of moving parts.

---

## Decide & Act

Use this pattern when you want one event to trigger multiple automated actions across independent systems. The publisher evaluates context — customer history, fraud rules, inventory levels, SLA data — makes a decision, and publishes an event that carries the outcome. Multiple independent consumers act on it simultaneously without knowing about each other. Adding a new action means adding a new subscription, not changing the publisher.

| Scenario | Industry | Publisher | Consumer(s) | Level |
|----------|----------|-----------|-------------|-------|
| [Customer Loyalty Milestone](customer-loyalty-milestone.md) | Retail | Activator | Activator, Eventhouse | Intermediate |
| [Abandoned Cart Recovery](abandoned-cart-recovery.md) | Ecommerce | User Data Function | Activator, Eventhouse | Intermediate |
| [Discount Approval Evaluation and Event Publication](discount-approval-routing.md) | Sales | User Data Function | Activator, Eventstream, Eventhouse | Intermediate |
| [Fraud Response Before Fulfillment](fraud-response-before-fulfillment.md) | Finance | User Data Function | Activator, Eventhouse | Advanced |
| [Real-Time Viewer Recommendations](real-time-viewer-recommendations.md) | Media | User Data Function | Activator, Eventhouse | Advanced |
| [Shipment Delay Auto-Mitigation](shipment-delay-auto-mitigation.md) | Logistics | User Data Function | Activator, Eventhouse | Advanced |

!!! note "The fan-out advantage"
    **Fraud Response Before Fulfillment** and **Shipment Delay Auto-Mitigation** each trigger three independent Activator rules from a single event. This is the fan-out pattern: one publisher, many independent consumers, zero coordination overhead.

---

## Track & Analyze

Use this pattern when you want published events stored as queryable records. With **Analyze in Eventhouse** enabled during Business Event creation, received events are automatically persisted in a dedicated KQL table. These scenarios use that history for operational dashboards, audit trails, or replenishment planning without custom ingestion code. Low Stock Threshold also includes an Activator notification; Data Lineage Audit focuses on analytical history.

| Scenario | Industry | Publisher | Consumer(s) | Level |
|----------|----------|-----------|-------------|-------|
| [Low Stock Threshold](low-stock-threshold.md) | Supply Chain | User Data Function | Activator, Eventhouse | Beginner |
| [Data Lineage Audit](data-lineage-audit.md) | DataOps | Notebook | Eventhouse | Intermediate |

!!! tip "Combine patterns"
    **Sales Volume Alert**, **Real-Time Stream Alert**, **Customer Loyalty Milestone**, **Revenue Below Target**, **Low Stock Threshold**, and **Discount Approval Evaluation and Event Publication** all pair Activator responses with Eventhouse history. The same combination appears in **Abandoned Cart Recovery**, **Fraud Response Before Fulfillment**, **Real-Time Viewer Recommendations**, and **Shipment Delay Auto-Mitigation**. Each walkthrough verifies both paths: the immediate response and the stored event.

---

## Deployment implementations

All walkthroughs above remain available independently of deployment automation. Six scenarios also have implementation folders and integrated deployment guides. **Reference available** means native definitions and separate deployment/test entry points exist; it does not mean a generally available customer provisioning flow. **In preparation** means a complete deployment is not yet implemented.

| Scenario | Deployment status | Authoritative guide | Source |
|---|---|---|---|
| [Sales Volume Alert](sales-volume-alert.md#deploy-this-scenario) | Reference available | [Deployment guide](sales-volume-alert/deployment.md) | [`sales-volume-alert`](https://github.com/microsoft/fabric-events/tree/main/cicd/sales-volume-alert) |
| [Real-Time Stream Alert](real-time-stream-alert.md#deploy-this-scenario) | Reference available | [Deployment guide](real-time-stream-alert/deployment.md) | [`high-value-payment`](https://github.com/microsoft/fabric-events/tree/main/cicd/high-value-payment) |
| [Discount Approval Evaluation and Event Publication](discount-approval-routing.md#deploy-this-scenario) | Reference available; portal subscription required | [Deployment guide](discount-approval-routing/deployment.md) | [`discount-approval-routing`](https://github.com/microsoft/fabric-events/tree/main/cicd/discount-approval-routing) |
| [Low Stock Threshold](low-stock-threshold.md#deploy-this-scenario) | Reference available; individual test destination validated | [Deployment guide](low-stock-threshold/deployment.md) | [`low-stock-threshold`](https://github.com/microsoft/fabric-events/tree/main/cicd/low-stock-threshold) |
| [Revenue Below Target](revenue-below-target.md#deploy-this-scenario) | In preparation | [Scope and remaining work](revenue-below-target/deployment.md) | [`revenue-below-target`](https://github.com/microsoft/fabric-events/tree/main/cicd/revenue-below-target) |
| [Fraud Response Before Fulfillment](fraud-response-before-fulfillment.md#deploy-this-scenario) | Reference available; repeatable native flows and persisted enforcement validated | [Deployment guide and verification](fraud-response-before-fulfillment/deployment.md) | [`fraud-response-before-fulfillment`](https://github.com/microsoft/fabric-events/tree/main/cicd/fraud-response-before-fulfillment) |

Follow the complete manual walkthrough for customer setup. Each deployment guide explains its reference implementation's scope, prerequisites and remaining limitations. Resource preparation and testing remain separate operations; do not treat a reference script as proof of provisioning support or successful consumer delivery.

Each deployment guide is a nested subpage of its scenario, with the site's navigation and search. Documentation lives only here in `docs`; executable folders and ZIPs contain no documentation copies. Local `.deployments` records and configuration remain excluded from Git and are not published by this site. Other scenarios do not currently have a corresponding deployment package.

## How to read a scenario

Each scenario follows the same structure:

1. **Business context** — the real-world problem and why it matters
2. **Architecture** — Mermaid flow diagram of the full solution
3. **Step 1: Create the Business Event** — Avro schema definition
4. **Step 2: Publish from the source workload** — publisher code (Notebook, User Data Function, Eventstream, or Activator)
5. **Step 3: Configure the consumer** — Activator rule setup and/or KQL queries for Eventhouse
6. **Step 4: Test end to end** — how to verify the full flow
7. **What happens next** — extension ideas and related scenarios
