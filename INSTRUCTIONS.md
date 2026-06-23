# fabric-events Site — Developer Instructions

This file contains everything needed to resume development of the `microsoft/fabric-events`
resource site. Read it fully before making any changes.

---

## 1. Project Overview

This is a MkDocs + Material theme static site that serves as the official public resource
center for Microsoft Fabric Business Events. The goal is to accelerate developer adoption
through practical, code-first content — real API signatures, JSON schemas, and Mermaid
diagrams instead of screenshots.

**Live site:** https://microsoft.github.io/fabric-events/  
**Source repo:** https://github.com/microsoft/fabric-events  
**Local project path:** `C:\Workspace\projects\fabric-events`

The site covers three pillars, only the first is active:

| Pillar | Status |
|--------|--------|
| Business Events | Active — has Introduction + 5 Scenarios |
| Fabric Events | Coming Soon placeholder |
| Azure Events | Coming Soon placeholder |

---

## 2. Repository & Git Setup

### Remotes

```
origin  → https://github.com/microsoft/fabric-events.git   (production)
fork    → https://github.com/robece/fabric-events.git       (personal fork for PRs)
```

### Git identity

Configure your git identity before committing:

```bash
git config user.name "your-github-username"
git config user.email "your-email"
```

### Branches

| Branch | Purpose |
|--------|---------|
| `main` | Source code — all `.md` files, `mkdocs.yml`, `docs/` |
| `gh-pages` | Compiled site pushed by `mkdocs gh-deploy` — never edit manually |

### Workflow for every change

1. Make changes locally under `C:\Workspace\projects\fabric-events`
2. Commit and push to the fork:
   ```bash
   git add .
   git commit -m "describe change here"
   git push fork main
   ```
3. Open a PR from `robece/fabric-events` → `microsoft/fabric-events`
4. Do **not** include `Co-authored-by` trailers in commit messages

---

## 3. Deploy

MkDocs compiles the site and pushes directly to the `gh-pages` branch of `origin`:

```bash
cd C:\Workspace\projects\fabric-events
mkdocs gh-deploy --remote-name origin --force
```

- This does **not** require a PR — it writes `gh-pages` directly
- Run this after a PR is merged to production, or to preview live changes
- The site is available at https://microsoft.github.io/fabric-events/ within ~2 minutes

### Local preview

```bash
cd C:\Workspace\projects\fabric-events
mkdocs serve --dev-addr 127.0.0.1:8000
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
├── docs/
│   ├── index.md                        # Home page (3-pillar layout)
│   ├── stylesheets/
│   │   └── extra.css                   # Hides logo icon, full-width layout
│   ├── javascripts/
│   │   └── nav.js                      # Navigation helper
│   ├── 01-introduction/
│   │   ├── what-are-business-events.md
│   │   ├── architecture-overview.md
│   │   ├── event-schema.md
│   │   └── decision-guide.md
│   ├── 02-scenarios/
│   │   ├── index.md                    # Scenario overview table
│   │   ├── scenario-01-sales-volume-alert/README.md
│   │   ├── scenario-02-low-stock-threshold/README.md
│   │   ├── scenario-03-high-value-transaction/README.md
│   │   ├── scenario-04-pipeline-audit/README.md
│   │   └── scenario-05-loyalty-milestone/README.md
│   ├── fabric-events/
│   │   └── index.md                    # Coming soon placeholder
│   └── azure-events/
│       └── index.md                    # Coming soon placeholder
```

---

## 5. Architecture Concepts (Critical)

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

---

## 6. Naming Conventions

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

## 7. Publisher APIs

### Notebook (Python)

```python
notebookutils.businessEvents.publish(
    eventSchemaSetWorkspace,  # workspace name string
    eventSchemaSet,           # schema set name string
    eventTypeName,            # e.g. "Retail.Sales.VolumeAlert"
    eventData,                # dict or JSON string
    dataVersion="v1"
)
```

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

Steps:
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

## 8. Consumer Flows

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

## 9. Content Rules (Non-Negotiable)

- **No em dashes** (`—`) anywhere in the content — split into two sentences or use a colon
- **No horizontal rule dashes** (`---`) as visual dividers
- **No generic "hub" or "bus"** — only allowed as part of: "Real-Time Hub", "Event Hubs", "Service Bus"
- **No screenshots** — use Mermaid diagrams, JSON snippets, and code blocks
- **No icons** on the three pillar cards on the home page
- **All content in English** — conversations between contributor and AI can be in Spanish
- **Numbered lists reset after code blocks** — indent code blocks 4 spaces inside the list item to preserve numbering

---

## 10. Scenario Structure Template

Every scenario must follow this exact structure. Scenario 01 is the reference implementation
— read it before writing a new scenario.

```
## Overview
Brief 2-3 sentence description. Include the publisher, consumer, and business value.

## Architecture
Mermaid flowchart showing: Publisher → Business Event → Consumer(s)
Include a table: Publisher, Consumer, Schema Set, Event Type, Namespace

## Step 1: Create the Business Event
Numbered steps to create the Business Event in Real-Time Hub → Business Events.
Include the JSON schema definition (paste into the schema editor).
End with "Analyze in Eventhouse" checkbox note and "Create" button.

## Step 2: Create the [Publisher]
How to create and configure the publisher item (Notebook, UDF, Eventstream, Activator rule).
Include all relevant code or UI steps.

## Step 3: Connect and Publish
Publisher-specific connection/configuration steps.
Full working code block (for code publishers) or full UI walkthrough (for Eventstream/Activator).

## Step 4: Set Up [Consumer]
Consumer-specific setup steps (Activator alert config or Eventhouse KQL queries).

## Step 5: End-to-End Test
How to trigger the event and verify it was received.
For code publishers: show the API call and expected response.
For Eventhouse consumers: show the KQL query that confirms arrival.
```

---

## 11. Existing Scenarios

| # | Title | Publisher | Consumer | Schema Set | Event Type |
|---|-------|-----------|----------|------------|------------|
| 01 | Sales Volume Alert | Notebook | Activator | RetailSales | Retail.Sales.VolumeAlert |
| 02 | Low Stock Threshold | User Data Function | Eventhouse | RetailInventory | Retail.Inventory.LowStockThreshold |
| 03 | High-Value Transaction | Eventstream | Activator | RetailPayments | Retail.Payment.HighValueTransaction |
| 04 | Pipeline Audit Trail | Notebook | Eventhouse | DataOps | DataOps.Pipeline.RunCompleted |
| 05 | Loyalty Milestone | Activator | Activator | RetailCustomers | Retail.Customer.MilestoneReached |

All 5 are marked `✅ Available` in `docs/02-scenarios/index.md`.

---

## 12. Suggested Next Scenarios

When adding new scenarios, follow these guidelines:
- Cover publisher/consumer combinations not yet represented
- Each scenario should be in a different industry vertical when possible
- Schema set names should follow the `Domain` (no dots) convention
- Place new scenario folder under `docs/02-scenarios/scenario-NN-short-name/README.md`
- Add entry to `docs/02-scenarios/index.md` table
- Add entry to `mkdocs.yml` nav under `Business Events → Scenarios`

Possible future scenarios:
- UDF → Activator (not yet covered)
- Notebook → Activator (multi-consumer, second Activator rule)
- Eventstream → Eventhouse
- Cross-workspace publishing
- Batched event publishing from a Notebook

---

## 13. Verified External Links

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

## 14. mkdocs.yml Notes

- `site_url` must be `https://microsoft.github.io/fabric-events/` — do not change for `origin` deploys
- If deploying to the fork (`robece/fabric-events`) for preview, temporarily change to `https://robece.github.io/fabric-events/` and revert before committing
- Mermaid diagrams are enabled via the `pymdownx.superfences` extension — no additional plugin needed
- The `gh-pages` branch is managed exclusively by `mkdocs gh-deploy`

---

## 15. Known Issues & Decisions

- **No Resources section** — API Reference, Troubleshooting, and FAQ were removed from the nav and their files deleted. Recreate only if there is substantial content to fill them.
- **No icons on pillar cards** — home page uses text-only cards for the three pillars.
- **No Co-authored-by trailers** in commit messages.
- **Numbered lists and code blocks** — MkDocs resets list numbering after a fenced code block unless the block is indented 4 spaces inside the list item.
- **UDF alias** — auto-generated from schema set name (e.g., `RetailInventory` → alias `RetailInventory`). Document this explicitly in every UDF scenario so users know where to find it.
