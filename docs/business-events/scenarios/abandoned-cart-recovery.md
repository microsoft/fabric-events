# Abandoned Cart Recovery

## Business Context

The storefront already identifies abandoned carts and retains the cart identity, customer details, value and time of abandonment. The recovery team uses those records to prepare customer reminders and to organize commercial follow-up. Those activities share the same starting facts, but they serve different purposes and do not have the same eligibility rules.

Here, reminder preparation selects the carts that merit an email, while the team separately maintains a register of cart-level tasks. Staff must reconcile those two views rather than assume that everyone receiving an email represents all the work to be done. A lower-value cart still needs a task, and two carts belonging to one customer are two separate opportunities to follow up. The risk is incomplete coverage, not simply a missing email.

```mermaid
flowchart TB
    S["<b>Storefront</b><br/>Identifies abandoned carts"]
    C[("<b>Cart records</b><br/>Cart and customer<br/>Value and time")]
    E["<b>Reminder preparation</b><br/>Selects carts for outreach"]
    M["<b>Email channel</b><br/>Delivers customer reminders"]
    subgraph GAP["Gap: Some carts can miss<br/>their own follow-up task"]
        F["<b>Recovery team</b><br/>Reconciles tasks<br/>for every cart"]:::businessGap
    end
    T[("<b>Task register</b><br/>Individual carts")]
    S -->|"Cart details"| C
    C -->|"Select candidates"| E
    E -->|"Send reminder"| M
    C -->|"Cart context"| F
    F -->|"Record tasks"| T
    classDef businessGap fill:#FFF4CE,stroke:#C88700,color:#000000
    style GAP fill:none,stroke:#C88700,stroke-width:1.5px
```

## Current Problem

Reminder eligibility and follow-up coverage are not the same policy. If the reminder list is treated as the complete worklist, lower-value carts disappear from follow-up. If work is recorded only against the customer, separate carts can lose their individual context.

The team needs selective outreach and a complete cart-level task register, both based on the abandonment the storefront already identified.

<span id="why-business-events"></span>
<span id="business-value"></span>

## Improved Business Response {#business-events}

One response sends reminders for qualifying cart values; another registers a follow-up task for every valid cart, keeping different carts from the same customer separate. The storefront's abandonment finding is published as a Business Event and reaches these two independent responses.

```mermaid
flowchart TB
    S["<b>Storefront</b><br/>Identifies abandoned carts"]
    E(["<b>Business Event</b><br/>Individual cart abandonment"])
    M["<b>Customer outreach</b><br/>Sends reminders for<br/>cart values above 30"]
    subgraph VALUE["Improvement: Every valid cart gets<br/>its own follow-up task"]
        F["<b>Commercial follow-up</b><br/>Registers each cart<br/>as a separate task"]:::businessValue
        T[("<b>Task register</b><br/>Cart-specific context")]:::businessValue
    end
    S -->|"Share cart context"| E
    E -->|"Apply reminder policy"| M
    E -->|"Every valid cart"| F
    F -->|"Preserve each cart separately"| T
    classDef businessValue fill:#DFF6DD,stroke:#107C10,color:#000000
    style VALUE fill:none,stroke:#107C10,stroke-width:1.5px
```

The recovery team can apply different policies to customer communication and internal work without recreating abandonment detection. A reminder does not have to finish before a task is registered, and the task register does not determine who receives an email. The result is organized recovery work, not evidence of recovered revenue.

## Event-driven architecture {#architecture}

**Business signal:** `Ecommerce.Cart.Abandoned`

**Publisher:** User Data Function | **Consumer(s):** Activator (Power Automate: email and SQL follow-up)

```mermaid
flowchart TB
    S["<b>Storefront</b><br/>Identifies abandoned carts"]
    P["<b>Publisher UDF</b><br/>Validates six cart facts"]
    BE(["<b>Business Event</b><br/>Cart abandoned"])
    A["<b>Activator<br/>(Power Automate)</b><br/>Selects cart values<br/>above 30"]
    C["<b>Activator<br/>(Power Automate)</b><br/>Starts follow-up<br/>for every cart"]
    E["<b>Power Automate</b><br/>Sends a recovery email"]
    T["<b>Power Automate</b><br/>Registers cart-level<br/>follow-up tasks"]
    DB[("<b>SQL database</b><br/>Cart-level follow-up tasks")]
    EH[("<b>Eventhouse</b><br/>Received abandonments")]
    S -->|"Abandonment details"| P
    P -->|"Publish"| BE
    BE -->|"Subscribe"| A
    BE -->|"Subscribe"| C
    A -->|"Map cart context"| E
    C -->|"Map cart context"| T
    T -->|"Store task"| DB
    BE -.->|"Analyze in Eventhouse"| EH
    MAIL["<b>Approved mailbox</b><br/>Recovery reminders"]
    E -->|"Deliver"| MAIL
```

The reminder and task branches proceed independently after publication:

1. The publishing User Data Function validates the storefront's cart facts and shares the abandonment as a Business Event.
2. One Activator rule selects cart values strictly above 30 and invokes the Power Automate reminder flow, which delivers to the approved mailbox.
3. A separate Activator rule responds to every valid cart and invokes the SQL follow-up flow, preserving a task for that individual cart.

Use an approved test mailbox and the writable SQL database. The task register represents follow-up work, not a complete CRM application.

Dashed arrows show optional event history and its analysis, separate from the required response. **Analyze in Eventhouse** is a creation-time choice, not a connection enabled by the deployment package or a prerequisite for completing the Workbook.

## Business outcome and boundaries

From six synthetic carts, deliver three reminders for values strictly above 30 and create six SQL follow-up tasks. Different carts from the same customer remain separate tasks.

**Where the exercise stops:** Exact 30, below-threshold and zero-value carts create tasks but no reminder. This exercise does not calculate offers, recover orders or prove revenue recovery. Real outreach also needs current cart state, contact preferences and approved eligibility checks.

## Try the Workbook

The [Workbook](abandoned-cart-recovery/deployment.md){#deploy-this-scenario} brings preparation, connections, sample inputs, expected results and code reference into one place. Follow that single path to explore the outcome above.

<span id="evaluation-criteria"></span>
<span id="inspect-independent-eventhouse-history"></span>
<span id="prepare-the-sql-follow-up-register"></span>
<span id="prepare-two-genuine-custom-actions"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers-recovery-email-sql-follow-up-and-history"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
