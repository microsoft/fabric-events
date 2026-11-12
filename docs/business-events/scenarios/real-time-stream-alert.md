# Real-Time Stream Alert

## Business Context

Retail payments arrive continuously, and the payments system retains the transaction identity, amount, currency, store and processing time. Payment operations applies a review policy to identify transactions that deserve closer assessment. The reviewer works from a case, not from the full transaction feed.

The current process in this scenario requires the selected payment to be copied into a review register before that assessment can begin. The person preparing the case must keep the original amount and currency together; a large number in one currency is not a comparable risk score across all currencies. Identifying a qualifying payment and making it available to a reviewer are therefore two distinct responsibilities, and the second can remain unfinished even when the first has succeeded.

```mermaid
flowchart TB
    P["<b>Payments system</b><br/>Records incoming payments"]
    L[("<b>Transaction records</b><br/>Amount and currency<br/>Store and time")]
    subgraph GAP["Gap: Selected payments still need<br/>a recorded review case"]
        O["<b>Payment operations</b><br/>Selects payments<br/>for review"]:::businessGap
        C["<b>Case preparation</b><br/>Captures original<br/>transaction facts"]:::businessGap
    end
    Q[("<b>Review cases</b><br/>Pending assessment")]
    R["<b>Payment reviewer</b><br/>Assesses the transaction"]
    P -->|"Record"| L
    L -->|"Review activity"| O
    O -->|"Selected payments"| C
    C -->|"Register case"| Q
    Q -->|"Assess case"| R
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

The payment activity can contain a qualifying transaction without a case appearing in the review queue. Asking reviewers to search the wider activity and copy payment details leaves a gap between identifying a review condition and organizing the work.

A separate process that interprets the amount condition again can also select a different population. Review needs a consistent finding with its original currency, not an assumption that a large payment is fraudulent.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Improved Business Response {#business-events}

A qualifying payment starts case preparation with the same transaction, amount and currency that caused it to qualify. The payment flow selects transactions above the sample review threshold and publishes each qualifying finding as a Business Event.

```mermaid
flowchart TB
    S["<b>Payment review policy</b><br/>Selects amounts<br/>above 5000"]
    E(["<b>Business Event</b><br/>Selected payment facts"])
    subgraph VALUE["Improvement: Selected payments<br/>arrive as reviewable cases"]
        P["<b>Case preparation</b><br/>Preserves the transaction,<br/>amount and currency"]:::businessValue
        Q[("<b>Review queue</b><br/>Cases pending assessment")]:::businessValue
    end
    R["<b>Payment reviewer</b><br/>Assesses the transaction"]
    S -->|"Share selected payment"| E
    E -->|"Start case preparation"| P
    P -->|"Record original facts"| Q
    Q -->|"Make case available"| R
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

Reviewers get work they can pick up instead of having to assemble a queue from payment activity. Payment operations retains its selection policy, and the review team can change case preparation without taking over detection. The response registers a review need; it does not block the payment.

## Event-driven architecture {#architecture}

**Business signal:** `Retail.Payment.HighValueTransaction`

**Publisher:** Eventstream | **Consumer(s):** Activator (User Data Function)

```mermaid
flowchart TB
    PS["<b>Payments system</b><br/>Streams transactions"]
    ES["<b>Eventstream</b><br/>Selects amounts<br/>above 5000"]
    BE(["<b>Business Event</b><br/>High-value payment"])
    ACT["<b>Activator<br/>(User Data Function)</b><br/>Starts a response<br/>for each finding"]
    UDF["<b>User Data Function</b><br/>Registers a<br/>payment review case"]
    CASE[("<b>Lakehouse Files</b><br/>pending_review")]
    EH[("<b>Eventhouse</b><br/>Payment event history")]
    KQL["<b>KQL analysis</b><br/>Explores payment patterns"]

    PS --> ES
    ES -->|"Filter and map"| BE
    BE -->|"Register case"| ACT
    ACT -->|"Pass event fields"| UDF
    UDF -->|"Persist"| CASE
    BE -.->|"Analyze in Eventhouse"| EH
    EH -.->|"Explore history"| KQL
```

1. Eventstream receives the payment activity and selects amounts strictly above 5000 in each payment's local currency units.
2. The matching transaction is mapped to a Business Event. Activator starts the review response for each received finding.
3. The response User Data Function saves a payment review case in Lakehouse Files, preserving the original transaction, amount and currency.

Eventstream owns the filter. Review cases are JSON records in Lakehouse Files.

Dashed arrows show optional event history and its analysis, separate from the required response. **Analyze in Eventhouse** is a creation-time choice, not a connection enabled by the deployment package or a prerequisite for completing the Workbook.

## Business outcome and boundaries

Create three saved payment review cases with status `pending_review` from six payments. Preserve the transaction, amount and currency so reviewers know which finding needs attention.

**Where the exercise stops:** Exactly 5000 and lower amounts create no Business Event or review case. The threshold uses each payment's local currency units, with no conversion; it is a sample policy, not a fraud verdict, payment block or cross-currency risk comparison.

## Try the Workbook

The [Workbook](real-time-stream-alert/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="add-a-filter-transformation"></span>
<span id="add-the-business-events-destination"></span>
<span id="configure-the-rule"></span>
<span id="consumer-1-register-a-payment-review-case-with-activator-user-data-function"></span>
<span id="consumer-2-analyze-high-value-activity-in-eventhouse"></span>
<span id="create-the-eventstream"></span>
<span id="map-the-schema-fields"></span>
<span id="prepare-the-target-function"></span>
<span id="set-up-the-alert-rule"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-eventstream"></span>
<span id="step-3-consumers-activator-user-data-function-and-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
