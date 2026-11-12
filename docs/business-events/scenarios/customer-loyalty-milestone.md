# Customer Loyalty Milestone

## Business Context

The loyalty program maintains customer-wide spending and tier information across stores. Marketing works from a separate campaign catalog that identifies which campaigns are available for each tier and store, whether they are active and which benefits they offer. A customer's progress through the program and the availability of a local campaign are different business facts.

For the current process in this scenario, loyalty operations identifies a new Gold milestone and marketing prepares the campaign candidates. A planner must match the customer's tier and store to the catalog, exclude inactive campaigns and keep missing matches available for review. This is more than exporting customers above a spending threshold: a spending list alone does not tell marketing which campaign it can prepare, and an unresolved catalog entry must not look like a customer ready for outreach.

```mermaid
flowchart TB
    S[("<b>Customer balances</b><br/>Spend, tier and store")]
    L["<b>Loyalty operations</b><br/>Identifies new<br/>Gold milestones"]
    subgraph GAP["Gap: A milestone alone does not<br/>identify a usable campaign"]
        M["<b>Marketing planner</b><br/>Matches tier and store<br/>to the campaign catalog"]:::businessGap
        D{"What is the campaign<br/>status?"}:::businessGap
    end
    C[("<b>Campaign catalog</b><br/>Status and benefit codes")]
    P[("<b>Campaign candidates</b><br/>Customer and campaign<br/>Benefit details")]
    R[("<b>Campaign setup review</b><br/>Missing catalog match")]
    N["<b>Campaign exclusion</b><br/>Inactive match;<br/>no candidate"]
    S -->|"Review milestones"| L
    L -->|"Milestone context"| M
    C -->|"Campaign details"| M
    M -->|"Check match"| D
    D -->|"Active"| P
    D -->|"Missing"| R
    D -->|"Inactive"| N
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

A customer passing the Gold spending threshold does not identify a usable campaign. The relevant campaign may be active, inactive or missing from the catalog. A list based on spending alone leaves marketing checking those distinctions or preparing outreach for an unavailable campaign.

Missing campaign mappings also need a visible review path rather than disappearing into the same list. Continued spending above the threshold should not be mistaken for a new milestone.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Business Events

A qualifying crossing above 5000 USD is shared as a customer-milestone Business Event. The campaign response combines the milestone's tier and store with campaign and benefit context, producing candidates for active campaigns and a separate review list for missing mappings. Inactive matches produce no candidate.

```mermaid
flowchart TB
    L["<b>Loyalty program</b><br/>Recognizes Gold crossings<br/>above 5000 USD"]
    E(["<b>Business Event</b><br/>Customer milestone"])
    C[("<b>Campaign catalog</b><br/>Active status and benefits")]
    subgraph VALUE["Improvement: Milestones lead to<br/>campaign preparation or mapping review"]
        M["<b>Campaign matching</b><br/>Matches tier and store<br/>to the catalog"]:::businessValue
        D{"What is the campaign<br/>match status?"}:::businessValue
        P[("<b>Campaign candidates</b><br/>Active campaign and benefits")]:::businessValue
        R[("<b>Mapping review</b><br/>Missing campaign mapping")]:::businessValue
    end
    N["<b>Campaign exclusion</b><br/>Inactive match;<br/>no candidate"]
    L -->|"Share new milestone"| E
    E -->|"Start campaign preparation"| M
    C -->|"Available campaigns"| M
    M -->|"Classify match"| D
    D -->|"Active"| P
    D -->|"Missing"| R
    D -->|"Inactive"| N
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

Marketing gets a specific next step for the milestone instead of an undifferentiated spending list. Campaign availability can change without redefining how the loyalty program recognizes progress. The output supports campaign preparation and exception review; it does not issue a reward or authorize customer contact.

## Event-driven architecture {#architecture}

**Business signal:** `Retail.Customer.MilestoneReached`

**Publisher:** Activator | **Consumer(s):** Eventstream (campaign enrichment and Lakehouse Delta tables)

```mermaid
flowchart TB
    SRC[("<b>Source Eventhouse</b><br/>Customer-wide spend")]
    PUB["<b>Activator</b><br/>Detects spending crossings<br/>above 5000 USD"]
    BE(["<b>Business Event</b><br/>Retail.Customer.MilestoneReached"])
    ES["<b>Eventstream</b><br/>Joins tier and store<br/>to the campaign catalog"]
    REF[("<b>Lakehouse reference table</b><br/>Campaigns and<br/>benefit codes")]
    ACTIVE["<b>Candidate selection</b><br/>Selects fields for<br/>active campaigns"]
    MISSING["<b>Mapping review selection</b><br/>Selects fields for<br/>missing campaign matches"]
    OUT[("<b>Lakehouse Delta table</b><br/>LoyaltyCampaignCandidates")]
    REVIEW[("<b>Lakehouse Delta table</b><br/>LoyaltyCampaignReview")]
    SRC --> PUB --> BE --> ES
    REF --> ES
    ES --> ACTIVE --> OUT
    ES --> MISSING --> REVIEW
```

1. Activator monitors supplied customer-wide balances and publishes the milestone when spending crosses above 5000 USD.
2. Eventstream joins the milestone's tier and store to the Lakehouse campaign catalog, adding the matching campaign and benefit details.
3. An active match produces a candidate row; no campaign match produces a mapping-review row. Inactive matches produce neither.
4. The two Lakehouse Delta tables make campaign preparation and missing-mapping review available separately to marketing.

The source Eventhouse holds supplied balances; this exercise does not calculate lifetime spend. No consumer Activator, CRM change, issued reward or delivered communication is part of the outcome.

## Business outcome and boundaries

Prepare one distinct candidate in `LoyaltyCampaignCandidates` and one unmatched customer in `LoyaltyCampaignReview`. Eventstream joins the tier and store to a reference catalog, excludes inactive campaigns and saves useful fields as Lakehouse Delta tables.

**Where the exercise stops:** Exactly 5000 and 4999 do not qualify; continued spending from 5100 to 5200 is not a second crossing. An inactive campaign produces no output. These append tables are not an exactly-once audience or a reward ledger; check contact permission and benefit eligibility before outreach.

## Try the Workbook

The [Workbook](customer-loyalty-milestone/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="configure-the-milestone-rule"></span>
<span id="confirm-that-continued-spending-is-not-another-crossing"></span>
<span id="connect-and-shape-the-campaign-flow"></span>
<span id="establish-the-starting-balances"></span>
<span id="evaluation-criteria"></span>
<span id="introduce-the-new-balances"></span>
<span id="prepare-the-campaign-catalog"></span>
<span id="prepare-the-spending-view"></span>
<span id="step-1-define-the-loyalty-milestone"></span>
<span id="step-2-recognize-the-customer-milestone"></span>
<span id="step-3-prepare-campaign-candidates-in-eventstream"></span>
<span id="step-4-observe-which-customers-need-campaign-preparation"></span>
<span id="why-this-matters"></span>
