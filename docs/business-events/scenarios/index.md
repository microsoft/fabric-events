# Scenarios

Choose the business outcome you want to explore: bring a finding to someone's attention, prepare work for a team, or understand recurring activity. Open the scenario for its business context and architecture, then use its Workbook for preparation, connections and one complete exercise. Some responses require connections to your own business systems.

## How to read a scenario

Each scenario has two pages with one shared outcome:

1. **Business context:** how the activity works today, the current problem, what changes with Business Events, and the event-driven architecture with its flows explained. The page also states what the exercise demonstrates and where it stops.
2. **Workbook:** prerequisites, resource coverage, supported deployment or manual preparation, required connections, one source sample and visible results, including cases that should be ignored.

Schemas, mappings, KQL, SQL and source examples are retained in the Workbook, next to the relevant step or in expandable implementation reference. Optional extensions are separate from completion criteria.

**Level** reflects the Workbook's manual setup and result-checking effort:

- **Beginner:** few setup steps and straightforward result checks.
- **Intermediate:** some manual setup or guided result checks.
- **Advanced:** substantial manual setup or coordinated, multi-step checks.

Scenarios are ordered by level within each pattern, with advanced exercises last.

## Explore by outcome

### Track & Analyze

Use this pattern when the primary outcome is understanding received business activity rather than starting an operational response. Order History Analysis compares synthetic orders across two stores.

| Scenario | Industry | Publisher | Consumer(s) | Level | Workbook |
|----------|----------|-----------|-------------|-------|------------------|
| [Order History Analysis](order-history-analysis.md) | Retail | Notebook | Eventhouse | Beginner | [Workbook](order-history-analysis/deployment.md) |

With **Analyze in Eventhouse** enabled during Business Event creation, received events are automatically persisted in a dedicated KQL table. This history consumer is **required** for Order History Analysis, whose Workbook guides native setup and one result query; it does not claim automated provisioning. History remains an optional extension in the operational scenarios, and their deployment packages do not configure it. A source Eventhouse is not the same as an event-history destination.

!!! tip "Good first scenario"
    Start with [Order History Analysis](order-history-analysis.md) for a Beginner publish-and-query exercise. Choose [Sales Volume Alert](sales-volume-alert.md) when you are ready to connect an operational response in Teams.

!!! tip "Combine patterns"
    Add received-event analysis when it answers a business question, such as recurring sales drops or repeated stock shortages. Plan the history choice when creating the event, and check delivered messages, saved requests or cases separately. A history row does not establish that an operational action completed.

### Detect & Alert

Use this pattern when a finding needs a person's attention. Sales Volume Alert notifies store operations in Teams. Revenue Below Target prepares the figures a revenue team needs for commercial review. Eventhouse can separately retain the findings to help teams identify recurring patterns.

| Scenario | Industry | Publisher | Consumer(s) | Level | Workbook |
|----------|----------|-----------|-------------|-------|------------------|
| [Sales Volume Alert](sales-volume-alert.md) | Retail | Notebook | Activator (Teams), optional Eventhouse | Intermediate | [Workbook](sales-volume-alert/deployment.md) |
| [Revenue Below Target](revenue-below-target.md) | Sales | Activator (Business Event publication) | Activator (Data Pipeline), optional Eventhouse | Advanced | [Workbook](revenue-below-target/deployment.md) |

### Decide & Act

Use this pattern to turn a finding or decision into work. Prepare a replenishment request, register a payment review case, or apply an order hold. Each team owns its response to the shared signal. The scenario makes clear where the work stops: a request still needs approval, and a review case still needs assessment. Add another response without changing the publisher.

| Scenario | Industry | Publisher | Consumer(s) | Level | Workbook |
|----------|----------|-----------|-------------|-------|------------------|
| [Low Stock Threshold](low-stock-threshold.md) | Supply Chain | User Data Function | Activator (Data Pipeline), optional Eventhouse | Intermediate | [Workbook](low-stock-threshold/deployment.md) |
| [Real-Time Stream Alert](real-time-stream-alert.md) | Finance | Eventstream | Activator (User Data Function), optional Eventhouse | Intermediate | [Workbook](real-time-stream-alert/deployment.md) |
| [Discount Approval Evaluation and Event Publication](discount-approval-routing.md) | Sales | User Data Function | Activator (Teams), Eventstream, optional Eventhouse | Intermediate | [Workbook](discount-approval-routing/deployment.md) |
| [Real-Time Viewer Recommendations](real-time-viewer-recommendations.md) | Media | User Data Function | Activator (User Data Function), optional Eventhouse | Intermediate | [Workbook](real-time-viewer-recommendations/deployment.md) |
| [Data Pipeline Failure Follow-up](data-pipeline-failure-follow-up.md) | DataOps | Notebook | Activator (User Data Function), optional Eventhouse | Intermediate | [Workbook](data-pipeline-failure-follow-up/deployment.md) |
| [Customer Loyalty Milestone](customer-loyalty-milestone.md) | Retail | Activator | Eventstream (campaign enrichment → Lakehouse Delta tables) | Advanced | [Workbook](customer-loyalty-milestone/deployment.md) |
| [Abandoned Cart Recovery](abandoned-cart-recovery.md) | Ecommerce | User Data Function | Activator (Power Automate: email and SQL follow-up), optional Eventhouse | Advanced | [Workbook](abandoned-cart-recovery/deployment.md) |
| [Fraud Response Before Fulfillment](fraud-response-before-fulfillment.md) | Finance | User Data Function | Activator (Power Automate), optional Eventhouse | Advanced | [Workbook](fraud-response-before-fulfillment/deployment.md) |

!!! note "The fan-out advantage"
    **Fraud Response Before Fulfillment** uses one event to support three separate responses. Teams can evolve their workflows without changing the publisher, while still agreeing how competing or overlapping actions should be handled.
