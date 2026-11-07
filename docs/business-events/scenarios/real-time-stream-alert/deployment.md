# High-value payment detected

[Back to the scenario](../real-time-stream-alert.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/high-value-payment)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\high-value-payment`; for an extracted package, open that scenario folder instead.

**Reading this guide on the documentation site?** Obtain the complete [source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/high-value-payment) before running commands. The site publishes this complete runbook and links to the implementation, not the complete executable folder. For another approved target, use the wizard configuration and the same local configuration file for both launchers, as described below.

This **deployment guide is the documentation source of truth** for `cicd\high-value-payment`. The package matches the Day Zero card with scenario ID `high-value-payment`, not the numbering of the public scenario catalog.

Historical validation records remain in the ignored scenario-local `.deployments` folder. They do not imply that test artifacts remain deployed or certify a new target or custom resource names. Verify the complete flow independently for each new deployment as described below.

External payment inputs -> Eventstream filter (`amount > 5000`) -> Business Event -> independent Activator and Eventhouse consumers

## Card contract {#contract}

Eventstream owns filtering and schema-field mapping. Python injects raw dummy inputs only during the explicit test; it does not select high-value payments or publish Business Events itself. Every flagged payment retains its original currency. There is no exchange-rate conversion, fraud determination, payment blocking, or review workflow.

| Wizard role | Default artifact | Responsibility |
| --- | --- | --- |
| schema | PaymentReviewSchema | One Business Event and its v1 Avro schema. |
| publisher | PaymentReviewPublisher | Native Eventstream: custom endpoint, strict Filter, six-field ManageFields mapping, Business Events destination. |
| activator | PaymentReviewActions | On each received event, notify the approved individual Teams recipient. |
| eventhouse | PaymentReviewHistory | Independent history consumer. Does not ingest the unfiltered input stream. |
| database | PaymentReviewAnalytics | Child KQL database with the platform-managed event-named history table. |

Event name: `Retail.Payment.HighValueTransaction`. Table: `['Retail.Payment.HighValueTransaction']`. Fields are exactly `transaction_id:string`, `payment_method:string`, `amount:float`, `currency:string`, `store_id:string`, `processed_at:string`. A payment of exactly 5,000 produces no event.

## Copy, paste, and deploy the base card {#run}

Prerequisites: Windows, Python 3.10 or later, Azure CLI with an already authenticated delegated user, and an approved workspace on an active accessible Fabric capacity. No script opens a login window, creates or reassigns capacity, or uses Git integration. Complete automatic Eventhouse association relies on a workload-specific bridge, not a documented public provisioning contract. The scripts are an implementation reference, not a generally available customer provisioning flow. Use the linked manual scenario for customer setup where provisioning is unsupported.

Select your approved workspace before running either launcher. Copy `config.wizard.example.json` to `payment.local.json`, set `workspace_id` to that workspace's UUID and review every resource name and Teams destination. Pass the same `--config .\payment.local.json` to deployment and testing. The checked-in card configuration is a reference, not authorization to deploy to someone else's workspace. Individual Teams delivery uses `@me` unless approved recipients are selected.

```powershell
Set-Location '.\cicd\high-value-payment'
$configFile = '.\payment.local.json'
if (Test-Path -LiteralPath $configFile) { throw 'Preserve the existing file or choose a new local configuration name.' }
Copy-Item .\config.wizard.example.json $configFile
$config = Get-Content $configFile -Raw | ConvertFrom-Json
$config.environment = 'fabric'
$config.workspace_id = '<your-workspace-uuid>'
$config | ConvertTo-Json -Depth 20 | Set-Content $configFile -Encoding utf8
python .\deploy.py plan --config $configFile
.\deploy.bat --config .\payment.local.json
```

`deploy.bat` prints the offline plan, checks capacity and current name collisions, imports checked-in native definitions, enables Business Event data access, grants the existing delegated user Publish/Consume, creates and verifies the native Eventhouse association, binds and enables the native Activator rule, imports the Eventstream publisher, and reads back definitions and topology. A successful deployment has no dummy data and no test publication. Inspect all artifacts before testing.

Deploy and test are independent. Deployment does not install the Event Hubs SDK, inject data, invoke a publisher, or call `test.bat`. Test never creates or updates Fabric items.

Run native deployments sequentially within a workspace: Fabric rejects concurrent CI/CD operations. Readback treats Eventstream graph-node order as non-semantic while checking node identities, references, filter and mappings. The native association creates the managed KQL table, so its exported schema is not expected to remain an empty initialization script. Permission checks compare policy and membership, not regenerated server role IDs.

## Inject names and target parameters {#names}

Download the reviewed configuration from the matching prototype card, or copy the wizard example. The prototype deliberately leaves `workspace_id` null; its demo workspace names are not live identities.

```powershell
Copy-Item .\config.wizard.example.json .\payment.local.json
$config = Get-Content .\payment.local.json -Raw | ConvertFrom-Json
$config.environment = 'fabric'
$config.workspace_id = '<your-workspace-uuid>'
$config.resource_prefix = 'PaymentReviewV2'
$config.event_name = 'Retail.Payment.HighValueTransactionV2'
$config.resource_names.schema = 'PaymentReviewV2Schema'
$config.resource_names.publisher = 'PaymentReviewV2Publisher'
$config.resource_names.activator = 'PaymentReviewV2Actions'
$config.resource_names.eventhouse = 'PaymentReviewV2History'
$config.resource_names.database = 'PaymentReviewV2Analytics'
$config | ConvertTo-Json -Depth 20 | Set-Content .\payment.local.json -Encoding utf8
python .\deploy.py plan --config .\payment.local.json
.\deploy.bat --config .\payment.local.json
```

`resource_names` controls actual artifact names; `resource_prefix` isolates logical identities and local records and does not implicitly rename artifacts. Names must be distinct ignoring case. Eventstream names use 3-100 letters, digits or underscores and start with a letter. Other artifact names also permit spaces and hyphens. Event names use 3-80 Avro-qualified characters, without hyphens. Renaming the event rebinds the Avro record, destination, Activator source and managed history association together.

The schema is [config.schema.json](https://github.com/microsoft/fabric-events/blob/main/cicd/high-value-payment/config.schema.json); runtime validation also rejects duplicate names and unsupported backends. These card packages support individual Teams delivery only. Set approved addresses in `teams_recipients` or retain `@me`. Channel delivery is not silently replaced by a direct message.

## Run the flow only when you choose {#test}

Install the test-only custom-endpoint dependency, then run one new dummy batch:

```powershell
python -m pip install -r .\requirements.txt
.\test.bat --config .\payment.local.json
```

With the custom configuration, use the same file for both phases:

```powershell
.\test.bat --config .\payment.local.json
```

The six external fixtures include values below 5,000, exact boundaries in USD and EUR, and values above 5,000 in USD, EUR and JPY. All six enter the native Eventstream unchanged except for unique test IDs and a fresh UTC timestamp. Exactly **three Business Events and three matching Teams notifications** are expected. Eventhouse verification checks all six fields, rejects missing events, unexpected negative-case events and payload variants, and tolerates repeated identical at-least-once deliveries.

Exit **2** means Eventhouse payloads were verified but required independent delivery evidence is incomplete. Check the actual three Teams cards and the absence of cards for negative cases; verification alone never asserts that Teams delivered them.

```powershell
.\test.bat --config .\payment.local.json --verify-only
.\test.bat --config .\payment.local.json --verify-only --teams-confirmed
```

Add `--config .\payment.local.json` to these commands when using custom names. `--teams-confirmed` is an explicit operator attestation, not automatic telemetry. It requires `--verify-only`, which never republishes. Negative-case absence is only evidence from the current observation, not an unlimited future-delivery guarantee.

## Fresh deployments and local records

Every `deploy.bat` invocation creates a new UUID ledger under `.deployments\<environment>\<workspace-id>\`. It does not read, recover, archive or reuse previous deployments. Existing Fabric names still cause an error; remove only the intended old artifacts yourself or choose new names. Deleting Fabric artifacts does not require deleting local ledgers.

`PaymentReview.active.json` selects the exact current ledger for `test.bat`. A failed new deployment does not fall back to an older successful one. Local configs, credentials and `.deployments` must remain outside source control and distributions. No access keys are written to the ledger or printed.

Both launchers accept `--state-file`; deployment requires a previously unused path, while testing requires the exact existing deployment. If one native import times out, explicitly resume that same attempt using the ledger path printed by deployment:

```powershell
$configFile = '.\payment.local.json'
$config = Get-Content $configFile -Raw | ConvertFrom-Json
$active = ".\.deployments\$($config.environment)\$($config.workspace_id)\$($config.resource_prefix).active.json"
$ledger = (Get-Content $active -Raw | ConvertFrom-Json).state_file
python .\deploy.py resume --config $configFile --state-file "$ledger" --confirm-workspace $config.workspace_id
```

Resume is explicit and is not the behavior of `deploy.bat`. An ambiguous request without a returned operation Location cannot be automatically resubmitted or adopted by name. An ambiguous test likewise requires inspection before an explicitly requested `test.bat --new-run`; it never retries publication automatically.

## Engineering handoff and native ownership {#engineering}

Prototype: `high-value-payment`. Configuration: [config.wizard.example.json](https://github.com/microsoft/fabric-events/blob/main/cicd/high-value-payment/config.wizard.example.json). Native graph: [eventstream.json](https://github.com/microsoft/fabric-events/blob/main/cicd/high-value-payment/items/PaymentReviewPublisher.Eventstream/eventstream.json). External fixtures: [test-cases.json](https://github.com/microsoft/fabric-events/blob/main/cicd/high-value-payment/data/test-cases.json). Scenario guide: [High-value payment stream](../real-time-stream-alert.md).

Native files own the schema, filter, field mapping, publication destination, consumers and Teams action. Python only binds IDs and reviewed names, orchestrates supported native imports, applies the existing native Eventhouse association bridge, injects explicit dummy inputs, and checks delivery evidence. The custom endpoint belongs to the publisher, not a second cloud resource. Replacing it with a production source requires an explicit native topology change and its own access/network validation.

The executable scenario folder is self-contained: `deploy.bat`, `test.bat`, runtime modules, configs, `items`, `connections`, `data`, and ignored `.deployments` sit together. Documentation lives only on this scenario subpage, not in the folder or ZIP. Transport and association helpers are reused unchanged from scenario 1. The orchestration modules are identical to the discount package so each folder can be copied independently.

A ZIP is optional for sharing, not required for local launchers. It contains only this scenario beneath `cicd/high-value-payment/`; no local ledgers, credentials, tests or local configs. Fabric does not directly import this ZIP, and no launcher notebook is supplied for this package.

```powershell
python .\bundle.py --output '.\high-value-payment.zip'
python -m unittest discover -s .\tests -v
```

References: [BusinessEvents destination contract](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-destination), [native Eventstream API](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/eventstream-rest-api), [custom endpoint connection API](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-source-connection). Definition readback and a Running topology prove configuration, not downstream delivery.

Exit codes: 0 = requested phase complete; 1 = error or unverified history; 2 = history verified with remaining evidence required. No Git integration or interactive authentication is used.

## Continue reading

- [Full scenario and manual setup](../real-time-stream-alert.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
