# Discount approval required

[Back to the scenario](../discount-approval-routing.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/discount-approval-routing)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\discount-approval-routing`; for an extracted package, open that scenario folder instead.

**Reading this guide on the documentation site?** Obtain the complete [source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/discount-approval-routing) before running commands. The site publishes this complete runbook and links to the implementation, not the complete executable folder. Select an approved target with the wizard configuration and use the same local configuration for deployment, the portal-subscription attestation and testing.

This **deployment guide is the documentation source of truth** for `cicd\discount-approval-routing`. It implements prototype card ID `discount` with a native User Data Function publisher and independent Activator, Eventstream and Eventhouse consumers.

Historical validation records remain in the ignored scenario-local `.deployments` folder. They do not imply that test artifacts remain deployed or certify a new target or custom resource names. Verify the complete flow independently for each new deployment as described below.

**Automation boundary:** the package automates the UDF, schema, Activator and Eventhouse. Its Eventstream subscription still requires the portal because the documented public source-creation contract is unavailable. `deploy.bat` exits **3** while that step remains; it never creates an empty substitute stream. Native topology readback returned the running BusinessEvents source and its exact binding after portal creation. That readback does not establish portable API-based source creation.

External sales application -> native UDF policy evaluation -> exception Business Event -> independent Activator, Eventstream and Eventhouse consumers

## Card contract and policy {#contract}

| Condition | Result | Event |
| --- | --- | --- |
| Discount <= 10% and margin >= 25% | none | No event. |
| Discount > 10% and <= 15%, margin >= 25% | manager | One exception event. |
| Discount > 15% or margin < 25% | executive | One exception event. Executive takes priority. |

A 12% discount with 24% margin requires executive approval. Exactly 15% with exactly 25% margin requires manager approval. The function returns its decision synchronously and publishes only manager or executive exceptions. This scenario neither chooses an approver nor approves, rejects or executes a deal.

| Wizard role | Default artifact | Responsibility |
| --- | --- | --- |
| schema | DiscountSchema | One v1 Business Event. |
| publisher | DiscountPublisher | Native UDF with a bound EventDefinition connection and exact commercial policy. |
| activator | DiscountActions | On every exception, send Teams the deal ID, approval level and reason. |
| eventstream | DiscountRouting | Independent Business Event subscription, configured in the portal. |
| eventhouse | DiscountHistory | Independent durable exception history. |
| database | DiscountAnalytics | Child KQL database with the event-named managed table. |

Event: `Sales.Deals.DiscountApprovalRequired`. Table: `['Sales.Deals.DiscountApprovalRequired']`. Exact fields: `deal_id:string`, `account_name:string`, `region:string`, `deal_value:double`, `requested_discount_percent:double`, `estimated_margin_percent:double`, `approval_level:string`, `approval_reason:string`, `event_time:string`. No evaluation ID or undeclared field is added to v1.

## Copy, paste, and prepare the native environment {#run}

Prerequisites: Windows, Python 3.10 or later, Azure CLI with an already authenticated delegated user, and an approved workspace on an active accessible Fabric capacity. No script opens a login window, creates or reassigns capacity, or uses Git integration. Complete automatic Eventhouse association relies on a workload-specific bridge, not a documented public provisioning contract. The scripts are an implementation reference, not a generally available customer provisioning flow. Use the linked manual scenario for customer setup where provisioning is unsupported.

Select your approved workspace before running either launcher. Copy `config.wizard.example.json` to `discount.local.json`, set `workspace_id` to that workspace's UUID and review every resource name and Teams destination. Pass the same `--config .\discount.local.json` to deployment and testing. The checked-in card configuration is a reference, not authorization to deploy to someone else's workspace. Individual Teams delivery uses `@me` unless approved recipients are selected.

```powershell
Set-Location '.\cicd\discount-approval-routing'
$configFile = '.\discount.local.json'
if (Test-Path -LiteralPath $configFile) { throw 'Preserve the existing file or choose a new local configuration name.' }
Copy-Item .\config.wizard.example.json $configFile
$config = Get-Content $configFile -Raw | ConvertFrom-Json
$config.environment = 'fabric'
$config.workspace_id = '<your-workspace-uuid>'
$config | ConvertTo-Json -Depth 20 | Set-Content $configFile -Encoding utf8
python .\deploy.py plan --config $configFile
.\deploy.bat --config .\discount.local.json
```

Deploy imports native foundations, enables Business Event data access, grants the delegated identity Publish/Consume, creates and verifies the native Eventhouse association, enables the native Teams consumer, and deploys the connected UDF. It reads back native definitions and checks the database parent. It does not invoke the UDF, publish test events, load fixtures, or run the test.

**Expected exit: 3** until you complete the portal subscription below. This is a known capability boundary, not permission to rerun deploy: every new `deploy.bat` invocation starts a separate fresh deployment and existing names still collide.

Deploy scenarios sequentially within the same workspace to avoid Fabric's concurrent CI/CD lock. Fabric may regenerate data-access role IDs and add its SDK library during import; the package pins that SDK and checks semantic permissions. Service export omits generated `resources/functions.json` metadata: this limitation is recorded in the ledger, while connection and Python source are read back and actual invocations independently verify execution. The native association owns the generated KQL history schema.

## Required native Eventstream portal step {#manual}

1. Open the same target workspace and Real-Time hub > Business events. Select the event with the exact deployed type `Sales.Deals.DiscountApprovalRequired` and schema set `DiscountSchema`, or your configured names.

2. Use the event's streaming/consume action to create an Eventstream in this workspace named exactly `DiscountRouting`, or `resource_names.eventstream`. Select the Business Event itself as the source, with no exception-level filter: both manager and executive events must arrive.

3. Publish the native Eventstream configuration. Inspect its source binding against the deployed workspace, schema set and event name, and ensure the source is running. Copy the Eventstream item ID from its Fabric URL.

4. Record your explicit configuration attestation against this deployment, not an inferred item with a matching name:

```powershell
$configFile = '.\discount.local.json'
$config = Get-Content $configFile -Raw | ConvertFrom-Json
$active = ".\.deployments\$($config.environment)\$($config.workspace_id)\$($config.resource_prefix).active.json"
$ledger = (Get-Content $active -Raw | ConvertFrom-Json).state_file
$eventstreamId = '<paste-the-created-Eventstream-item-UUID>'
python .\deploy.py acknowledge-eventstream --config $configFile --state-file "$ledger" --eventstream-id $eventstreamId --confirm-workspace $config.workspace_id
```

The command checks the explicitly supplied item's workspace, type and exact reviewed name. Source configuration remains **operator-attested, not API-verified**. It never adopts another artifact by name or modifies the selected Eventstream. The ledger can then become `configured_delivery_unverified`; this does not assert that Eventstream received events.

In the UDF, inspect Home > Manage connections. The schema set should already be connected with its configured name as the alias. For a manual native setup: Add connection, find the Event Schema Set by name, select it, retain the alias, and save. The alias must match both `@udf.connection` and `resources/functions.json`. Native definition metadata uses `artifactType: "EventDefinition"`, not `EventSchemaSet`.

The documented creation limitation is explicit in [Consume events with Eventstream REST APIs](https://learn.microsoft.com/en-us/fabric/real-time-hub/consume-events-with-event-stream-rest-api). A source returned by native topology readback is useful readback evidence, not proof that the same contract can be imported in every environment. Do not invent a source definition, reuse another source type, route through a Notebook, or claim an unbound stream satisfies this card.

## Inject wizard names and deployment parameters {#names}

The card's downloaded JSON matches [config.wizard.example.json](https://github.com/microsoft/fabric-events/blob/main/cicd/discount-approval-routing/config.wizard.example.json) and intentionally has no live workspace ID. Change the target, prefix, event name and all six resource names explicitly:

```powershell
Copy-Item .\config.wizard.example.json .\discount.local.json
$config = Get-Content .\discount.local.json -Raw | ConvertFrom-Json
$config.environment = 'fabric'
$config.workspace_id = '<your-workspace-uuid>'
$config.resource_prefix = 'DiscountV2'
$config.event_name = 'Sales.Deals.DiscountApprovalRequiredV2'
$config.resource_names.schema = 'DiscountV2Schema'
$config.resource_names.publisher = 'DiscountV2Publisher'
$config.resource_names.activator = 'DiscountV2Actions'
$config.resource_names.eventstream = 'DiscountV2Routing'
$config.resource_names.eventhouse = 'DiscountV2History'
$config.resource_names.database = 'DiscountV2Analytics'
$config | ConvertTo-Json -Depth 20 | Set-Content .\discount.local.json -Encoding utf8
python .\deploy.py plan --config .\discount.local.json
.\deploy.bat --config .\discount.local.json
```

For the portal attestation block, set `$configFile = '.\discount.local.json'` and create the reviewed `DiscountV2Routing` stream against the V2 event. Changing only `resource_prefix` does not rename artifacts. Prefix isolates logical identities and ledger names; `resource_names` controls actual names.

Names must be distinct ignoring case. Eventstream names require 3-100 letters, digits or underscores; other resource names also allow spaces and hyphens. Event names require 3-80 Avro-qualified characters. Event renaming binds the schema, UDF constant, Activator and history association together; the manual portal subscription must use that same identity. The contract is [config.schema.json](https://github.com/microsoft/fabric-events/blob/main/cicd/discount-approval-routing/config.schema.json). These packages intentionally support individual Teams delivery only.

## Explicit end-to-end test {#test}

After inspecting every resource and acknowledging the portal subscription, invoke the native UDF with one new external batch:

```powershell
.\test.bat --config .\discount.local.json
```

For custom configuration:

```powershell
.\test.bat --config .\discount.local.json
```

Eight synthetic proposals cover within-policy and exact seller boundaries, the lower/upper manager boundaries, high discount, low margin, executive precedence, and both executive reasons. Exactly **six exception events** are expected: two manager and four executive. The two within-policy proposals must return `none` without publication. All fixture account names are synthetic.

Python does not evaluate commercial policy. It invokes the native UDF, checks each actual returned decision against the fixture oracle, and independently queries Eventhouse for all nine fields. The native function owns numeric/text validation, strict policy boundaries, reason generation and event publication. The UDF takes camelCase parameters; `eventTime` is an ISO 8601 UTC string, validated and normalized inside the function. This transport-friendly parameter differs from the earlier sample's datetime argument; the Business Event schema and policy are unchanged.

Check all six real Teams messages and inspect the Eventstream source's event preview for the exact saved `cicd-<run-UUID>-*` deal IDs, levels and reasons. Eventhouse receipt and UDF success do not establish Eventstream receipt or Teams delivery. Verify both missing and unexpected cases before attesting:

```powershell
.\test.bat --config .\discount.local.json --verify-only
.\test.bat --config .\discount.local.json --verify-only --teams-confirmed --eventstream-confirmed
```

Add `--config .\discount.local.json` when needed. These flags record independent **operator attestations**, not automatic service telemetry. They require `--verify-only`, which never invokes the UDF again. Exit **2** preserves incomplete evidence; only verified history, a completed input batch and both attestations yield exit 0. Negative-case absence is limited to the current observation.

## Fresh attempts, local records and resume

Every deployment gets a fresh UUID ledger beside the launchers under `.deployments\<environment>\<workspace-id>\`. No previous deployment is inspected, archived or reused. Existing resource names are not adopted or overwritten. If you delete the Fabric artifacts yourself, a new deployment can proceed even while old local records remain.

`Discount.active.json` selects the exact ledger for testing. A failed new attempt never selects an older successful deployment. Both launchers accept an explicit `--state-file`; deployment requires a previously unused path. Local records are ignored by the repository and excluded from ZIPs, as are credentials and local configs. An unresolved native operation must be resumed explicitly, using its current ledger:

```powershell
$configFile = '.\discount.local.json'
$config = Get-Content $configFile -Raw | ConvertFrom-Json
$active = ".\.deployments\$($config.environment)\$($config.workspace_id)\$($config.resource_prefix).active.json"
$ledger = (Get-Content $active -Raw | ConvertFrom-Json).state_file
python .\deploy.py resume --config $configFile --state-file "$ledger" --confirm-workspace $config.workspace_id
```

A request with no returned polling Location cannot be blindly resubmitted. Ambiguous UDF execution is saved before invocation and blocks automatic republication; inspect the saved attempt before explicitly choosing `test.bat --new-run`. This starts a different uniquely identified batch, not a retry of the old one.

## Engineering handoff and optional package {#engineering}

Prototype: `discount`. Native source: [function_app.py](https://github.com/microsoft/fabric-events/blob/main/cicd/discount-approval-routing/items/DiscountPublisher.UserDataFunction/function_app.py). Connection contract: [definition.json](https://github.com/microsoft/fabric-events/blob/main/cicd/discount-approval-routing/items/DiscountPublisher.UserDataFunction/definition.json). External cases: [test-cases.json](https://github.com/microsoft/fabric-events/blob/main/cicd/discount-approval-routing/data/test-cases.json). Guide: [Discount approval routing](../discount-approval-routing.md).

This is a self-contained executable scenario folder with independent deploy/test launchers, config/schema, native `items`, `connections`, `data` and ignored `.deployments`. Documentation lives only on this scenario subpage, not in the folder or ZIP. Authentication and Eventhouse association helpers are reused unchanged from scenario 1; orchestration modules match the payment package. No Notebook substitutes for the UDF or Eventstream.

**Engineering gap to close:** native creation/export of the Eventstream Business Event source is not exposed by the documented public definition API. Until that platform contract exists, the portal step cannot honestly be replaced with a preconfigured downloadable source definition. Keep the card requirement and this gap visible when handing the package to the portal team.

A ZIP is optional for download/sharing. It includes only this scenario under `cicd/discount-approval-routing/`, excluding local configs, credentials, ledgers and tests. Fabric does not directly deploy this ZIP, and this package has no launcher notebook.

```powershell
python .\bundle.py --output '.\discount-approval-routing.zip'
python -m unittest discover -s .\tests -v
```

Native API references: [connected UDF creation and invocation](https://learn.microsoft.com/en-us/fabric/real-time-hub/publish-business-events), [UDF definition parts](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/user-data-function-definition). Local validation and definition readback alone are not live delivery evidence; all three independent consumers must be observed separately for the current run.

Exit codes: 0 = requested phase complete; 1 = error or history mismatch; 2 = history verified with required evidence missing; 3 = native artifacts prepared but the required portal subscription remains. Deploy and test never call each other.

## Continue reading

- [Full scenario and manual setup](../discount-approval-routing.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
