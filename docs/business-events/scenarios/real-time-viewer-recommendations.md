# Real-Time Viewer Recommendations

## Business Context

A viewing application records watches, likes, clicks, searches and skips with the viewer, content and session context. A separate recommendation process uses a content catalog to prepare suggestions. The product team must decide which interactions express enough interest to justify refreshing those suggestions, rather than treating every action as equally meaningful.

In this operating model, application-specific logic connects recorded interactions to requests for a new list. It must choose the relevant action and carry the right viewer and session context into recommendation preparation. Collecting more interaction history does not, on its own, make a refreshed list available. The business needs a consistent response to meaningful viewing behavior without turning every application interaction into another place to maintain recommendation rules.

```mermaid
flowchart TB
    A["<b>Viewing app</b><br/>Records viewer actions"]
    L[("<b>Interaction records</b><br/>Viewer, content and session")]
    subgraph GAP["Gap: Meaningful actions need<br/>a separate refresh request"]
        I["<b>Application logic</b><br/>Selects actions that<br/>need a refresh"]:::businessGap
        P["<b>Recommendation process</b><br/>Ranks candidate content"]:::businessGap
    end
    C[("<b>Content catalog</b><br/>Tags and popularity")]
    R[("<b>Recommendation lists</b><br/>Session and action")]
    A -->|"Record action"| L
    L -->|"Action context"| I
    I -->|"Request refresh"| P
    C -->|"Candidate content"| P
    P -->|"Save list"| R
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

An interaction record does not, by itself, prepare a recommendation list. Treating every click or search as a reason to refresh also fails to distinguish stronger expressions of interest, such as watching or liking a title.

The team needs selected actions to initiate recommendation work with enough context to explain which interaction produced each list, without making the viewing application own the ranking process.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Improved Business Response {#business-events}

The recommendation response selects watch and like actions, prepares suggestions from the sample catalog and saves the resulting list with its triggering interaction and session. The application shares each interaction as a Business Event.

```mermaid
flowchart TB
    A["<b>Viewing app</b><br/>Records viewer interactions"]
    E(["<b>Business Event</b><br/>Viewer interaction"])
    subgraph VALUE["Improvement: Selected actions lead<br/>to saved recommendation lists"]
        F["<b>Refresh policy</b><br/>Selects watch<br/>and like actions"]:::businessValue
        P["<b>Recommendation preparation</b><br/>Ranks catalog candidates"]:::businessValue
        R[("<b>Saved recommendations</b><br/>Interaction and session context")]:::businessValue
    end
    C[("<b>Content catalog</b><br/>Tags and popularity")]
    A -->|"Share interaction"| E
    E -->|"Apply refresh policy"| F
    F -->|"Selected action"| P
    C -->|"Candidate content"| P
    P -->|"Save a contextual list"| R
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

Meaningful actions now lead to a usable next set of suggestions rather than remaining records alone. The product team can change which actions deserve a refresh without embedding recommendation processing in every interaction. Showing those lists and evaluating their effect on discovery or engagement remain separate product responsibilities.

## Event-driven architecture {#architecture}

**Business signal:** `Streaming.Viewer.BehaviorDetected`

**Publisher:** User Data Function | **Consumer(s):** Activator (User Data Function)

```mermaid
flowchart TB
    APP["<b>Viewing app</b><br/>Records watches, likes,<br/>clicks, searches and skips"]
    PUB["<b>Publisher UDF</b><br/>Shares interaction facts"]
    BE(["<b>Business Event</b><br/>Viewer interaction"])
    ACT["<b>Activator<br/>(User Data Function)</b><br/>Selects watch<br/>and like actions"]
    UDF["<b>Response UDF</b><br/>Ranks three<br/>recommendations"]
    OUT[("<b>Lakehouse Files</b><br/>Recommendation snapshots")]
    EH[("<b>Eventhouse</b><br/>Interaction history")]
    APP -->|"Action details"| PUB
    PUB -->|"Publish"| BE
    BE -->|"Subscribe"| ACT
    ACT -->|"Map"| UDF
    UDF -->|"Persist"| OUT
    BE -.->|"Analyze in Eventhouse"| EH
```

1. The publishing User Data Function shares the original interaction details as a Business Event, including actions that will not require a recommendation refresh.
2. Activator selects watch and like actions. Click, search and skip do not start the response.
3. The response User Data Function ranks the sample catalog by tag overlap and popularity, then saves a recommendation snapshot in Lakehouse Files with the interaction and session context.

The Workbook checks the saved lists and preserves the session context, including earlier or out-of-order interactions.

Dashed arrows show optional event history and its analysis, separate from the required response. **Analyze in Eventhouse** is a creation-time choice, not a connection enabled by the deployment package or a prerequisite for completing the Workbook.

## Business outcome and boundaries

Turn six viewer interactions into three persisted recommendation snapshots with status `ready_for_serving`. A small synthetic catalog ranks suggestions by tag overlap and then popularity.

**Where the exercise stops:** Click, search and skip create no response execution or recommendation file. This is a transparent sample without a trained model or profile inference. A saved snapshot does not prove that an application displayed it.

## Try the Workbook

The [Workbook](real-time-viewer-recommendations/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="connect-and-start-the-native-rule"></span>
<span id="consumer-1-prepare-recommendations-with-activator-user-data-function"></span>
<span id="consumer-2-analyze-received-interactions-in-eventhouse"></span>
<span id="prepare-the-catalog-and-response-function"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
