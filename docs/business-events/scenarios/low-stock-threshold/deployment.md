Fabric Business Events / Prototype card: inventory

# Inventory at risk

[Back to the scenario](../low-stock-threshold.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/low-stock-threshold)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\low-stock-threshold`; for an extracted package, open that scenario folder instead.

The [source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/low-stock-threshold) contains the native publisher, deployment runtime and consumer definitions. This site publishes the complete runbook and links to the implementation. Packages contain executable sources and configuration, not documentation copies.

**Native reference validated with an explicit individual test destination.** Three external findings produced three complete seven-field Eventhouse payloads and three independently observed Teams cards with all six inventory context fields. Native definitions, permissions, history association and the UDF connection were read back independently. This is not a claim that the canonical inventory-planning channel was configured or validated.

## Card contract

Warehouse management system -> native User Data Function -> `Retail.Inventory.LowStockThreshold` -> independent Activator and Eventhouse consumers.

The WMS already detects the shortage. The function normalizes the finding and publishes its seven fields; it does not compare stock to the threshold, monitor warehouses, create purchase orders or mutate stock. Callers must supply actual warehouse findings, not arbitrary stock samples. Activator must use **On each event**, not a duplicate stock detector.

Default review names: `LowStockSchema`, `LowStockPublisher`, `LowStockActions`, `LowStockHistory` and `LowStockAnalytics`. The managed history table follows the event name: `['Retail.Inventory.LowStockThreshold']`. These names match the card; they do not identify deployed resources.

## Implemented source

[schema.avsc](https://github.com/microsoft/fabric-events/blob/main/cicd/low-stock-threshold/schema.avsc) preserves the card's exact v1 field names and Avro types. [scenario.json](https://github.com/microsoft/fabric-events/blob/main/cicd/low-stock-threshold/scenario.json) records the source, resources, action and remaining verification. [function_app.py](https://github.com/microsoft/fabric-events/blob/main/cicd/low-stock-threshold/items/LowStockPublisher.UserDataFunction/function_app.py), its native definition and function metadata form the publisher. The source and native metadata use camelCase invocation parameters mapped to the snake_case event fields.

Counts must fit a signed 32-bit Avro int. Strings are trimmed and timestamps must be timezone-aware UTC. `detectedAt` preserves the warehouse's reported detection time after UTC normalization. Publish failures propagate; an invocation result alone is never proof of consumer delivery. Three external warehouse findings are in [data/test-cases.json](https://github.com/microsoft/fabric-events/blob/main/cicd/low-stock-threshold/data/test-cases.json); deployment must not load them.

## Connection and remaining native bindings

The checked-in publisher contains deployment tokens: `${event_name}`, `${schema_name}`, `${schema_id}` and `${workspace_id}`. This package's runtime binds them to the selected configuration and actual imported identities. Do not import the templates unchanged or pass the scenario manifest as deployment configuration.

In Fabric, the UDF connection must target the actual Event Schema Set. For manual setup, open the UDF, select **Home > Manage connections > Add connection**, search for the selected schema set and connect it. The generated alias follows the schema-set name, `LowStockSchema` by default; it must match the connection decorator and native definition after binding. Save the connection. The runtime imports this native connection and checks the exported definition against the bound schema, workspace and alias.

The runtime imports the schema-set artifact, grants the selected delegated publisher/consumer identity access, associates Eventhouse, and imports the native Activator and UDF definitions. Root-level `deploy.bat` only prepares these resources. `test.bat` separately injects external findings, retains intent/evidence under ignored `.deployments`, and verifies full history payloads. Every deployment creates a fresh ledger, with no silent adoption of prior artifacts. Actual notifications still require independent observation; a successful invocation or history query is insufficient.

The canonical guide uses the inventory-planning Teams channel. `teams_delivery: "channel"` requires an exported complete native channel rule in `teams_definition_file`. Export the rule for this event with **On each event**, all six inventory context fields and its actual approved channel binding. Paths resolve relative to the selected configuration file. A missing export stops deployment before creation; the runtime never substitutes a direct message. An explicitly configured `teams_delivery: "individual"` test is a separate alternative and does not establish channel delivery. Enable the prototype's configuration download only after the executable package and its delivery evidence satisfy the reviewed contract.

## Separate deployment and test commands

This is not a generally available customer provisioning flow: automatic Eventhouse association still depends on service capabilities that are not exposed by the general public provisioning interface. A portable configuration does not remove that limitation.

Copy `config.card.example.json` to a local configuration file, select the approved workspace and names, and configure the native channel export. Keep local destinations and exports outside the website source and distribution archives.

```powershell
Set-Location '.\cicd\low-stock-threshold'
$configFile = '.\config.local.json'
python deploy.py plan --config $configFile
.\deploy.bat --config $configFile
```

Deployment imports and reads back the schema, native UDF connection, native per-event Activator rule and history association. It does not invoke the publisher or load any fixture. The fresh ledger path is printed and saved in the scenario-local ignored `.deployments` directory.

Only after deployment succeeds, explicitly submit the external findings:

```powershell
.\test.bat --config $configFile
```

The initial test can return exit code `2` after Eventhouse verification because notifications remain unverified. Observe the actual destination, confirm the exact run-specific product identifiers and all inventory context fields, then verify the saved batch without publishing again:

```powershell
.\test.bat --config $configFile --verify-only --teams-confirmed
```

`--teams-confirmed` records an operator attestation, not an independent automated Teams query. Retain the actual observation evidence beside the ledger. For individual test delivery, the attestation applies only to those configured recipients, not the canonical channel.

An unresolved operation must be resumed explicitly using the exact ledger; deployment never adopts an older attempt:

```powershell
python deploy.py resume --config $configFile --state-file '<exact-ledger.json>' --confirm-workspace '<approved-workspace-id>'
```

Do not blindly republish after an ambiguous test. Inspect its saved invocation records and downstream evidence first. `test.bat --new-run` explicitly creates a different batch after that review.

## Offline publisher tests

```powershell
Set-Location '.\cicd\low-stock-threshold'
python -m unittest discover -s tests -p "test_*.py"
```

These tests execute the actual publisher source against an isolated SDK/client double: exact payloads, native metadata, normalization, invalid inputs, upstream-owned detection and surfaced publish failures. Deployment tests additionally check real bound definitions, channel-to-DM rejection, separation of deployment from publication, the exact native invocation parameters, and complete payload comparisons. They do not authenticate, create Fabric items, publish live events or establish Teams/Eventhouse delivery.

Reference: [canonical inventory scenario](../low-stock-threshold.md).

## Continue reading

- [Full scenario and manual setup](../low-stock-threshold.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
