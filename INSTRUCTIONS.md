# fabric-events Site — Developer Instructions

This file contains everything needed to resume development of the `microsoft/fabric-events`
resource site. Read it fully before making any changes.

---

## 1. Project Overview

This is a MkDocs + Material theme static site that serves as the community developer resource
for Microsoft Fabric Events. The goal is to accelerate developer adoption through practical,
code-first content — real API signatures, JSON schemas, and Mermaid diagrams instead of screenshots.

**Live site:** https://microsoft.github.io/fabric-events/  
**Source repo:** https://github.com/microsoft/fabric-events  

The site covers three pillars:

| Pillar | Status |
|--------|--------|
| Business Events | Active — Introduction, Design Guides, Recipes, 5 Scenarios |
| Fabric Events | Coming Soon placeholder |
| Azure Events | Coming Soon placeholder |

---

## 2. Repository & Git Setup

### Remotes

```
origin  → https://github.com/microsoft/fabric-events.git   (production)
fork    → https://github.com/<your-username>/fabric-events.git  (personal fork for PRs)
```

### Branches

| Branch | Purpose |
|--------|---------|
| `main` | Source code — all `.md` files, `mkdocs.yml`, `docs/` |
| `gh-pages` | Compiled site pushed by `mkdocs gh-deploy` — never edit manually |

### Workflow for every change

1. Make changes locally
2. Create a branch and commit:
   ```bash
   git checkout -b your-branch-name
   git add .
   git commit -m "describe change here"
   git push fork your-branch-name
   ```
3. Open a PR from your fork → `microsoft/fabric-events`
4. Do **not** include `Co-authored-by` trailers in commit messages

---

## 3. Deploy

### Auto-deploy (GitHub Actions)

Every merge to `main` triggers `.github/workflows/deploy.yml` which runs `mkdocs gh-deploy` automatically. No manual deploy needed after a PR merge.

### Manual deploy

```bash
cd <project-root>
mkdocs gh-deploy --remote-name origin --force
```

### Local preview

```bash
cd <project-root>
mkdocs serve --dev-addr 127.0.0.1:8000 --watch overrides
```

Preview at http://127.0.0.1:8000/fabric-events/

### Dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Project Structure

```
fabric-events/
├── mkdocs.yml                          # Site config, nav, theme, plugins
├── requirements.txt                    # MkDocs + Material dependencies
├── INSTRUCTIONS.md                     # This file
├── overrides/
│   └── main.html                       # Announcement banner + Ask a question widget
├── docs/
│   ├── index.md                        # Root redirect to /home/
│   ├── home/
│   │   └── index.md                    # Home page (3-pillar layout)
│   ├── stylesheets/
│   │   └── extra.css                   # Banner color, nav styles, heart CSS
│   ├── business-events/
│   │   ├── introduction/
│   │   │   ├── what-are-business-events.md
│   │   │   ├── architecture-overview.md
│   │   │   ├── event-schema.md
│   │   │   └── decision-guide.md
│   │   ├── design-guides/
│   │   │   ├── index.md
│   │   │   ├── structuring-payloads.md
│   │   │   ├── handling-retries.md
│   │   │   ├── schema-versioning.md
│   │   │   └── multi-consumer-fanout.md
│   │   ├── recipes/
│   │   │   ├── index.md
│   │   │   ├── filter-events-by-field-value.md
│   │   │   ├── chain-two-business-events.md
│   │   │   ├── batch-publish-notebook.md
│   │   │   └── batch-publish-udf.md
│   │   └── scenarios/
│   │       ├── index.md
│   │       ├── scenario-01-sales-volume-alert/README.md
│   │       ├── scenario-02-low-stock-threshold/README.md
│   │       ├── scenario-03-high-value-transaction/README.md
│   │       ├── scenario-04-pipeline-audit/README.md
│   │       └── scenario-05-loyalty-milestone/README.md
│   ├── fabric-events/
│   │   └── index.md                    # Coming soon placeholder
│   └── azure-events/
│       └── index.md                    # Coming soon placeholder
```

### Nav order

Introduction → Design Guides → Recipes → Scenarios

---

## 5. Site Features

### Announcement banner

Defined in `overrides/main.html` via `{% block announce %}`. Color is teal `#0D9488` set in `docs/stylesheets/extra.css`. The `announce.dismiss` feature is disabled so the banner is always visible.

### Ask a question widget

Also in `overrides/main.html` via `{% block content %}`. A floating button `💬 Ask a question` appears bottom-right on every page. The modal has two modes:

- **About this page** — pre-fills a GitHub Discussion with the question, page title, and page URL
- **General question** — pre-fills only the question, no page context

Discussions are created in the **Q&A** category of `microsoft/fabric-events`.

---

## 6. Architecture Concepts (Critical)

Getting these wrong will introduce inconsistencies across the site.

### Event Schema Registry
- A **platform-level capability** in Microsoft Fabric (not a UI section in Real-Time Hub)
- Contains **Event Schema Sets** (items you create inside the registry)
- Do NOT say users "go to the Event Schema Registry" — they don't navigate there directly

### Event Schema Set
- An item created inside the Event Schema Registry
- Each scenario uses one schema set (e.g., `RetailSales`, `RetailInventory`)
- The schema set **name** is used in the Connection Manager for UDF integration

### Business Event
- A special type of event schema
- **Must be created from Real-Time Hub → Business Events** — NOT from Event Schema Registry
- Creation wizard flow: define schema fields → enable "Analyze in Eventhouse" (on by default) → Create
- Enabling "Analyze in Eventhouse" auto-creates a dedicated KQL table

### Eventhouse Integration
- Enabled by default via checkbox during Business Event creation
- Creates a KQL table automatically — no separate ingestion config needed
- KQL table names preserve dots — use bracket syntax: `['Retail.Sales.VolumeAlert']`

### Platform behavior (critical)
- Publish **always succeeds silently** — no error is returned if the event type name mismatches
- Events are **discarded silently** if: wrong event type name, wrong schema set, no consumers subscribed
- Retry: automatic for up to 24 hours if delivery fails after acceptance

---

## 7. Naming Conventions

### Event namespace
```
Domain.Subdomain.EventName   (PascalCase for all three segments)
```

Examples used in the site:
- `Retail.Sales.VolumeAlert`
- `Retail.Inventory.LowStockThreshold`
- `Retail.Payment.HighValueTransaction`
- `DataOps.Pipeline.RunCompleted`
- `Retail.Customer.MilestoneReached`

### Schema set names
One schema set per domain, named without dots:
- `RetailSales`
- `RetailInventory`
- `RetailPayments`
- `DataOps`
- `RetailCustomers`

### Field names
`snake_case` — e.g., `product_id`, `store_id`, `threshold_qty`

---

## 8. Publisher APIs

### Notebook (Python)

```python
notebookutils.businessEvents.publish(
    eventSchemaSetWorkspace,  # workspace name string
    eventSchemaSet,           # schema set name string
    eventTypeName,            # e.g. "Retail.Sales.VolumeAlert"
    eventData,                # dict or list of dicts (batch)
    dataVersion="v1"
)
```

`eventData` can be a single dict or a list of dicts for batch publishing.

### User Data Function (Python)

Requires the connection decorator:

```python
import azure.functions as func
from fabric.functions import *

udf = UserDataFunctions()

@udf.connection(argName="businessEventsClient", alias="<schema-set-name>")
@udf.route(trigger=HttpTrigger())
def publish_event(req: HttpRequest, businessEventsClient: FabricBusinessEventsClient):
    event_data = { ... }
    businessEventsClient.PublishEvent(
        type="Retail.Inventory.LowStockThreshold",
        event_data=event_data,
        data_version="v1"
    )
```

**Connection setup (UI steps, must be documented in every UDF scenario):**
1. Open the UDF item in Fabric
2. In the ribbon, click **Home → Manage connections**
3. Click **Add connection**
4. Search for the Event Schema Set by name
5. Select it — the alias auto-populates from the schema set name
6. Save — the alias value goes into the `@udf.connection` decorator

### Eventstream (100% UI — no code)

1. Create a new Eventstream
2. Add a source (e.g., Azure Event Hubs, sample data)
3. Add a **Filter** transformation
4. Add a **Business Events** destination
5. Map source fields to schema fields in the destination configuration
6. Click **Publish**

Reference: https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-event-stream-publisher

### Activator as Publisher (UI only)

1. Open an Activator rule monitoring a KQL query, Power BI report, or Dashboard
2. Set the **Action** to **Publish a business event**
3. Map KQL column values to schema fields
4. Activate the rule

Reference: https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-activator

---

## 9. Consumer Flows

### Activator as Consumer

1. Go to **Real-Time Hub → Business events**
2. Find the Business Event and click **Set alert**
3. Configure:
   - **Monitor** — source event + optional filter condition
   - **Condition** — set to "On each event"
   - **Action** — choose from: Teams message, Email, Power Automate, Notebook, UDF, Data Pipeline
4. Save the rule

Reference: https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/consume-business-events-from-activator

### Eventhouse as Consumer

- Enabled automatically when "Analyze in Eventhouse" checkbox is checked during Business Event creation
- KQL table is named after the event type, preserving dots
- Use bracket syntax for dot-names:

```kql
['Retail.Sales.VolumeAlert']
| where ingestion_time() > ago(1h)
| summarize count() by store_id
```

Reference: https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-eventhouse

---

## 10. Content Rules (Non-Negotiable)

- **No em dashes** (`—`) anywhere in the content — split into two sentences or use a colon
- **No screenshots** — use Mermaid diagrams, JSON snippets, and code blocks
- **All content in English** — conversations between contributor and AI can be in Spanish
- **Numbered lists reset after code blocks** — indent code blocks 4 spaces inside the list item to preserve numbering
- **No troubleshooting content** — platform behavior is too early-stage; troubleshooting articles are parked outside the site until the platform matures

---

## 11. Content Types

### Design Guides
Cover a single design decision, platform behavior, or architectural pattern that applies across projects. Not tied to a specific scenario. Lives under `docs/business-events/design-guides/`.

### Recipes
Short, executable code or UI walkthroughs for a specific task. No business context needed. Lives under `docs/business-events/recipes/`.

### Scenarios
End-to-end walkthroughs with full business context, architecture diagram, step-by-step setup, and end-to-end test. Lives under `docs/business-events/scenarios/scenario-NN-short-name/README.md`.

---

## 12. Scenario Structure Template

Every scenario must follow this exact structure. Scenario 01 is the reference implementation — read it before writing a new scenario.

```
## Business context
2-3 sentence description of the business problem.

**The problem without Business Events:**
What the developer would have to do without this capability.

**The solution with Business Events:**
How Business Events solves the problem.

## Architecture
Mermaid flowchart showing: Publisher → Business Event → Consumer(s)
Include a table: Publisher, Consumer, Schema Set, Event Type

## Step 1: Create the Business Event
Numbered steps to create the Business Event in Real-Time Hub → Business Events.
Include the JSON schema definition.

## Step 2: [Publisher setup]
Publisher-specific steps with full code or UI walkthrough.

## Step 3: [Consumer setup]
Consumer-specific setup steps.

## Step 4: End-to-end test
How to trigger the event and verify it was received.
```

---

## 13. Existing Scenarios

| # | Title | Publisher | Consumer | Schema Set | Event Type |
|---|-------|-----------|----------|------------|------------|
| 01 | Sales Volume Alert | Notebook | Activator | RetailSales | Retail.Sales.VolumeAlert |
| 02 | Low Stock Threshold | User Data Function | Eventhouse | RetailInventory | Retail.Inventory.LowStockThreshold |
| 03 | High-Value Transaction | Eventstream | Activator | RetailPayments | Retail.Payment.HighValueTransaction |
| 04 | Pipeline Audit Trail | Notebook | Eventhouse | DataOps | DataOps.Pipeline.RunCompleted |
| 05 | Loyalty Milestone | Activator | Activator + Eventhouse | RetailCustomers | Retail.Customer.MilestoneReached |

---

## 14. Suggested Next Content

### Scenarios
- UDF → Activator (not yet covered as primary combination)
- Notebook → Activator (multi-consumer, two independent Activator rules)
- Eventstream → Eventhouse
- Cross-workspace publishing

### Design Guides
- Idempotency patterns for consumers
- Cross-workspace schema set strategy

### Recipes
- UDF → Activator end-to-end (short form)
- Filter events by nested field

---

## 15. Verified External Links

All links below were verified as of June 2026. Use these exact URLs in content:

| Topic | URL |
|-------|-----|
| Real-Time Hub overview | https://learn.microsoft.com/en-us/fabric/real-time-hub/real-time-hub-overview |
| Notebook how-to | https://learn.microsoft.com/en-us/fabric/data-engineering/how-to-use-notebook |
| User Data Functions overview | https://learn.microsoft.com/en-us/fabric/data-engineering/user-data-functions/user-data-functions-overview |
| Eventstream overview | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview |
| Eventhouse overview | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse |
| Business Events overview | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-overview |
| notebookutils (Spark utilities) | https://learn.microsoft.com/en-us/fabric/data-engineering/microsoft-spark-utilities |
| Event Schema Sets | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/schema-sets/create-manage-event-schema-sets |
| Event Schemas | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/schema-sets/create-manage-event-schemas |
| Create Business Events | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/create-business-events |
| Consume from Activator | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/consume-business-events-from-activator |
| Eventhouse consumer | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-eventhouse |
| UDF publisher | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-user-data-function |
| UDF + Activator tutorial | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/tutorial-business-events-user-data-function-activation-email |
| Eventstream publisher | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-event-stream-publisher |
| Eventstream + UDF + Activator tutorial | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/tutorial-business-events-event-stream-user-data-function-activator |
| Activator publisher | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-activator |
| Activator overview | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/activator/activator-introduction |
| CloudEvents spec | https://cloudevents.io/ |

---

## 16. Known Issues & Decisions

- **No troubleshooting section** — articles are parked in `temporal/troubleshooting/` outside the repo until platform behavior is stable enough to document reliably.
- **No Co-authored-by trailers** in commit messages.
- **Numbered lists and code blocks** — MkDocs resets list numbering after a fenced code block unless the block is indented 4 spaces inside the list item.
- **UDF alias** — auto-generated from schema set name (e.g., `RetailInventory` → alias `RetailInventory`). Document this explicitly in every UDF scenario.
- **Root redirect** — `docs/index.md` contains a JavaScript redirect to `/fabric-events/home/` because MkDocs requires a root `index.md` but the home page lives at `docs/home/index.md`.

