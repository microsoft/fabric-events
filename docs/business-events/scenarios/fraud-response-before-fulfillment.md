# Fraud Response Before Fulfillment

## Business Context

An online retailer receives orders and scores from an existing risk-scoring process. Its policy uses the supplied score and order context to determine whether an order can be released, needs human review or should be held. The warehouse, however, follows the fulfillment permission saved in the order system rather than the separate risk assessment.

In the current process described here, order operations must take the recorded policy decision and apply it to the order before the warehouse can rely on it. Risk review and fulfillment can therefore be looking at different records of the same order. A warning that has reached a person is not equivalent to a hold that the warehouse enforces, and requesting a review does not mean that a reviewer has cleared the order for release.

```mermaid
flowchart TB
    S["<b>Risk input</b><br/>Order and supplied score"]
    P["<b>Risk policy</b><br/>Selects hold, review<br/>or release"]
    R[("<b>Risk assessment</b><br/>Score and decision")]
    subgraph GAP["Gap: Risk decisions are not yet<br/>enforced by the order system"]
        O["<b>Order operations</b><br/>Applies the risk decision"]:::businessGap
        OMS[("<b>Order system</b><br/>Order state and<br/>fulfillment permission")]:::businessGap
    end
    W["<b>Warehouse</b><br/>Checks fulfillment<br/>permission"]
    S -->|"Evaluate"| P
    P -->|"Record"| R
    R -->|"Operations action"| O
    O -->|"Save state"| OMS
    OMS -->|"Check permission"| W
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

A risk decision recorded separately from order operations may leave fulfillment permission unchanged. Sending a warning does not prevent an order from proceeding, and a request for human review must not be mistaken for a completed review.

The missing connection is from the policy decision to an actual order-state change, with distinct handling for hold, review and release.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Business Events

The risk-policy result is published as a Business Event for the order. The relevant connected response applies a hold, requests review or releases the order. The order system saves that result and enforces the corresponding fulfillment permission.

```mermaid
flowchart TB
    P["<b>Risk policy</b><br/>Uses the supplied score<br/>and order context"]
    E(["<b>Business Event</b><br/>Order risk decision"])
    subgraph VALUE["Improvement: Risk decisions reach<br/>the system that controls fulfillment"]
        H["<b>Hold response</b><br/>Requests an order hold"]:::businessValue
        V["<b>Review response</b><br/>Requests human review"]:::businessValue
        L["<b>Release response</b><br/>Requests order release"]:::businessValue
        O["<b>Order system</b><br/>Saves the state and enforces<br/>fulfillment permission"]:::businessValue
    end
    P -->|"Share policy result"| E
    E -->|"Hold"| H
    E -->|"Review"| V
    E -->|"Release"| L
    H -->|"Block fulfillment"| O
    V -->|"Keep blocked pending review"| O
    L -->|"Permit fulfillment"| O
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

This makes the policy useful at the point where operational behavior changes: released orders can proceed, while held and review-requested orders remain blocked. Business Events carries the decision to that response; it does not replace the connected order system or perform the human review.

## Event-driven architecture {#architecture}

**Business signal:** `Ecommerce.Order.FraudDecision`

**Publisher:** User Data Function | **Consumer(s):** Activator (Power Automate: hold, review and release)

```mermaid
flowchart TB
    SOURCE["<b>Risk input</b><br/>Order and supplied score"]
    UDF["<b>Publisher UDF</b><br/>Applies the risk policy"]
    BE(["<b>Business Event</b><br/>FraudDecision"])
    ACT["<b>Activator<br/>(Power Automate)</b><br/>Selects the response<br/>for each decision"]
    HOLD["<b>Power Automate</b><br/>Requests an order hold"]
    REVIEW["<b>Power Automate</b><br/>Requests human review"]
    RELEASE["<b>Power Automate</b><br/>Requests order release"]
    OMS["<b>Test OMS</b><br/>Saves order state and<br/>enforces fulfillment permission"]
    EH[("<b>Eventhouse</b><br/>Decision history")]

    SOURCE -->|"Inputs"| UDF
    UDF -->|"Decision"| BE
    BE -->|"Subscribe"| ACT
    ACT -->|"hold"| HOLD
    ACT -->|"manual_review"| REVIEW
    ACT -->|"release"| RELEASE
    HOLD --> OMS
    REVIEW --> OMS
    RELEASE --> OMS
    BE -.->|"Analyze in Eventhouse"| EH
```

1. The publishing User Data Function applies the risk policy to the supplied score and order context, then publishes the decision as a Business Event.
2. Activator selects the decision's hold, review or release response and invokes the corresponding Power Automate flow.
3. The connected test order system persists the requested state and enforces fulfillment permission. A delivered event or flow invocation is not a substitute for this order-system result.

The Workbook checks the saved order states and fulfillment behavior separately from event delivery.

Dashed arrows show optional event history and its analysis, separate from the required response. **Analyze in Eventhouse** is a creation-time choice, not a connection enabled by the deployment package or a prerequisite for completing the Workbook.

## Business outcome and boundaries

Apply six supplied scores to six synthetic orders: two releases, two review requests and two holds. The connected test order system permits fulfillment only for released orders and keeps held or review-requested orders blocked.

**Where the exercise stops:** A supplied score is not proof of fraud. `review_requested` is not `review_completed`. You must supply an authenticated, isolated test order system and connect the three real responses; the package does not deploy an order backend.

## Try the Workbook

The [Workbook](fraud-response-before-fulfillment/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="before-you-start"></span>
<span id="consumer-1-activator-power-automate"></span>
<span id="consumer-2-independent-decision-history"></span>
<span id="evaluation-criteria"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-create-the-policy-publisher"></span>
<span id="step-3-connect-the-operational-responses"></span>
<span id="step-4-test-from-the-original-source"></span>
<span id="what-happens-next"></span>
