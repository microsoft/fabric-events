# Scenarios

Each scenario is a complete, end-to-end walkthrough of a real business problem solved with Business Events. Every scenario includes:

- A business context explaining the problem
- A flow diagram showing the architecture
- The event schema definition
- Code snippets for the publisher
- Configuration for the consumer

## Available scenarios

| # | Scenario | Publisher | Consumer | Status |
|---|----------|-----------|----------|--------|
| 1 | [Sales Volume Alert](scenario-01-sales-volume-alert/README.md) | Notebook | Activator | ✅ Available |
| 2 | [Low Stock Threshold](scenario-02-low-stock-threshold/README.md) | User Data Function | Eventhouse | ✅ Available |
| 3 | [Real-Time Stream Alert](scenario-03-high-value-transaction/README.md) | Eventstream | Activator | ✅ Available |
| 4 | [Data Lineage Audit](scenario-04-pipeline-audit/README.md) | Notebook | Eventhouse | ✅ Available |
| 5 | [Business Automation Loop](scenario-05-loyalty-milestone/README.md) | Activator | Activator | ✅ Available |

## How to read a scenario

Each scenario follows the same structure:

1. **Business context**: the real-world problem and why it matters
2. **Architecture**: flow diagram of the solution
3. **Event schema**: the Business Event definition
4. **Publisher**: how to publish the event from the source workload
5. **Consumer**: how to consume and react to the event
