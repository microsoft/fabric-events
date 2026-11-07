Fabric Business Events / Prototype card: revenue

# Revenue below target

[Back to the scenario](../revenue-below-target.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/revenue-below-target)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\revenue-below-target`; for an extracted package, open that scenario folder instead.

The [source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/revenue-below-target) contains the complete native reference. [Download the executable package](../../../assets/downloads/revenue-below-target-cicd.zip). This site publishes the complete runbook; executable folders and ZIPs contain no documentation copies.

**Fresh native reference validated with an explicitly approved individual Teams destination.** Nine independently imported/read-back resources passed all six actual source states and both invalid-target guards. Exactly two genuine Business Events produced two matching actual Teams cards, two completed native investigation pipeline jobs and three useful persisted output rows. This is not a channel-delivery claim and not a generally available customer provisioning flow. This deployment guide remains the documentation source of truth.

## Card contract

Power BI semantic model/report -> Activator publisher -> `Sales.Revenue.BelowTarget` -> independent Activator and Eventhouse consumers. Activator starts a Teams notification and a real Data Factory investigation-data pipeline independently.

Default review names are listed below. Every name is explicitly configurable and bound to its actual imported identity. The history table follows the configured event name, `['Sales.Revenue.BelowTarget']` by default. These defaults are not identifiers of the validation deployment.

| Configuration role | Default name | Native resource |
|---|---|---|
| `source` | `RevenueSource` | Power BI semantic model |
| `report` | `RevenueMonitor` | Report with the actual six-field table visual |
| `schema` | `RevenueSchema` | Event Schema Set |
| `publisher` | `RevenuePublisher` | Activator with the genuine Power BI source and strict detector |
| `activator` | `RevenueActions` | Independent per-event Teams and pipeline rules |
| `eventhouse` | `RevenueHistory` | Eventhouse |
| `database` | `RevenueAnalytics` | Its actual child KQL database |
| `notebook` | `RevenueInvestigationNotebook` | Useful history investigation notebook |
| `pipeline` | `RevenueInvestigation` | Native pipeline running that notebook |

## Implemented source

[schema.avsc](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/schema.avsc) contains exactly the card's six v1 fields. [scenario.json](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/scenario.json) specifies the publisher, independent responses, reviewed resources and validation boundaries. [variance_pct.dax](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/publisher/variance_pct.dax) remains the canonical numeric measure for a `RevenuePerformance` table with `region`, `period_start`, `revenue` and `target`.

The measure compares revenue and target in the same region, period and currency context. It returns `-20`, not `-0.20`, for revenue 80,000 against target 100,000. Do not apply percent formatting that multiplies the value again. Zero or negative targets yield no valid variance.

The native publisher must monitor `variance_pct` with **Becomes less than -15**. Exactly -15 does not publish. Staying below the threshold is not another crossing; recover above it before testing a second crossing. The source report's refresh and Activator evaluation cadence must both be observed. Python must not evaluate the condition and publish an event on Activator's behalf.

## Native bindings and preparation

The runtime binds the genuine semantic model, report/page/visual binding and native publisher to their actual imported identities. All six event fields come from the same native source row. `detected_at` is a calculated **UTC model-refresh snapshot**, formatted as ISO 8601 basic UTC `yyyyMMddTHHmmssZ`, for example `20260115T143000Z`. It is not Activator ingestion/detection time. The calculated column uses the DAX shown in the full scenario; the basic representation avoids the connector's verified culture-format coercion of extended ISO text.

Configure an approved individual Teams destination explicitly using `teams_delivery: "individual"` and `teams_recipients`; `@me` resolves to the authenticated delegated identity. Channel delivery is rejected, not silently replaced with a direct message. The channel setup in the manual scenario remains available separately but is not validated by this executable reference.

The real pipeline uses a native `TridentNotebook` activity with actual parameter mapping. Its bound parameters are the actual child database's `query_endpoint`, its `database_id`, and `pipeline_run_id` from `@pipeline().RunId`. The per-event Activator action has no invented event trigger parameters. Each execution refreshes the full dedicated event history and persists original event fields, `shortfall`, `revenue_lift_pct`, the actual pipeline execution ID and UTC `investigated_at`. It never modifies the source model, report, revenue or target. Teams and pipeline rules remain independent of each other and of the source detector.

Deployment verifies workspace/capacity, ownership and name collisions; imports the schema and Eventhouse/database; grants the selected delegated identity access; associates the Business Event to the actual child database; and imports/read-backs all native dependencies in order. It requests TMSL explicitly for BIM model readback. It also performs an actual **empty model refresh**, initializing the calculated UTC column without loading any fixture or publishing test events. No Notebook or KQL detector substitutes for the Power BI publisher.

## Prerequisites and separate commands

Use Python 3.10 or later and the authenticated Azure CLI transport already used by the package. Select the approved service environment and a workspace on active Fabric capacity. The live proof applies to the authorized reference target, not every selectable environment. Automatic history association still uses service capabilities unavailable in the general customer provisioning interface.

Deployment is API-driven. Strict end-to-end source and Teams observation additionally requires **interactive Windows, PowerShell 7, Edge, authenticated Fabric, and an authenticated Teams session open to the authorized Fabric Activator bot**. A headless CI worker cannot replace native source samples or actual Teams cards with elapsed time or operator success flags.

Copy `config.card.example.json` to a private configuration, select the approved workspace, review all nine names and the event name, and choose the approved individual recipients:

```powershell
Set-Location '.\cicd\revenue-below-target'
$configFile = '.\config.local.json'
.\deploy.bat --config $configFile
```

Every deployment creates a fresh scenario-local ignored `.deployments` ledger and records it as the current attempt before writes. Existing unmanaged names are not adopted; use unused reviewed names for a new deployment. A missing current ledger never falls back to an older one.

An interrupted deployment requires its exact recorded ledger:

```powershell
.\deploy.bat --config $configFile --resume --state-file '<exact-deployment-ledger.json>'
```

Native import and empty-refresh resume read recorded operations without resubmitting them. A changed configuration, unresolved ambiguous response or externally changed ownership stops the operation. Do not redeploy over a source already used by the separate test.

Only after preparation succeeds, explicitly run:

```powershell
.\test.bat --config $configFile
```

The test opens the exact deployed native source page and only inspects the authorized bot chat. When multiple matching windows exist, supply `--browser-handle` and `--teams-handle` with their exact owned Windows handles. Dismiss any bot profile dialog so the actual conversation is visible. No unrelated chat, substituted publisher, manual pipeline job or manually submitted Business Event supplies the evidence.

## External test sequence and evidence

[data/test-cases.json](https://github.com/microsoft/fabric-events/blob/main/cicd/revenue-below-target/data/test-cases.json) starts at -10, observes exactly -15 without publication, then crosses to -20. It also covers remaining below, recovery and another crossing. Invalid target cases are separate. These are expected test vectors, not evidence that DAX or the native detector has run.

Use a fresh synthetic region or isolated test period on every run. Apply one external source change, refresh the report and confirm publisher evaluation before advancing. Preserve the region, period and currency filter context. For each crossing, independently verify the full Eventhouse payload, the actual Teams notification and the completed investigation-data pipeline run. A published event, saved rule or pipeline invocation alone proves none of the other outcomes.

| State | Actual variance | Expected cumulative events |
|---|---|---|
| Fresh active baseline | -10 | 0 |
| Exact boundary | -15 | 0 |
| First crossing | -20 | 1 |
| Remain below | -22 | 1 |
| Recovery | -10 | 1 |
| Second crossing | -20 | 2 |
| Zero target | DAX BLANK | 2 |
| Negative target | DAX BLANK | 2 |

The source and detector stay continuously active between valid states. Each state requires the actual refreshed model and a complete native source row with stable region/period identity before advancing. Invalid targets require actual DAX BLANK and two observed active source cadence intervals with no additional events. This does **not** claim a native row for a BLANK metric; the connector may omit it.

Final verification independently reads the native consumer/pipeline/notebook definitions and history association, both six-field Business Event payloads, both actual matching Teams cards, exactly two new completed native pipeline jobs and useful persisted output tied to their exact execution IDs. The second pipeline must include both crossings in its full-history refresh. The reference produced three output rows: one in the first execution and two in the second.

All inputs, request identities, actual source captures, private Teams evidence and pipeline output remain beside the ignored ledger. No personal destination, credential, resource ID or capture belongs in the ZIP or website.

`--resume` can continue an existing observation without replaying a requested input when its native source and detector are still active. A source update or refresh with an unresolved cloud outcome is not automatically replayed. A failed test pauses only its owned source and consumer rules; do not reactivate it and pretend the original detector baseline survived. Preserve that evidence and use a fresh reviewed deployment for another complete sequence.

To recheck a completed run without changing any input:

```powershell
.\test.bat --config $configFile --state-file '<exact-ledger.json>' --verify-only
```

An existing native Teams capture can be imported with `--verify-only --teams-file '<private-capture.json>'`; its unique run and all fields must match both independently observed crossings. There is no `--teams-confirmed` shortcut.

## Offline checks and distribution

```powershell
python -m unittest discover -s tests -p "test_*.py"
python bundle.py --output '<output-folder>\revenue-below-target-cicd.zip'
```

Focused tests cover native two-field identity and six-field publication, actual-refresh request recording/resume without replay, concurrent-refresh rejection, deployment/test option separation and channel-to-DM rejection. Offline tests do not establish live delivery. The bundle uses an explicit allowlist of executable sources, configuration examples and native definitions; it excludes documentation, private configuration, `.deployments`, captures and caches.

Reference: [canonical revenue scenario](../revenue-below-target.md).

## Continue reading

- [Full scenario and manual setup](../revenue-below-target.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
