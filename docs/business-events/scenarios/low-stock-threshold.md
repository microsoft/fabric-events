# Low Stock Threshold

## Business Context

The warehouse management system tracks available stock and identifies items below their replenishment threshold. Its shortage record already contains the item, warehouse, observed stock, threshold and supplier context. Warehouse operations owns that finding; the replenishment team owns deciding what purchasing response should follow.

Here, a replenishment coordinator takes the shortage details from the warehouse system and prepares a request for the purchasing team. The reviewer needs the original stock position and observation time to understand why the request exists. Until that request is recorded, a shortage can be visible to the warehouse without being present in the approval queue. Copying the facts does not replenish stock, but it determines whether the purchasing decision is ready to be made.

```mermaid
flowchart TB
    S[("<b>Inventory records</b><br/>Item, stock and threshold")]
    W["<b>Warehouse system</b><br/>Detects low stock"]
    subgraph GAP["Gap: Shortages are not yet<br/>in the approval queue"]
        C["<b>Replenishment team</b><br/>Transfers shortage facts"]:::businessGap
        P["<b>Request preparation</b><br/>Captures item, warehouse,<br/>supplier and time"]:::businessGap
    end
    Q[("<b>Request register</b><br/>Pending approval")]
    R["<b>Purchasing team</b><br/>Reviews replenishment<br/>requests"]
    S -->|"Monitor stock"| W
    W -->|"Shortage details"| C
    C -->|"Copy facts"| P
    P -->|"Save request"| Q
    Q -->|"Review work"| R
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

A shortage can be visible in the warehouse system without a corresponding request being ready for replenishment to review. When the details must be transferred separately, the team first reconstructs the finding instead of assessing the response.

Repeating the stock check in the request process also creates two places to interpret the shortage policy. The missing step is preparing reviewable work from a finding that the warehouse has already made.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Improved Business Response {#business-events}

Each low-stock finding starts preparation of a saved replenishment request using the original item, location and stock context. The request is available for approval rather than remaining a shortage observation in another system. The warehouse finding is shared as a low-stock Business Event.

```mermaid
flowchart TB
    W["<b>Warehouse system</b><br/>Detects low stock"]
    E(["<b>Business Event</b><br/>Low-stock finding"])
    subgraph VALUE["Improvement: Shortages become<br/>requests ready for approval review"]
        P["<b>Request preparation</b><br/>Uses the original<br/>shortage facts"]:::businessValue
        Q[("<b>Replenishment requests</b><br/>Pending approval")]:::businessValue
    end
    R["<b>Purchasing team</b><br/>Reviews the<br/>prepared request"]
    W -->|"Share shortage"| E
    E -->|"Start request preparation"| P
    P -->|"Save original context"| Q
    Q -->|"Make reviewable work available"| R
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

Replenishment can focus on the next purchasing decision instead of re-entering the details. The warehouse keeps its established detection policy, while the team can change how it prepares requests without introducing another stock test.

## Event-driven architecture {#architecture}

**Business signal:** `Retail.Inventory.LowStockThreshold`

**Publisher:** User Data Function | **Consumer(s):** Activator (Data Pipeline)

```mermaid
flowchart TB
    WMS["<b>External WMS</b><br/>Detects low stock"]
    UDF["<b>User Data Function</b><br/>Normalizes the<br/>shortage finding"]
    BE(["<b>Business Event</b><br/>Low-stock finding"])
    EH[("<b>Eventhouse</b><br/>Shortage history")]
    ACT["<b>Activator<br/>(Data Pipeline)</b><br/>Starts a response<br/>for each finding"]
    PIPELINE["<b>Data Pipeline</b><br/>Prepares a<br/>replenishment request"]
    REQUEST[("<b>Lakehouse Files</b><br/>pending_approval")]

    WMS -->|"Send finding"| UDF
    UDF -->|"Publish"| BE
    BE -.->|"Analyze in Eventhouse"| EH
    BE -->|"Prepare request"| ACT
    ACT -->|"Pass event fields"| PIPELINE
    PIPELINE -->|"Persist"| REQUEST
```

1. The external warehouse system detects low stock. The publishing User Data Function normalizes and shares that finding without detecting the shortage again.
2. Activator receives the Business Event and starts the Data Pipeline with the original stock facts.
3. The pipeline saves a replenishment request in Lakehouse Files for approval review.

The request is saved as JSON in Lakehouse Files, not as a Delta table.

Dashed arrows show optional event history and its analysis, separate from the required response. **Analyze in Eventhouse** is a creation-time choice, not a connection enabled by the deployment package or a prerequisite for completing the Workbook.

## Business outcome and boundaries

Prepare three saved replenishment requests with status `pending_approval`. Each request preserves the item, warehouse, stock level, threshold and observation time supplied by the source.

**Where the exercise stops:** This exercise does not approve purchases, calculate an order quantity or replenish stock. The threshold is context for review, not a recommended purchase quantity; neither the publishing function nor the response runs a second stock test.

## Try the Workbook

The [Workbook](low-stock-threshold/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="connect-the-event-to-the-pipeline"></span>
<span id="connect-to-the-schema-set"></span>
<span id="consumer-1-prepare-a-replenishment-request-with-activator-data-pipeline"></span>
<span id="consumer-2-analyze-shortages-in-eventhouse"></span>
<span id="create-the-user-data-function"></span>
<span id="function-code"></span>
<span id="prepare-the-target-pipeline"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers-activator-data-pipeline-and-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
