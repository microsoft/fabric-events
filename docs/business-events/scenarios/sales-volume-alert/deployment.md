# Sales volume alert: deployment and testing {#sales-volume-alert-deployment-and-testing}

[Back to the scenario](../sales-volume-alert.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/sales-volume-alert)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\sales-volume-alert`; for an extracted package, open that scenario folder instead.

**Reading this guide on the documentation site?** Obtain the complete
[source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/sales-volume-alert)
before running commands. The site publishes this complete runbook and links to the implementation,
not the complete executable folder. For another approved target, start with the [wizard configuration](https://github.com/microsoft/fabric-events/blob/main/cicd/sales-volume-alert/config.wizard.example.json)
and [configuration schema](https://github.com/microsoft/fabric-events/blob/main/cicd/sales-volume-alert/config.schema.json), then use the same local configuration
with both launchers as described below.

This self-contained folder implements the Day Zero **Sales volume dropped** card
with real Fabric items. Its business reference is
[Sales Volume Alert](../sales-volume-alert.md).

Run the commands in this guide from `cicd\sales-volume-alert`, including after
extracting the ZIP. The scenario folder contains its launchers and local records.
All documentation stays on this page, not in the folder or ZIP:

```
cicd\
  sales-volume-alert\
    deploy.bat
    test.bat
    .deployments\
    deploy.py
    card.py
    items\
```

**Deployment model: no Git integration or Git operations.** Local native item definitions
are submitted to Fabric's **Bulk Import Item Definitions API**. Fabric creates or updates
the selected items and handles their dependencies. Python packages the files, waits for
the native operation, configures demonstrated runtime gaps and verifies the results.
It does not generate item graphs or create resources one at a time.

Native deployment, publication and Eventhouse delivery require independent verification, including actual Teams delivery. Local validation is not cloud deployment evidence. This is not yet a fully unattended production CI/CD pipeline. The versioned wizard configuration described below adds configurable event and item names. Its bindings and packaged entry points are validated offline; historical delivery evidence does not certify every naming combination.

## Start here {#start-here}

| Task | Instructions |
| --- | --- |
| Deploy the sales-volume card, inspect it, then test separately | [Run the card](#copy-and-paste-run-the-sales-volume-card) |
| Deploy again after deleting the workspace items | [Clean deployment quick start](#clean-deployment-quick-start) |
| Inject workspace and Teams parameters | [Configuration and parameter injection](#configuration-and-parameter-injection) |
| Change Fabric artifact names | [Custom artifact names](#custom-artifact-names) |
| Hand the scenario to portal engineering | [Engineering handoff](#engineering-handoff) |
| Run from a notebook instead of the local CLI | [Manual execution from the Fabric portal](#manual-execution-from-the-fabric-portal) |
| Resume an interrupted operation | [Provisioning and recovery](#2-provision-the-foundations) |
| Check actual consumer delivery | [Verify the two consumers](#5-verify-the-two-consumers-independently) |

The complete guide is maintained only in `docs/business-events/scenarios/sales-volume-alert/deployment.md`,
as a subpage of the scenario. Neither the executable folder nor its ZIP contains documentation.
The deployment ZIP is generated separately from the native sources.

## Copy and paste: run the sales-volume card {#copy-and-paste-run-the-sales-volume-card}

This is the real counterpart of **Store transactions below expectations**
(`Sales volume dropped`), not the prototype's simulated Create button.
The two Windows launchers in the scenario folder deliberately have separate
responsibilities. `deploy.bat` prepares the environment only. `test.bat` explicitly
loads dummy inputs, publishes once and verifies delivery. Deployment never invokes
the test.

Both launchers must use the same reviewed local configuration. Copy `config.wizard.example.json` to `sales-volume.local.json`, set your approved workspace UUID and review the destinations before deploying. `config.card.example.json` records the card's reference defaults; it is not a shared customer target. The native card contract is:

| Setting | Value |
| --- | --- |
| Event | `Retail.Sales.VolumeAlert`, with the card's six fields |
| Schema set / Notebook | `SalesDropSchema` / `SalesDropPublisher` |
| Activator / Eventhouse / database | `SalesDropActions` / `SalesDropHistory` / `SalesDropAnalytics` |
| Inputs | Separate KQL transaction and baseline tables; only the test loads external fixtures |
| Response | Native Teams message to the current user (`@me`) only for `deviation_pct < -30` |
| History | Every published comparison in the managed `Retail.Sales.VolumeAlert` table |

**Before deploying:** use Python 3.12+ and an already authenticated Azure CLI user
session with access to that workspace and capacity. Wait for any intended workspace
cleanup to finish and resolve older running operations. The five `SalesDrop` items
must not already exist for a fresh ledger. Neither launcher deletes or adopts
items or opens a login window. Running a launcher confirms the target workspace
in its selected configuration. The base configuration is specific to this approved
workspace, not a universal target for other users.

### Deploy only {#deploy-only}

From PowerShell in this checkout, copy:

```powershell
Set-Location .\cicd\sales-volume-alert
$configFile = '.\sales-volume.local.json'
if (Test-Path -LiteralPath $configFile) { throw 'Preserve the existing file or choose a new local configuration name.' }
Copy-Item .\config.wizard.example.json $configFile
$config = Get-Content $configFile -Raw | ConvertFrom-Json
$config.environment = 'fabric'
$config.workspace_id = '<your-workspace-uuid>'
$config | ConvertTo-Json -Depth 20 | Set-Content $configFile -Encoding utf8
.\deploy.bat --config .\sales-volume.local.json
```

This runs the offline plan, read-only preflight and native deployment. It configures
the five items, empty source-table schemas, permissions, Notebook defaults,
Eventhouse connection and active Activator rule. **It does not seed data, execute
the publisher, send test notifications or claim consumer delivery.** Existing
source data is not cleared. Inspect the resources and their configuration in Fabric
before deciding to test.

**Every `deploy.bat` invocation is a new deployment.** It does not read, validate,
resume, reset or archive any previous deployment. A new execution gets its own ledger:

```
cicd\sales-volume-alert\.deployments\<deployment-scope>\<your-workspace-uuid>\SalesDrop.deploy-<UTC timestamp>-<unique ID>.json
```

The only shared reference is `SalesDrop.active.json` beside those ledgers.
It selects the exact ledger for this new invocation so that a later `test.bat`
addresses the environment just deployed. It is not read by `deploy.bat` and is
not used to recover or validate historical resources. Older ledgers remain untouched.
The `.deployments` folder sits beside `deploy.bat` and `test.bat`.
It is excluded by the repository's `.gitignore` and never included in the deployment
ZIP. The launchers locate it relative to their own files, not your working directory
or user profile. Explicit `--state-file` overrides still use your selected location.

Preflight still checks the **current target**: access, capacity and available item
names. If the configured names already exist, deployment stops rather than adopting,
updating or deleting those items. To run the same configuration again after cleanup,
wait for the scenario artifacts to disappear from Fabric, then run `deploy.bat`.
There is no historical-ID check or ledger cleanup to perform.

A failed new attempt does not make `test.bat` fall back to an older successful
deployment. Keep the printed configuration and ledger paths if you need to inspect
or resume that specific operation using the lower-level CLI. Do not start another
deployment to poll or resume a running one.

For an extracted ZIP, open its `cicd\sales-volume-alert` folder instead.
Both launchers locate their sources relative to themselves, not the current directory.

### Test only, after reviewing the deployment {#test-only-after-reviewing-the-deployment}

Run this separately from the same folder:

```powershell
.\test.bat --config .\sales-volume.local.json
```

The test requires the deployment's saved configuration and complete item ledger.
Without `--state-file`, it uses only the explicit active reference, never a search
for an older or apparently successful deployment.
It does not provision or repair resources. It validates the existing consumer
configuration, initializes **five baselines and 3,868 transactions**, publishes
**one batch of five comparisons**, then checks Eventhouse. Running it authorizes
real sample-data ingestion, publication and Teams notifications. Every deliberately
repeated test creates a fresh batch. Expected results:

| Store suffix | Deviation | Eventhouse | Teams |
| --- | --- | --- | --- |
| `store-mx-042` | -40.56% | Retained | Notify |
| `store-mx-043` | -20% | Retained | No notification |
| `store-mx-044` | -30% | Retained | No notification |
| `store-mx-045` | 0% | Retained | No notification |
| `store-mx-046` | -100% | Retained | Notify |

Check the **Fabric Activator** chat in Teams for exactly the two matching
notifications. Match the printed run UUID in each `store_id`, not previous cards.
Exit code **2 is not end-to-end success**: it means Eventhouse was verified
but the CLI cannot independently prove Teams delivery. No automatic Teams attestation
is included. Allow time for asynchronous ingestion and notification delivery.

To wait for asynchronous delivery, repeat **only verification**. This command
does not seed, publish or redeploy:

```powershell
.\test.bat --config .\sales-volume.local.json --verify-only
```

Only after personally checking the two actual Teams cards, their matching run UUID
and the absence of other notifications for that run, explicitly attest:

```powershell
.\test.bat --config .\sales-volume.local.json --verify-only --teams-confirmed
```

Neither launcher silently supplies that attestation. Exit `0` from deployment means
the environment is configured, not tested. Test exit `1` means failure or incomplete
evidence; do not resubmit the publisher to poll delivery. A saved interrupted
operation must be resumed before starting another test.

### Custom configuration or an existing deployment {#custom-configuration-or-an-existing-deployment}

Copy the base JSON to your own file, edit names, workspace or supported settings,
then select **the same config and ledger for both phases**. An explicit deployment
`--state-file` must be a new, unused path; omit it for automatic unique filenames:

```powershell
.\deploy.bat --config "C:\my scenario\config.json" --state-file "C:\my scenario\state.json"
# After inspection, run separately:
.\test.bat --config "C:\my scenario\config.json" --state-file "C:\my scenario\state.json"
# Later, verify this exact run without publishing again:
.\test.bat --verify-only --config "C:\my scenario\config.json" --state-file "C:\my scenario\state.json"
```

To test an environment created with an older launcher, pass its printed configuration
and ledger explicitly to `test.bat`; it does not guess or migrate them.
When continuing a specific operation through the lower-level CLI, identity drift
and unversioned ledger reuse are still rejected. For an interrupted job or import,
use those saved paths with
[the appropriate resume command](#f-continue-or-resume-with-the-same-ledger).
The full flow still depends on the supported native association bridge;
this is not a promise of complete public-Fabric automation or a newly cloud-validated
run of the versioned bindings.

## Clean deployment quick start {#clean-deployment-quick-start}

Use this sequence for a new deployment, including after manually deleting the prior
scenario items. **Do not run it while deletion is still in progress.** It creates
real Fabric items and the final publication sends real Teams notifications.

Prerequisites: Python 3.12+, Azure CLI with an existing authenticated delegated user
session, the approved workspace on active Fabric capacity, and permission to create
items and configure the dedicated schema set. No script opens a login window.
No Git operation or new Azure capacity is required.

### A. Select the files and target {#a-select-the-files-and-target}

Run in PowerShell from this scenario's folder. For this checkout:

```powershell
Set-Location .\cicd\sales-volume-alert

$Scenario = '.'
$WorkspaceId = '<your-workspace-uuid>'
$Config = Join-Path (Resolve-Path -LiteralPath $Scenario).Path 'config.rebuild.local.json'

if (Test-Path -LiteralPath $Config) {
    throw 'Preserve the existing configuration. Choose a new local file name or review and reuse it deliberately.'
}

$Settings = Get-Content -Raw (Join-Path $Scenario 'config.wizard.example.json') | ConvertFrom-Json
$Settings.environment = 'fabric'
$Settings.workspace_id = $WorkspaceId
$Settings.teams_delivery = 'individual'
$Settings.teams_recipients = @('@me')
[System.IO.File]::WriteAllText(
    $Config,
    ($Settings | ConvertTo-Json -Depth 20),
    [System.Text.UTF8Encoding]::new($false)
)

$LedgerFolder = Join-Path (Resolve-Path -LiteralPath $Scenario).Path ".deployments\$($Settings.environment)\$WorkspaceId"
$State = Join-Path $LedgerFolder ($Settings.resource_prefix + ".clean-" + [guid]::NewGuid().ToString('N') + '.json')
Write-Output "Configuration: $Config"
Write-Output "Keep this ledger path for every remaining command: $State"
```

For an extracted deployment ZIP, set the working directory to its
`cicd\sales-volume-alert` folder instead. For custom names, edit the versioned configuration
before proceeding; there is no need to copy or rename the native package.

**Create `$State` once, then retain that exact path.** `preflight` does not create the
ledger; the first successful write stage saves it. Record the path outside the shell,
and set `$State` to the same value after reopening PowerShell. A new random path for
every command is not a continuation strategy.

### B. Review, then check the target {#b-review-then-check-the-target}

The plan is offline. Preflight reads Fabric but does not deploy:

```powershell
python "$Scenario\deploy.py" plan --config $Config
if ($LASTEXITCODE -ne 0) { throw 'Fix the offline plan before continuing.' }

python "$Scenario\deploy.py" preflight --config $Config --state-file $State
if ($LASTEXITCODE -ne 0) { throw 'Resolve the target or ownership check before deploying.' }
```

Review the environment, workspace, all artifact names, event fields and Teams
destination. With a fresh ledger, existing managed-name items are deliberately
treated as collisions, not silently adopted.

### C. Deploy only, then inspect {#c-deploy-only-then-inspect}

```powershell
python "$Scenario\deploy.py" deploy `
    --config $Config --state-file $State `
    --confirm-workspace $WorkspaceId
if ($LASTEXITCODE -ne 0) { throw 'Deployment did not finish. Inspect or resume its saved operation.' }
```

This configures all five native items, schema permissions, Notebook bindings,
Eventhouse ingestion and the approved Activator action. It neither initializes
dummy inputs nor publishes events. Inspect the deployed environment before moving
to the independent test stage.

For an optional separate runtime check, run `runtime-check` with the same
`--config`, `--state-file` and `--confirm-workspace` arguments. It publishes nothing.

### D. Explicitly initialize and publish one test batch {#d-explicitly-initialize-and-publish-one-test-batch}

```powershell
python "$Scenario\deploy.py" run `
    --config $Config --state-file $State --confirm-workspace $WorkspaceId --seed-dummy-data
if ($LASTEXITCODE -ne 0) { throw 'Inspect the existing publisher job. Do not blindly submit another one.' }
```

This explicitly initializes five baselines and 3,868 synthetic transactions, then
publishes five findings. If real inputs or an existing saved seed batch will be
used instead, omit `--seed-dummy-data`. Never use publication to poll ingestion
or notifications.

### E. Verify the actual results {#e-verify-the-actual-results}

```powershell
python "$Scenario\deploy.py" verify --config $Config --state-file $State
```

Expected: five distinct findings in Eventhouse and two Teams notifications for
deviations -40.56% and -100%. The -20%, exact -30% and 0% findings must not notify.
Match the run UUID in each `store_id`, not older messages from previous tests.

| Exit code | Meaning |
| --- | --- |
| `0` | Command succeeded. For `verify --teams-confirmed`, includes explicit operator attestation. |
| `1` | Configuration, service, runtime or evidence failure. Inspect the error and saved operation. |
| `2` | `verify` confirmed Eventhouse, but the CLI has not independently confirmed Teams. |

After personally checking the two actual Teams cards and the absence of other
matches for this run, an operator can run:

```powershell
python "$Scenario\deploy.py" verify --config $Config --state-file $State --teams-confirmed
```

### F. Continue or resume with the same ledger {#f-continue-or-resume-with-the-same-ledger}

```powershell
python "$Scenario\deploy.py" status --config $Config --state-file $State
python "$Scenario\deploy.py" resume-import --config $Config --state-file $State
python "$Scenario\deploy.py" resume --config $Config --state-file $State
```

These are alternatives, not a batch to execute unconditionally: `status` inspects,
`resume-import` polls a saved native import, and `resume` polls a saved Notebook job.
Use only the command corresponding to the saved operation.

### After manual deletion: do not reuse stale identities {#after-manual-deletion-do-not-reuse-stale-identities}

A previous deployment's ledger, whether under the scenario-local `.deployments` folder or an explicitly selected path, remains historical evidence after its Fabric items are deleted:

```
%USERPROFILE%\.copilot\state\fabric-events-cicd\<deployment-scope>\<your-workspace-uuid>\SalesVolumeCicdV1.json
```

It records the **old** item IDs, connection and runs. Deleting items in the portal
does not clear that file. Keep it as historical evidence; do not edit individual
IDs or copy its connection into the new deployment. Use the fresh `$State` path
from the quick start consistently, including for verification and resumption.
If any old scenario item still exists, finish the intended cleanup or continue
using its original ledger instead. A fresh ledger must not overwrite live items.

Fabric deletion and name availability can be asynchronous. Wait for the intended
items to disappear and for their names to become available. An empty-looking
workspace alone does not establish that a previous native operation is resolved.
Never start a clean deployment while an earlier import or publisher is still running.
Changing only the local ledger does not cancel a cloud operation or release a name.

For a clean portal deployment, use a new launcher notebook without the previous
`builtin/sales-volume-alert-state.json`. Preserve the previous launcher and ledger
as evidence. A notebook session restart is not a reliable ledger reset.
When continuing rather than recreating, transfer the current ledger between clients
as described in the portal section.

## Configuration and parameter injection {#configuration-and-parameter-injection}

The CLI injects settings through **`--config <JSON-file>`**, not arbitrary
environment-variable substitution. These are the supported configuration keys:

| Key | Example | Behavior |
| --- | --- | --- |
| `environment` | `"fabric"` | Selects the public Fabric API endpoint and default ledger namespace. Complete automatic association remains subject to the provisioning limits documented below. |
| `workspace_id` | `"<your-workspace-uuid>"` | Existing approved workspace UUID, not its display name. Required for cloud stages. |
| `resource_prefix` | `"SalesDrop"` | Stable deployment namespace for the default ledger, versioned logical IDs and optional plan name. Not an automatic renaming prefix. |
| `configuration_version` | `"1.0"` | Selects the wizard contract. Omit only when continuing a legacy configuration. |
| `scenario_id` | `"sales-volume"` | Stable card/scenario identifier. This configuration applies only to the sales-volume card. |
| `event_name` | `"Retail.Sales.VolumeAlert"` | Binds the native event/schema identity, Notebook publisher, Activator source, association and history query together. |
| `source_backend` | `"native_kql_tables"` | Fixed source implementation for this package, not an arbitrary source connector. |
| `resource_names` | See below | Independent names for the schema set, Notebook, Activator, Eventhouse and KQL database. |
| `teams_delivery` | `"individual"` | Explicit direct messages. `"channel"` requires a complete native channel template. |
| `teams_recipients` | `["@me"]` | Current delegated user, or an array of approved recipient email addresses. |
| `teams_definition_file` | `null` | Channel-template JSON path, resolved relative to the configuration file. |

For example:

```json
{
  "configuration_version": "1.0",
  "scenario_id": "sales-volume",
  "environment": "fabric",
  "workspace_id": "<your-workspace-uuid>",
  "resource_prefix": "SalesDrop",
  "event_name": "Retail.Sales.VolumeAlert",
  "source_backend": "native_kql_tables",
  "resource_names": {
    "schema": "SalesDropSchema",
    "notebook": "SalesDropPublisher",
    "activator": "SalesDropActions",
    "eventhouse": "SalesDropHistory",
    "database": "SalesDropAnalytics"
  },
  "teams_delivery": "individual",
  "teams_recipients": ["@me"],
  "teams_definition_file": null
}
```

Change settings in a **new local configuration**, as in quick-start step A.
Save JSON as UTF-8. The reader accepts the optional byte-order mark produced by
Windows PowerShell 5.1; the quick start writes without one for portability.
Do not overwrite a preserved deployment configuration. For direct messages to another
approved destination, set, for example,
`$Settings.teams_recipients = @('operations@example.com')` before saving JSON.
The deployment resolves `@me` in memory; no access token belongs in configuration.

| CLI argument | Purpose |
| --- | --- |
| `--config <path>` | Selects the configuration; the default is `config.wizard.example.json`, with no workspace bound. |
| `--state-file <path>` | Selects one durable ledger for all stages of this deployment. |
| `--confirm-workspace <UUID>` | Required explicit target confirmation for write commands. Must equal the configured UUID. |
| `--seed-dummy-data` | With `deploy`/`provision`: seed only. With `run`: create and publish a fresh simulation. |
| `--with-deployment-plan` | Opt into the preview native ordering plan. Use consistently for review and provisioning. |
| `--items schema eventhouse database notebook` | Explicit foundation selection for `import-native` only. Does not select custom names. |
| `--capacity-id <UUID>` | Only for explicit `assign-capacity`; normal deployment leaves capacity unchanged. |
| `--teams-confirmed` | Operator attestation after actual Teams verification, not an automated delivery check. |

Notebook runtime parameters are different from deployment settings. The orchestrator
binds `config_json` to actual workspace, schema and database IDs, query endpoint and
operation. It supplies `fixtures_json` from the external fixture file only when seeding.
Do not paste item IDs from the deleted deployment into Notebook parameters.

## Custom artifact names {#custom-artifact-names}

Use `config.wizard.example.json`, or download **deployment configuration** from the
Sales volume dropped wizard's resource review. Edit its JSON before a new deployment:

```powershell
$Settings.resource_prefix = 'RetailDemo'
$Settings.event_name = 'Retail.Stores.VolumeDeviation'
$Settings.resource_names.schema = 'Retail Signals'
$Settings.resource_names.notebook = 'Volume Publisher'
$Settings.resource_names.activator = 'Operations Alerts'
$Settings.resource_names.eventhouse = 'Retail History'
$Settings.resource_names.database = 'Retail Analytics'
[System.IO.File]::WriteAllText(
    $Config,
    ($Settings | ConvertTo-Json -Depth 20),
    [System.Text.UTF8Encoding]::new($false)
)
python "$Scenario\deploy.py" plan --config $Config
```

Apply these edits after loading `$Settings` in quick-start A, before the first
deployment. Derive and retain the new `$State` path after selecting the namespace.
Changing only `resource_prefix` does not change item display names; edit
`resource_names` explicitly. They do not have to share a prefix.

The versioned binder copies the native definitions in memory and binds display
names, native folder names, logical identities and references together. It binds
`event_name` across the event contract, publisher, Activator and native association.
It preserves the six fields, source-table schema, threshold, graph and action.
No native source file, test fixture or `scenario.py` path needs manual renaming.

Resource names use a portable subset: 3-100 ASCII characters, starting with a
letter; letters, digits, spaces, underscores and hyphens, with no trailing spaces
or reserved Windows device names. All six item names, including the optional
`<resource_prefix>Deployment`, must be distinct ignoring case. Event names use
3-80 character Avro-qualified identifiers with dot-separated segments, not hyphens.

The history table is platform-managed and follows `event_name`. It is not a
separately named item or a `resource_names` option. Input-table names remain fixed.
The optional native plan is not a consumer or a wizard artifact.

Logical IDs are deterministic per native template and `resource_prefix`. Keep that
namespace stable throughout a deployment. For an independent instance, select a
different namespace and non-colliding item names. The ledger records the reviewed
event, names and logical IDs, and rejects changes before cloud access. A fresh
ledger does not authorize adopting or overwriting another deployment.

**Legacy compatibility:** `config.example.json` and existing versionless local
configurations retain their original names, logical IDs and prefix validation.
Continue them explicitly with their original configuration and ledger. Do not add
wizard fields to an old ledger or claim that this performs an in-place migration.

## Engineering handoff {#engineering-handoff}

This folder is the executable reference for the prototype's **Sales volume dropped**
card (`scenario_id: sales-volume`), not an implementation of the Fabric portal itself.
Other cards have their own implementation status and packages; this guide covers only sales volume.

| Handoff layer | Source and responsibility |
| --- | --- |
| UX | `fabric-events-prototypes`: Day Zero `scenarios.ts`, `DayZeroBusinessEvents.tsx` and `deploymentConfiguration.ts`. The wizard remains a stateless simulation. |
| Versioned contract | `config.schema.json`, with defaults in `config.wizard.example.json`. The prototype pins the same schema and defaults beside `sales-volume.config.json`. |
| Native implementation | `items\`: event/schema, Notebook business logic, native Activator graph, Eventhouse and KQL schema. |
| Bindings and orchestration | `native.configured_items`, CLI and portal launcher consume the same configuration. Python does not generate a replacement resource graph. |
| Optional demo inputs | `data\store-windows.json`, loaded only through an explicit seed operation. |

### Wizard field to deployment binding {#wizard-field-to-deployment-binding}

| Wizard choice | Contract field | Native effect |
| --- | --- | --- |
| Selected real workspace | `workspace_id` | Existing target UUID. Demo workspace labels are not UUIDs and are never fabricated. |
| Business event name | `event_name` | Schema ID/name, CloudEvents type, Notebook target, Activator source, association and history table/query. |
| Event schema set | `resource_names.schema` | Native schema-set display name and import path. |
| Publisher | `resource_names.notebook` | Native Notebook display name and import path. |
| Activator | `resource_names.activator` | Native consumer display name and import path. |
| Eventhouse | `resource_names.eventhouse` | Native Eventhouse identity and resolved database parent. |
| KQL database | `resource_names.database` | Database display name; Notebook and connection bind its deployed ID. |
| KQL history table | Derived, read-only | Equals the event name. Business Events creates it through its association. |
| Teams destination | `teams_delivery`, `teams_recipients`, `teams_definition_file` | Default individual `@me`; channel mode requires an actual native channel template. The prototype does not collect a destination. |

Place downloaded configuration beside `config.schema.json` for editor validation;
the CLI itself does not fetch schemas or require an extra JSON Schema dependency.
The prototype's download contains reviewed names and an unbound workspace. An
engineer must resolve the selected real workspace to its UUID and collect an
approved destination; users downloading the package fill those settings themselves.
`environment`, `resource_prefix` and optional seeding are execution settings, not
extra wizard artifact names. Never interpret a demo workspace label as permission
to deploy, or the simulation's progress messages as actual service receipts.

### Acceptance and platform boundaries {#acceptance-and-platform-boundaries}

The card and native rule retain every comparison in history, notify only when
`deviation_pct < -30`, and do not diagnose a POS failure. The publisher reads KQL
transaction/baseline inputs, not a Lakehouse. The wizard reflects that same source.
Changing the event name must update every publisher/consumer reference together.

Before handing this to a portal implementation, compare the pinned prototype JSON
with `config.wizard.example.json`; run the wizard download through the offline
CLI plan and packaged portal plan with renamed resources and event; then verify
real native import, data and consumer delivery in the chosen environment.
Local tests do not establish that the revised configuration has run in Fabric.

The full connection bridge remains workload-specific. Public Fabric foundation
import support does not mean public Fabric supports the complete automated flow.
Real source onboarding, production identity, channel-template validation, automatic
Teams delivery evidence, portal service integration and the other cards remain
explicit engineering work. The sample is not an unattended production pipeline.
No Git operation or publishing action is part of this package. The public repository
path becomes available only when the owner separately publishes these local changes.

## User-facing deployment contract {#user-facing-deployment-contract}

The deliverable is a reusable package, not a one-off Python resource generator:

1. Every item has its own native definition or executable source under `items`.
    Filters, event contracts, actions and business logic belong to those files.

2. Manual execution from a Fabric notebook and local `deploy.py` execution use the
    **same packaged source files and native Bulk Import engine**.

3. Python only orchestrates native deployment, supplies environment-specific bindings,
    handles runtime gaps and optionally initializes external dummy inputs.

4. Dummy inputs remain in `data\store-windows.json`, not embedded in deployment code.
    `provision` creates no business data. The separate `seed` command initializes KQL source tables
    without publishing events or requiring configured consumers.

5. A completed automated deployment must leave the artifacts, permissions, references,
    Eventhouse association and approved Teams action configured. Asking the operator to
    configure a missing connection afterward does **not** satisfy this contract.

Use an existing approved Fabric capacity. Track native foundation import, source-table schema application, Publish/Consume role readback, Notebook binding and Activator import in the selected deployment's ledger. Keep other deployments' configuration and ledgers separate. No new capacity or Azure resource is required.

The deployed Notebook source is normalized from Fabric's native export and checked
after import. A real `runtime-check` succeeded with Python 3.13.11 and
`notebookutils.businessEvents` available. A separate seed job verified five baselines
and 3,868 POS transactions without publishing events. The final publisher verification
used run `<historical-run-uuid>`, publishing all five findings once.
`verify` independently read five distinct findings and five received records from the
native Eventhouse history table, checking every event field.

During that validation, the native Activator definition was enabled and its strict per-event numeric condition
and Teams action have been read back. Native activation history contains exactly two
records for the final run, for stores ending `store-mx-042` and `store-mx-046`.
The existing Teams desktop session independently displayed the two actual
**Fabric Activator** notification cards with the matching run IDs, transaction counts
and deviations. No activation for the final run appears for -20, the exact -30 boundary
or zero deviation. Earlier diagnostic batches and their notifications are preserved.
Native UI inspection exposed a running rule whose saved changes were not applied.
Deployment now sets both `shouldRun` and `shouldApplyRuleOnUpdate`, and rejects a
readback that leaves either disabled. Fabric UI inspection confirmed the updated
rule is running without the pending-changes warning. A new, separately identified
batch is used for subsequent action verification; the earlier batch is not replayed.
Runtime inspection also showed false-positive activations with the retained upstream
source filter. The canonical definition now subscribes without an upstream filter
and evaluates `deviation_pct IsLessThan -30` inside the native `EventTrigger` rule.
The comparison and numeric threshold remain in `ReflexEntities.json`, not Python.
Microsoft Graph chat access is not available in the existing delegated session.
Without explicit operator attestation, CLI `verify` still honestly reports
`eventhouse_verified_teams_unverified` and exit code 2: it does not pretend to automate
the independent Teams UI check. The deployment does not request additional consent
or start a new login.

The historical Lakehouse definition remains optional and excluded from active deployment;
the scenario uses native KQL input tables and does not require a SQL endpoint.

Only when continuing an intact deployment with its default ledger, use
its original configuration, for example `config.existing.local.json`, not another deployment's `config.local.json`:

```powershell
python .\deploy.py status --config .\config.existing.local.json
python .\deploy.py verify --config .\config.existing.local.json
```

The generic setup examples below use `config.local.json` for a new installation.
Keep each environment's configuration and ledger separate. After manual item deletion,
use the clean-deployment quick start and its explicit fresh `--state-file` instead of
these continuation commands.

## Native deployment, without a repository connection {#native-deployment-without-a-repository-connection}

The `items\` folders contain the native item definitions and stable
logical IDs. No repository connection, branch, commit, push, Git credential, or provider
configuration is required. The script does not invoke Git or its Fabric integration APIs.

Fabric uses `.platform` for native item identity, including in Bulk Import. Its published
schema URL contains `gitIntegration`; that does not configure or use Git. Similarly,
`FabricGitSource` is the documented notebook text format, not a synchronization operation.

The optional native Deployment Plan orders the foundations. Attach it explicitly with
`--with-deployment-plan`; this adds `beta=true` and `options.deploymentPlan` to the import.
Without that flag, Bulk Import uses its generally available dependency handling and does
not import the optional plan. The preview plan has no publisher actions: events must not
be emitted before permissions and both consumers are ready. `plan.yml` uses the JSON subset
of YAML so offline checks need no additional package.

Native import is not transactional and Fabric does not roll back completed work. The
orchestrator saves the accepted operation URL, checks every item receipt, records partial
successes and rejects missing, failed, unexpected or partially successful results.
There is no per-item create fallback and no automatic retry of a rejected native batch.

## What is real and what is synthetic {#what-is-real-and-what-is-synthetic}

The packaged resources are real Fabric Event Schema Set, Notebook, Activator,
Eventhouse and KQL database items. No Azure resource group, new capacity, storage account,
or additional Azure subscription resource is created. Existing Fabric capacity is required
and Spark/Eventhouse execution can consume capacity.

Only the input business data is synthetic. The notebook materializes individual POS
transaction records and explicitly seeded baseline values in two real KQL source tables.
Their schemas belong to the native `DatabaseSchema.kql`, not Python. The notebook reads
those tables back, counts distinct transaction IDs per store/hour, calculates deviations,
and calls the real `notebookutils.businessEvents.publish` API. It does not directly write
findings to Eventhouse or directly call Teams. The baseline fixtures are controlled test
inputs, not claimed to be measured customer history.

```
Synthetic POS records + baseline KQL source tables
    -> Fabric Notebook
    -> Retail.Sales.VolumeAlert
        -> Activator: native per-event deviation_pct < -30 condition -> native Teams notification
        -> native Eventhouse association -> KQL history
```

The notebook publishes **every finding**, not just alert matches. The event retains the
six fields from the canonical scenario. No extra run field is added to the event schema;
synthetic store IDs contain a fresh run UUID for unambiguous receipt verification.
The two input tables are `cicd_pos_transactions` and `cicd_store_baselines`. The native
Business Event association owns a separate history table. Optional dummy initialization
uses bounded inline CSV ingestion, suitable for this small demonstration, not production
high-volume ingestion. A fresh seed writes 3,868 transaction rows and five baselines.

| Fixture | Expected | Actual | Deviation | Teams rule matches |
| --- | --- | --- | --- | --- |
| store-mx-042 | 1250 | 743 | -40.56% | Yes |
| store-mx-043 | 1250 | 1000 | -20% | No |
| store-mx-044 | 1250 | 875 | -30% | No |
| store-mx-045 | 1250 | 1250 | 0% | No |
| store-mx-046 | 1250 | 0 | -100% | Yes |

Five distinct findings should reach Eventhouse. Only two match the strict Activator filter.
A drop does not diagnose a POS failure. A successful publish or notebook job does not prove
delivery, action execution, or incident resolution.

## Files and native items {#files-and-native-items}

Paths and PowerShell commands below are relative to this scenario's folder.

| Source | Purpose |
| --- | --- |
| `deploy.bat` / `test.bat` | Independent Windows entry points: configure only, then explicitly test |
| `.deployments\` | Ignored local execution records and active test reference; never packaged |
| `card.py` | Thin shared launcher over the native CLI, retaining one configuration and ledger |
| `config.card.example.json` | Card reference defaults; select an approved target in your own local configuration |
| `config.wizard.example.json` | Unbound versioned configuration matching the Sales volume dropped card |
| `config.schema.json` | Versioned wizard/deployment contract for engineering and editor validation |
| `config.example.json` | Legacy unbound configuration for the original fixed-name templates |
| `data\store-windows.json` | Reproducible business input |
| `schema.avsc` | Canonical six-field event contract |
| `items\` | Native definitions, `.platform` identities and optional deployment plan |
| `scenario.py` | Offline access to the native notebook's pure business logic |
| `native.py` | Lossless definition packaging and explicit environment-value binding |
| `deploy.py` | Bulk Import orchestration, runtime configuration, execution and verification |
| `fabric_api.py` | Existing-user authentication, scoped Fabric transport, pagination and LRO handling |
| `connections.py` | Scoped native workload association and readback |
| `connections\` | Native association and scoped workload-token request templates |
| `portal-deploy.py` | Importable Fabric notebook entry point for the same deployment stages |
| `bundle.py` | Allowlisted source archive for portal execution, excluding local settings and ledgers |
| `queries\` | Historical analysis and run-scoped receipt checks |
| `tests\` | Offline business-logic and deployment-safety checks |

Native `.platform` files define the template identities below. Versioned
`resource_names` bind their output display names and import folders without changing
the native graphs. Legacy configurations retain the following names and must use
their matching prefix:

| Name | Item type |
| --- | --- |
| SalesVolumeCicdV1Schema | EventSchemaSet containing `Retail.Sales.VolumeAlert` |
| SalesVolumeCicdV1History | Eventhouse |
| SalesVolumeCicdV1HistoryDb | Read/write KQL database in that Eventhouse |
| SalesVolumeCicdV1Publisher | Notebook with business logic and a native parameter cell |
| SalesVolumeCicdV1Alerts | Activator (`Reflex` in the API) |
| SalesVolumeCicdV1Deployment | Optional preview Deployment Plan |

The historical `optional\SalesVolumeCicdV1Source.Lakehouse` definition is excluded from
active Bulk Import, the preview plan and the portal resource archive.

The event's KQL table is created by the native Eventhouse association, not by pretending
that a standalone table or an empty database establishes ingestion.

## Authentication and environment requirements {#authentication-and-environment-requirements}

Use Python 3.12+ and the already authenticated Azure CLI user session. There are no extra
Python dependencies. Scripts do not run `az login`, launch browsers, attach to browser
profiles, read cookies, or export access tokens.

The deployment uses Fabric, Storage and Kusto access tokens only in process memory.
Tokens, captures, profiles and cookies must never be placed in this repository or uploaded
as CI artifacts. Local configuration and ledgers are excluded from the allowlisted archive.

Required target capabilities:

1. A user-confirmed workspace on active capacity, with Contributor permissions and visibility
    of the assigned capacity SKU. Public Fabric requires F/FT or Premium P.
    Premium Per User is rejected in all profiles. Other supported capacity SKUs are reported
    explicitly and actual item creation verifies workload support.

2. Business Events and OneLake data access security available in that environment.

3. Permission to grant the deployment user Publish and Consume for this dedicated schema set.

4. A Fabric notebook runtime with Python 3.12 and `notebookutils.businessEvents`.

5. An approved Teams test destination and the native Business Event/Eventhouse association.

The example and portal entry leave the workspace unbound. Native foundation deployment uses the configured Fabric control-plane endpoint. The complete `deploy` command relies on a workload-specific association bridge, not a documented public association REST API. The scripts are an implementation reference, not a generally available customer provisioning flow; use the manual scenario when a required provisioning capability is unavailable. Workspace discovery and preflight use the selected configuration. Native workload authorization verifies the database parent and common capacity against authenticated item metadata. The workload host comes from the token response's `TargetUriHost`; a hostname guessed from the capacity GUID is not a reliable routing contract. Only trusted HTTPS Microsoft service hosts receive the scoped MwcToken. Tokens are never saved. There is no fallback to another service target after a failed request. Control and Storage hosts from another profile, including mismatched operation locations, are rejected.

Event Schema Set support and delegated scopes must be available for the selected identity
and environment. Attaching the optional plan additionally requires `Item.Execute.All`.
An expiring browser token is not an unattended deployment credential. Cloud automation
needs a separately approved, supported deployment identity and verified workload support.

## Manual execution from the Fabric portal {#manual-execution-from-the-fabric-portal}

The portal supports importing notebook `.py` files and uploading files to notebook
built-in resources. It does not generally execute an arbitrary native item JSON file.
Use the notebook entry point for staged execution of the same definitions:

1. Build the resource archive locally:

    ```powershell
    python .\bundle.py `
        --output .\sales-volume-alert.zip
    ```

2. In the approved Fabric workspace, use **Import notebook** to import
    this scenario folder's `portal-deploy.py`.

3. Open its **Resources** explorer and upload `sales-volume-alert.zip` into **Built-in**.
    The archive contains native definitions, external fixtures and allowlisted modules.
    It excludes local configuration, deployment state, credentials and unrelated files.

4. Review the parameter cell. Set `workspace_id` to the approved target. The example uses individual Teams messages to `@me`; it is not the canonical channel post.
    Start with `command = "plan"`, which makes no authentication or cloud calls.

5. To continue, select one command such as `preflight`, `deploy`, `provision`,
    `connect-eventhouse`, `enable-activator`, `runtime-check`, `seed`, `run`, `resume`,
    `status` or `verify`. For write commands,
    set `confirm_workspace_id` to the same reviewed workspace UUID, then run the notebook.
    Python submits the original definition files; it does not reconstruct the resources.

For example, begin with this parameter-cell configuration:

```python
environment = "fabric"
workspace_id = "<your-workspace-uuid>"
command = "plan"
confirm_workspace_id = ""
teams_delivery = "individual"
teams_recipients_json = '["@me"]'
teams_definition_file = ""
seed_dummy_data = False
with_deployment_plan = False
native_items_json = "[]"
capacity_id = ""
eventhouse_connected = False
config_file = ""
```

Then run these stages **one at a time**, retaining the same built-in ledger:

| Stage | Parameter changes |
| --- | --- |
| Offline review | `command = "plan"`; check the names in the uploaded ZIP. |
| Read-only target check | `command = "preflight"`. |
| Deploy only | `command = "deploy"`, `confirm_workspace_id = workspace_id`, `seed_dummy_data = False`. No dummy inputs or events. |
| Inspect the environment | Review the items, bindings and consumers in Fabric before deciding to test. |
| Explicit test: initialize and publish | `command = "run"`, retain confirmation, `seed_dummy_data = True`. Creates and publishes one fresh sample batch. |
| Verify Eventhouse | `command = "verify"`, `seed_dummy_data = False`. |
| Inspect existing resources | `command = "status"`. |
| Resume an accepted operation | `command = "resume-import"` for an import, or `"resume"` for a Notebook job. |

`teams_recipients_json` and `native_items_json` are JSON **strings**, not Python
lists. The latter is used only with `command = "import-native"`, for example
`'["schema", "eventhouse", "database", "notebook"]'`. `capacity_id` is used only
with `command = "assign-capacity"`; leave it empty for normal deployment.
`eventhouse_connected` is a legacy no-bypass flag; keep it `False`.

To consume the same JSON as the CLI or wizard download, upload it to Built-in and
set `config_file = "sales-volume.local.json"`. That file supplies **all deployment
settings**; the environment/workspace/Teams cell settings are ignored in this mode,
not silently merged. `command`, `confirm_workspace_id`, seeding and other execution
flags remain cell parameters. Review its plan before providing confirmation.
Relative channel-template paths in the uploaded JSON resolve within Built-in.

With `config_file = ""`, the cell settings override the packaged
`config.wizard.example.json`. To continue an old portal deployment, upload its
original versionless JSON and select it through `config_file` with its old ledger.
Do not silently switch a tracked legacy deployment to the new wizard defaults.
Custom names require no ZIP rebuild; both entry points bind the same native files.

The portal wrapper raises an error when the shared CLI returns code 2 for
`verify`, even when Eventhouse verification succeeded. Inspect the printed
`eventhouse_verified_teams_unverified` status and confirm actual Teams delivery
separately. There is no `teams_confirmed` parameter in the current portal cell.
If operator attestation is needed in the CLI, first save and transfer the
current portal ledger, then use `verify --state-file <saved-ledger> --teams-confirmed`
only after checking the actual messages.

Authentication uses `notebookutils.credentials.getToken` with the current notebook user,
not Azure CLI, browser cookies, interactive login or a token pasted into a cell.
The notebook supports the same stages and limitations as the CLI. In particular,
`run` refuses to publish without verifying the actual native Eventhouse connection
and the enabled Activator graph. An operator flag cannot bypass this check.
The portal entry shares the tested source package, but its runtime execution
has not yet been verified. Successful local CLI execution is not evidence of portal execution.

For repeat execution, the notebook uses `builtin/sales-volume-alert-state.json` and
prints its non-secret contents even when a command fails. Save this ledger before
ending the session. Do not rely on session filesystem writes being persisted by the UI.
Restore the ledger through built-in resources before resuming in a new session.
When switching from an existing CLI deployment, upload a copy of that deployment's
ledger under this exact resource name **before** running write commands.
When switching back, save the portal ledger locally and pass `--state-file <path>` to
the CLI. Both clients validate its workspace and prefix and refuse name-only adoption.
Never operate the two clients concurrently against the same deployment.

For a channel template, upload its complete native JSON to built-in resources and
set `teams_definition_file` to that resource's file name. Do not put credentials in it.
No channel destination is guessed or silently replaced by individual messages.

## 1. Review the offline plan {#1-review-the-offline-plan}

```powershell
python .\deploy.py plan
python -m unittest discover -s .\tests -v
```

The offline test suite requires the source checkout, including its `tests` folder
and canonical scenario documentation. Tests are not included in the portal resource ZIP.

The plan makes no authentication or cloud calls and works with `workspace_id: null`.
It prints proposed item names, schema fields, five findings and two expected alert matches.

For a new target only, copy `config.wizard.example.json` to `config.local.json` and set the
**confirmed** workspace UUID. Preserve an existing local configuration. Review it with:

```powershell
python .\deploy.py workspaces --config .\config.local.json
python .\deploy.py capacities --config .\config.local.json
python .\deploy.py plan --config .\config.local.json
python .\deploy.py preflight --config .\config.local.json
```

`preflight` only reads workspace/capacity and item names. It rejects inactive capacity and
unsupported public capacity SKUs before creating any item. It refuses to adopt existing items
with matching names. Workspace selection and plan approval remain explicit operator steps.

If an existing same-region Fabric capacity has been explicitly selected, the optional
assignment stage uses the native workspace API and `connections\workspace-assignment.json`:

```powershell
python .\deploy.py assign-capacity `
    --capacity-id '<existing-capacity-uuid>' `
    --config .\config.local.json `
    --confirm-workspace '<confirmed-workspace-uuid>'
```

This creates no capacity. It verifies access, active state and matching region, records
the request before submission, and waits for workspace and native item routing to agree.
Resume an unresolved assignment with the same command rather than posting another one.
If the user completes a different assignment in the portal, passing that actual selected
ID reconciles its completed routing without restoring the older selection or posting
another assignment. The portal entry supports `command = "assign-capacity"` and
`capacity_id`. Other stages never change the selected capacity.

## 2. Provision the foundations {#2-provision-the-foundations}

After reviewing and approving the configured plan:

```powershell
$workspaceId = '<confirmed-workspace-uuid>'
python .\deploy.py deploy `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

`deploy` provisions the native definitions, configures permissions and Notebook defaults,
creates and verifies the native Eventhouse connection, and enables the approved Activator.
This deployment invocation does not initialize inputs or publish events.
It stops at any failed stage and does not report delivery as verified.

For staged foundation provisioning without configuring consumers:

```powershell
python .\deploy.py provision `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

This sends the schema set, Eventhouse, KQL database and Notebook definitions
in **one native Bulk Import request** with `allowPairingByName: false`. Definitions and
logical IDs come from the files. Fabric performs resource creation and dependency handling.
The script checks the returned identities, native schema, Notebook source and database
parent. It uses the native KQL script to create source tables, configures schema-set
security before every role update, and merges the managed Publish/Consume role without removing
unrelated roles or members. It then binds the native Notebook's declared configuration
parameter to the actual workspace, schema, database and query endpoint, imports that
definition, and reads back its code. The deployed default operation is `publish`;
manual Notebook execution reads existing sources and does not implicitly generate dummy data.
It does not execute the notebook or enable Teams notifications.

To opt into the preview native ordering plan, first review the corresponding offline plan,
then use the same flag for the actual deployment:

```powershell
python .\deploy.py plan `
    --config .\config.local.json --with-deployment-plan
python .\deploy.py provision `
    --config .\config.local.json `
    --confirm-workspace $workspaceId --with-deployment-plan
```

The Windows launchers pass their repository-local ledger explicitly. Direct
`deploy.py` commands **without `--state-file`** retain the lower-level CLI's
profile-based default:

```
%USERPROFILE%\.copilot\state\fabric-events-cicd\<environment>\<workspace-id>\<resource-prefix>.json
```

When continuing an operation started by `deploy.bat`, always pass its printed
`--state-file` under `cicd\sales-volume-alert\.deployments` to the lower-level CLI.

Do not run concurrent deployments with the same workspace/prefix. Keep logical IDs stable.
Re-running provisioning submits the local definitions to the native engine. Foreign names
or changed ownership stop deployment. Nothing is deleted or adopted automatically.
If an accepted import is interrupted, resume that operation without resubmitting its body:

```powershell
python .\deploy.py resume-import `
    --config .\config.local.json
```

After import completion, run `provision` to complete runtime configuration and readback.
If the server never returned an operation URL, inspect the workspace before retrying.
Native deployment does not guarantee that a previously failing service operation will succeed.

To inspect the actual workspace and any saved operation without deploying anything:

```powershell
python .\deploy.py status `
    --config .\config.local.json
```

For a terminal **Failed** import, `resume-import` reports the service failure rather than
resubmitting the request. Reconcile already-created items into the local ledger with:

```powershell
python .\deploy.py reconcile-import `
    --config .\config.local.json
```

Reconciliation does not create, update or delete cloud items. For already tracked items,
an exact successful native receipt must match their current IDs, logical IDs, names,
types and ownership. Cumulative receipts are retained across selective imports.
For unproven side effects, native **Bulk Export** verifies logical identity before any
adoption, and the imported event contract is validated. Matching names alone are never
enough. It exports selectively when the workspace contains other items, or uses `All`
only when every workspace item is a verified candidate. The returned IDs must match the
requested candidates exactly. Terminal failure status and error codes are persisted so
reconciliation does not depend on the operation URL remaining available. It does not
retry deployment. Resolve the reported service problem before explicitly running
`provision` again.

## 3. Complete the native integration gates {#3-complete-the-native-integration-gates}

### Eventhouse {#eventhouse}

The `deploy` command includes this stage. To run it separately:

```powershell
python .\deploy.py connect-eventhouse `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

The native JSON template associates the exact Business Event with the child
configured KQL database, not the parent Eventhouse. The native workload bridge uses
scoped native authorization, the service-advertised route and `businessEventDataConnections`.
This is an environment-specific integration gap, not a claimed public association REST API.
Available binding fields must match the schema, source workspace, event ID and JSON
format, with no advanced alert filter. Current native list responses contain a healthy
connection summary rather than the complete binding. That summary is accepted only
for a pending managed creation or the same connection identity already saved with its
schema, database and event IDs. Unowned, ambiguous, unhealthy or non-JSON associations
are rejected. This is explicitly labeled managed-request-and-summary evidence, not
complete definition readback or delivery evidence. The history consumer must receive
every finding; `verify` checks that independently against actual event payloads.

The ledger records a creation request before sending it. An ambiguous timeout resumes
readback rather than issuing another create. Explicit rejected requests retain their
failure instead of being treated as success. A healthy managed connection summary must be accompanied by actual five-finding
ingestion evidence. A clean deployment obtains a new managed connection;
do not reuse that historical name as configuration.
No hidden Eventstream or direct history writes substitute for native ingestion.

### Teams channel: canonical scenario {#teams-channel-canonical-scenario}

The canonical scenario posts to an operations channel. The public action-authoring example
documents a `TeamsMessage` action with individual recipients, not a proven channel-post
payload. Selecting `teams_delivery: "channel"` therefore requires a complete native
channel definition, not guessed channel arguments. The example instead
selects individual messages explicitly.

Prepare a native **EventTrigger** rule for this Sales Volume Business Event with the
strict numeric condition `deviation_pct Is less than -30`, the approved Teams channel
and the `store_id`, `actual_transactions`, and `deviation_pct` context.
Export its `ReflexEntities.json` array as `teams-definition.local.json` and set
`teams_definition_file` to that relative file name. Keep the template-producing rule
disabled when using the managed deployment, to avoid duplicate notifications.

The binding step preserves the native action and destination, rebinds the Business Event source,
reconnects `SourceReference`, validates the native strict numeric condition and requires one event view,
one enabled event rule, and one Teams action. Template acceptance still requires deployment
and live action verification in the chosen environment. The importer rejects the documented
individual-recipient binding in channel mode. It otherwise preserves an opaque native action;
acceptance does not programmatically prove its channel type or successful delivery.

### Individual messages: explicit alternative {#individual-messages-explicit-alternative}

Only if approved, set `teams_delivery` to `individual` and populate `teams_recipients` with
approved test-recipient email addresses. This uses the documented public `TeamsMessage`
binding. It is not a channel post and is never selected silently.
Use `"teams_recipients": ["@me"]` to resolve the current delegated user's address in memory
when enabling the rule, without placing the address or a token in the configuration.

Then enable and read back the managed Activator:

```powershell
python .\deploy.py enable-activator `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

The individual-message graph is stored in the native `ReflexEntities.json`, initially
disabled. The script only binds the deployed event ID, source identity and approved
recipients, enables and applies the requested rule, and submits that existing definition through
Bulk Import. It does not reconstruct the graph in Python. Unresolved bindings cannot
be imported, and the offline source never contains a personal recipient address.
The native `EventDetectStep` uses `EventFieldSelector` for `deviation_pct`, followed by
`NumberValueCondition` with `op: "IsLessThan"` and numeric `threshold: -30`.
This evaluates each event, not only a transition into the low-volume state.
The source has no additional upstream filter. Saved filter text alone did not establish
correct behavior in the live test; native activation history must exclude both -20 and
the exact -30 boundary. Deployment validates the original condition rather than silently
generating or repairing it in Python.

## Optional: initialize dummy data without publishing {#optional-initialize-dummy-data-without-publishing}

After foundation deployment succeeds, initialize only the synthetic KQL sources:

```powershell
python .\deploy.py seed `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

In the portal entry point, select `command = "seed"` instead. Both paths use the
external fixture file and run the same native notebook with `operation: "seed"`.
The notebook appends POS transactions and baselines, reads them back and exits
**before** calling Business Events. This step requires neither an Activator nor
an Eventhouse association and sends no Teams notification. It records `last_seed`,
not publisher or consumer success. Resume an interrupted seed using `resume`;
do not submit another job just to poll the first one.

The lower-level CLI retains `deploy`/`provision --seed-dummy-data` for compatibility.
It seeds only after successful provisioning and never publishes, but combines two
responsibilities. The recommended instructions and `deploy.bat` do not use that option;
keep deployment separate from the explicit test.

The default `run` command publishes existing data, using `last_seed.run_id` when available,
or the latest source batch otherwise. To explicitly create and publish a fresh simulation,
use `run --seed-dummy-data`. A repeated publication is not an ingestion polling strategy.

## 4. Execute the real source and publisher {#4-execute-the-real-source-and-publisher}

Once the native association and Teams destination are ready:

```powershell
python .\deploy.py run `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

The legacy `--eventhouse-connected` flag cannot bypass actual native connection verification.
The Notebook counts persisted transaction IDs, includes zero-sales baselines, and publishes
all five demonstration findings. Seeding uses a fresh UUID and a recent completed UTC hour,
without clearing previous batches. Publishing existing data does not require dummy fixtures
as Notebook parameters.

To check the runtime without data or events:

```powershell
python .\deploy.py runtime-check `
    --config .\config.local.json `
    --confirm-workspace $workspaceId
```

This checks Python 3.12+ and the Business Events SDK. A terminal failed inspection is
archived separately because it cannot have published events. Failed terminal seed jobs
are likewise archived without claiming input initialization succeeded. Failed or
ambiguous publisher jobs remain pending to prevent blind replay. Runtime inspection, source initialization and publisher execution must each succeed independently.

The command saves the job URL before polling, checks job completion and its notebook exit
summary, and records the run UUID. A job result is only publisher evidence. If interrupted
or timed out, resume polling without submitting a duplicate publication:

```powershell
python .\deploy.py resume --config .\config.local.json
```

The native Notebook job receives configuration as `Text` parameters and external fixtures
only for explicit seeding. It uses Spark compute without a default Lakehouse dependency.
The source template remains unbound; deployment preconfigures only its parameter cell.
The notebook-specific `?beta=true` readback accepts the documented
`exitValue` and `properties.exitValue` response layouts. If evidence is unavailable,
execution stops with an explicit error and preserves the pending job for inspection.

## 5. Verify the two consumers independently {#5-verify-the-two-consumers-independently}

```powershell
python .\deploy.py verify --config .\config.local.json
```

This queries Kusto for the saved run and checks **all five payloads**, including the medium,
exact -30% boundary, normal and zero-sales findings. At-least-once duplicate deliveries are
reported as received-record counts, not mistaken for distinct store windows.
Allow ingestion time and repeat verification if necessary; do not republish as a polling
strategy. `queries\analysis.kql` contains the historical queries.

Eventhouse success alone returns exit code **2**, with `teams_unverified` in the status.
Inspect the native Activator execution/action evidence and the actual Teams destination.
Confirm that the two high findings generated notifications and the other three did not.
Only after that check may an operator attest:

```powershell
python .\deploy.py verify `
    --config .\config.local.json `
    --teams-confirmed
```

The output labels this as **operator-attested Teams evidence**, not an automated Teams
delivery check. No command treats a preview, a retained definition, or a successful publish
alone as an end-to-end pass.

## API references {#api-references}

- [Native Bulk Import Item Definitions](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/bulk-import-item-definitions)

- [Native Bulk Export Item Definitions](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/bulk-export-item-definitions)

- [Attach native deployment plans to API operations](https://learn.microsoft.com/en-us/fabric/cicd/deployment-plan/deployment-plan-automation)

- [Native notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition)

- [Run a native Notebook job with parameters](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/background-jobs/run-on-demand-notebook)

- [REST APIs and CI/CD overview](https://learn.microsoft.com/en-us/fabric/real-time-hub/automate-event-consumption-rest-api-cicd)

- [Create Business events and data access roles](https://learn.microsoft.com/en-us/fabric/real-time-hub/create-business-events-rest-api)

- [Publish Business events](https://learn.microsoft.com/en-us/fabric/real-time-hub/publish-business-events)

- [Consume with Activator](https://learn.microsoft.com/en-us/fabric/real-time-hub/consume-events-activator-rest-api)

- [Filter definitions](https://learn.microsoft.com/en-us/fabric/real-time-hub/configure-event-filters-rest-api)

- [Native Eventhouse integration](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-eventhouse)

- [Microsoft's action-authoring reference](https://github.com/microsoft/skills-for-fabric/blob/6c11ad58c25992e5d1435ce7cd80d217d5598a31/plugins/fabric-skills/skills/activator-cli/references/authoring/action-types.md)

- [Kusto authentication](https://learn.microsoft.com/en-us/kusto/api/rest/authentication?view=microsoft-fabric)

- [Import notebooks and manage built-in resources](https://learn.microsoft.com/en-us/fabric/data-engineering/how-to-use-notebook)

- [NotebookUtils token audiences](https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-credentials)

- [Fabric capacity throttling and smoothing](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)

## Continue reading

- [Full scenario and manual setup](../sales-volume-alert.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
