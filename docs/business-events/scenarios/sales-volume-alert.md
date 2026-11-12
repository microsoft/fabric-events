# Sales Volume Alert

## Business Context

Stores produce transaction totals throughout the trading day. Sales analysis compares each location's actual volume with its expected volume for the same hour, giving operations a view of which stores are performing normally and which deserve a closer look.

In the operating model described here, an operations lead reviews that comparison, selects a significant drop and prepares the store, trading hour and supporting figures for investigation. The report can be available while nobody has yet taken responsibility for investigating the exception. The business already knows how to measure the change; the delay is in turning that finding into work with enough context for someone to act.

```mermaid
flowchart TB
    S[("<b>Store sales records</b><br/>Store, hour and totals")]
    C["<b>Sales analysis</b><br/>Compares hourly sales"]
    R["<b>Performance report</b><br/>Expected vs. actual sales"]
    subgraph GAP["Gap: Sales exceptions still need<br/>to reach the operations team"]
        O["<b>Operations lead</b><br/>Selects stores that<br/>need investigation"]:::businessGap
        P["<b>Investigation preparation</b><br/>Captures store, hour<br/>and sales totals"]:::businessGap
    end
    I[("<b>Investigation worklist</b><br/>Store-specific findings")]
    S -->|"Sales totals"| C
    C -->|"Deviation"| R
    R -->|"Store results"| O
    O -->|"Select exception"| P
    P -->|"Record finding"| I
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

A significant drop can sit among routine observations until someone notices and passes it to operations. A notification without the store, expected volume and time window still leaves the recipient assembling the facts before an investigation can begin.

The team needs a consistent handoff from the sales comparison to operational attention, without treating every fluctuation as an incident or assuming what caused the drop.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Business Events

The sales comparison publishes each store finding as a Business Event. Operations applies its agreed response threshold and receives an investigation prompt for the qualifying stores, with the original comparison and time window.

```mermaid
flowchart TB
    S["<b>Sales analysis</b><br/>Compares hourly<br/>store performance"]
    E(["<b>Business Event</b><br/>Store sales finding"])
    subgraph VALUE["Improvement: Qualifying findings<br/>reach operations with context"]
        F["<b>Sales exception policy</b><br/>Selects deviations<br/>below -30%"]:::businessValue
        P["<b>Investigation prompt</b><br/>Store, hour and<br/>sales comparison"]:::businessValue
    end
    O["<b>Operations team</b><br/>Receives the<br/>investigation context"]
    S -->|"Share finding"| E
    E -->|"Apply response policy"| F
    F -->|"Qualifying store"| P
    P -->|"Bring finding to attention"| O
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

This changes the handoff from someone having to notice a report to a recognized condition starting the response. Operations can begin with the finding rather than request another extract. Another team can use the same finding without asking sales to build a different detector.

## Event-driven architecture {#architecture}

**Business signal:** `Retail.Sales.VolumeAlert`

**Publisher:** Notebook | **Consumer(s):** Activator (Teams)

```mermaid
flowchart TB
    S[("<b>Source Eventhouse</b><br/>Sales data in KQL tables")]
    N["<b>Notebook</b><br/>Compares hourly<br/>sales volumes"]
    B(["<b>Business Event</b><br/>Store sales finding"])
    A["<b>Activator (Teams)</b><br/>Selects deviations<br/>below -30%"]
    T["<b>Teams</b><br/>Notifies operations of<br/>the store and deviation"]

    S -->|"Read sales"| N
    N -->|"Publish"| B
    B -->|"Evaluate"| A
    A -->|"Rule matches"| T
    H[("<b>Eventhouse</b><br/>Sales finding history")]
    B -.->|"Analyze in Eventhouse"| H
```

1. The Notebook reads sales from the source Eventhouse and compares actual and expected hourly transactions for each store.
2. The store finding is published as a Business Event. Activator selects deviations strictly below -30%.
3. Teams delivers the selected store's context to operations, ready for investigation.

The source Eventhouse supplies sales inputs to the Notebook and is required. It is separate from the optional Eventhouse history branch.

Dashed arrows show optional event history and its analysis, separate from the required response. **Analyze in Eventhouse** is a creation-time choice, not a connection enabled by the deployment package or a prerequisite for completing the Workbook.

## Business outcome and boundaries

Give operations a store-specific Teams alert when transaction volume falls more than 30% below expectation. The Workbook compares six stores and produces two alerts ready for investigation.

**Where the exercise stops:** An alert does not explain the cause or show that sales recovered. The exact -30% boundary and less severe findings do not produce a Teams alert.

## Try the Workbook

The [Workbook](sales-volume-alert/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="configure-the-rule"></span>
<span id="consumer-1-notify-operations-with-activator-teams"></span>
<span id="consumer-2-analyze-recurring-drops-in-eventhouse"></span>
<span id="create-the-notebook"></span>
<span id="notebook-code"></span>
<span id="set-up-the-alert-rule"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-notebook"></span>
<span id="step-3-consumers-activator-teams-and-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
<span id="what-you-will-explore"></span>
