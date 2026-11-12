# Revenue Below Target

## Business Context

Finance and regional leaders monitor revenue against the target for each region and reporting period. A performance view shows the latest revenue, target and variance, while the underlying observations retain how that position has changed. The same region can stay below plan through several updates, recover and then fall behind again.

In this scenario, a finance analyst decides whether the latest observation warrants a new investigation, then assembles the figures for regional review. That decision requires more than the current percentage: the analyst must distinguish a continuing issue from a renewed decline and retain the values that prompted concern. Otherwise, each refresh can create more work for the same shortfall, or a later decline can be dismissed as something the team is already investigating.

```mermaid
flowchart TB
    S[("<b>Revenue records</b><br/>Region and period<br/>Revenue and target")]
    C["<b>Performance analysis</b><br/>Compares revenue<br/>against target"]
    V["<b>Performance view</b><br/>Regional variance"]
    subgraph GAP["Gap: A shortfall is not yet<br/>a distinct investigation"]
        A{"Does finance identify<br/>a new shortfall?"}:::businessGap
        P["<b>Investigation preparation</b><br/>Captures the<br/>triggering figures"]:::businessGap
    end
    I[("<b>Investigation worklist</b><br/>Region and period")]
    S -->|"Read figures"| C
    C -->|"Show variance"| V
    V -->|"Review change"| A
    A -->|"New issue"| P
    A -->|"Same issue"| V
    P -->|"Save context"| I
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

The current revenue position does not, by itself, distinguish a new reason to investigate from another observation of an existing shortfall. Opening work on every refresh creates noise; treating every later decline as the old issue can miss a renewed problem after recovery.

Finance also needs the figures that prompted the concern, not just whatever values happen to be displayed when someone begins the review.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Improved Business Response {#business-events}

A new crossing into a qualifying shortfall starts preparation of an investigation from the triggering region, period, revenue and target figures. Those facts are shared as a Business Event. Continued observations below the threshold do not create more investigations.

```mermaid
flowchart TB
    S["<b>Regional performance</b><br/>Compares revenue<br/>against target"]
    D{"Has revenue variance crossed<br/>below -15%?"}
    E(["<b>Business Event</b><br/>New revenue shortfall"])
    subgraph VALUE["Improvement: Each new shortfall<br/>keeps its own investigation context"]
        P["<b>Investigation preparation</b><br/>Uses the triggering<br/>revenue and target"]:::businessValue
        Q[("<b>Saved investigations</b><br/>Region, period and figures")]:::businessValue
    end
    R["<b>Finance and regional leaders</b><br/>Review the shortfall"]
    S -->|"Observe change"| D
    D -->|"New crossing"| E
    E -->|"Start investigation preparation"| P
    P -->|"Preserve triggering figures"| Q
    Q -->|"Make context available"| R
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

Finance and regional leadership get a common starting point for discussing the gap, while a renewed decline after recovery can receive fresh attention. The event-driven handoff separates the business decision to investigate from the changing display of current performance.

## Event-driven architecture {#architecture}

**Business signal:** `Sales.Revenue.BelowTarget`

**Publisher:** Activator (Business Event publication) | **Consumer(s):** Activator (Data Pipeline)

```mermaid
flowchart TB
    SRC[("<b>Source Eventhouse</b><br/>Revenue snapshots")]
    Q["<b>KQL query</b><br/>Calculates revenue variance"]
    DASH["<b>Real-Time Dashboard</b><br/>Shows current<br/>regional performance"]
    PUB{"Does <b>Activator</b> detect a crossing<br/>below -15%?"}
    BE(["<b>Business Event</b><br/>Revenue below target"])
    ACT["<b>Activator<br/>(Data Pipeline)</b><br/>Starts a response<br/>for each new shortfall"]
    PIPE["<b>Data Pipeline</b><br/>Prepares investigation<br/>in a Notebook"]
    EH[("<b>Eventhouse</b><br/>Published shortfall history")]
    OUT[("<b>Lakehouse Files</b><br/>RevenueInvestigations")]

    SRC -->|"Read snapshots"| Q
    Q -->|"Monitor observations"| PUB
    Q -->|"Visualize current state"| DASH
    PUB -->|"Publish crossing"| BE
    BE -->|"Start response"| ACT
    ACT -->|"Pass six event fields"| PIPE
    BE -.->|"Analyze in Eventhouse"| EH
    PIPE -->|"Persist with execution ID"| OUT
```

1. The revenue query reads source observations from Eventhouse. The Dashboard shows current regional performance while the publishing Activator monitors the same business condition.
2. A crossing strictly below -15% publishes a Business Event. Continued shortfall observations do not publish another crossing; recovery allows a later crossing to qualify.
3. The consuming Activator starts the Data Pipeline with the triggering figures. The pipeline prepares and saves the investigation in Lakehouse Files without reading event history.

The source Eventhouse is required for revenue observations. The Dashboard shows current performance; optional event history is a different branch and never supplies the pipeline's inputs.

## Business outcome and boundaries

Prepare a traceable investigation dataset in Lakehouse Files for each new crossing, with status `ready_for_review`. The Workbook follows a shortfall, recovery and second crossing, producing two investigations.

**Where the exercise stops:** The exact -15% boundary, continued observations below the threshold and recovery do not start another investigation. Zero or negative targets do not qualify. A saved analysis is not a reviewed recovery plan or proof that revenue improved.

## Try the Workbook

The [Workbook](revenue-below-target/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="configure-the-publisher"></span>
<span id="consumer-1-prepare-investigation-analysis"></span>
<span id="optional-consumer-analyze-shortfalls-in-eventhouse"></span>
<span id="prepare-the-source-and-dashboard"></span>
<span id="shared-resource-names"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-activator-on-a-kql-query"></span>
<span id="step-3-consumers-activator-data-pipeline-and-optional-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
