# Sales Volume Alert: Workbook {#sales-volume-alert-deployment-and-testing}

[Business context and architecture](../sales-volume-alert.md) | [Download package](../../../assets/downloads/sales-volume-alert.zip)

Help store operations spot a significant sales-volume drop. Set up the sample, run it once and check that Teams highlights only the stores that need attention.

<span id="deployment-architecture"></span>

## Resources and coverage

| Resource | What the package sets up | What to expect |
| --- | --- | --- |
| Event Schema Set | `SalesDropSchema` with `Retail.Sales.VolumeAlert` | Public preview API; requires a signed-in user and explicit preview consent |
| Source Eventhouse and KQL database | `SalesDropSource` and `SalesDropSourceDb`, with store baselines and transaction records | Supported as the **sales data source for the Notebook** |
| Notebook | `SalesDropPublisher` compares sales volume and publishes findings | Requires a Fabric runtime with **Python 3.12+ and Business Events support**; the test loads sample source data |
| Activator (Teams) | `SalesDropActions` sends individual alerts to your signed-in user (`@me`) | Native Teams action; inspect the delivered alerts yourself |
| Business Event history in Eventhouse | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |

## Before you start

Have **Python 3.10+**, **Azure CLI**, and **PowerShell on Windows** available. You need a Fabric workspace on an active supported capacity and permission to create its items.

Keep your tenant ID and workspace ID ready. The workspace ID is the UUID in your Fabric workspace URL.

## 1. Configure your environment

Download **`sales-volume-alert.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\sales-volume-alert` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\sales-volume-alert'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` to block cloud operations if you do not accept preview APIs.

Sign in to Teams with the same account as Azure CLI. Alerts target your signed-in user (`@me`); inspect the **Fabric Activator** conversation after running the sample. No Graph permission or chat ID is needed for this exercise.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

When the command finishes successfully, open your workspace. You should see the schema, source Eventhouse and database, publisher Notebook, and Activator listed above. Your resource names may differ if you changed them.

Deployment prepares the resources; it does not publish sample events yet.

## 3. Run the sample and check Teams

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

The sample compares six stores with their expected sales volume. Wait for the Notebook run to finish, then check the new messages in Teams. Two stores should need attention; the other four should not generate an alert.

### Evaluation criteria

- Confirm that the Notebook run completed. Use the store IDs in its printed summary to find this exercise's results.
- In the source Eventhouse, compare each store's expected and recorded sales. In Activator, check that only a deviation **below -30%** calls for an alert.
- In Teams, expect **two alerts** for stores from that run ending in `-high` and `-high-zero`. Compare expected/actual transactions and deviation: `1250/743` gives `-40.56%`; `100/0` gives `-100%`.
- The exact **-30% boundary**, `-medium`, `-low` and `-normal` stores must not produce an alert. Check new messages for these stores rather than earlier exercises.

Allow time for the Notebook and Teams delivery. The exercise finishes with two alerts ready for investigation; it does not determine why sales fell. Run the sample once and inspect the results without submitting it again. Optional history is separate from this exercise; see the creation-time conditions below.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Retail.Sales.VolumeAlert']
    | order by ingestion_time() desc
    | take 50
    ```

    ```kusto
    ['Retail.Sales.VolumeAlert']
    | where ingestion_time() > ago(7d)
    | where deviation_pct < -30
    | summarize HighSeverityFindings = count(),
        WorstDeviationPct = min(deviation_pct) by store_id
    | order by HighSeverityFindings desc, WorstDeviationPct asc
    ```

    ```kusto
    ['Retail.Sales.VolumeAlert']
    | where ingestion_time() > ago(7d)
    | summarize Findings = count() by severity, ReceivedHour = bin(ingestion_time(), 1h)
    | order by ReceivedHour asc
    ```

    ```kusto
    ['Retail.Sales.VolumeAlert']
    | where ingestion_time() > ago(1h)
    | where store_id == "store-mx-042"
    | project ReceivedAt = ingestion_time(), store_id,
        expected_transactions, actual_transactions, deviation_pct, severity
    | order by ReceivedAt desc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `SalesDropSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Retail.Sales.VolumeAlert",
      "fields": [
        {
          "name": "store_id",
          "type": "string",
          "doc": "Unique identifier of the store reporting the alert"
        },
        {
          "name": "expected_transactions",
          "type": "int",
          "doc": "Expected number of transactions based on historical average"
        },
        {
          "name": "actual_transactions",
          "type": "int",
          "doc": "Actual transactions recorded in the current monitoring window"
        },
        {
          "name": "deviation_pct",
          "type": "float",
          "doc": "Percentage deviation from expected volume. Negative means below expected."
        },
        {
          "name": "window_start",
          "type": "string",
          "doc": "Start of the monitoring window in ISO 8601 format"
        },
        {
          "name": "severity",
          "type": "string",
          "doc": "Alert severity level: low, medium, high"
        }
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    # Publish a Retail.Sales.VolumeAlert Business Event from a Fabric Notebook
    # notebookutils reference: https://learn.microsoft.com/fabric/data-engineering/microsoft-spark-utilities

    WORKSPACE_NAME = "<your-workspace-name>"
    SCHEMA_SET_NAME = "<your-schema-set-name>"
    EVENT_NAME = 'Retail.Sales.VolumeAlert'

    # Calculate deviation for a store
    store_id = "store-mx-042"
    expected = 1250
    actual = 743
    deviation_pct = round(((actual - expected) / expected) * 100, 2)
    severity = 'high' if deviation_pct < -30 else 'medium' if deviation_pct < -15 else 'low'

    # Build the event payload
    payload = {
        'store_id': store_id,
        'expected_transactions': expected,
        'actual_transactions': actual,
        'deviation_pct': deviation_pct,
        'window_start': "2024-06-22T09:00:00Z",
        'severity': severity
    }

    # Publish to Business Events
    notebookutils.businessEvents.publish(
        eventSchemaSetWorkspace=WORKSPACE_NAME,
        eventSchemaSet=SCHEMA_SET_NAME,
        eventTypeName=EVENT_NAME,
        eventData=payload,
        dataVersion='v1'
    )
    ```

## Further reading

- [Real-Time Hub → Business Events → Create](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [Fabric item action documentation](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-trigger-fabric-items)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="configure-the-rule"></span>
<span id="consumer-1-notify-operations-with-activator-teams"></span>
<span id="consumer-2-analyze-recurring-drops-in-eventhouse"></span>
<span id="create-the-notebook"></span>
<span id="deploy-this-scenario"></span>
<span id="notebook-code"></span>
<span id="sales-volume-alert"></span>
<span id="set-up-the-alert-rule"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-notebook"></span>
<span id="step-3-consumers-activator-teams-and-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
<span id="what-you-will-explore"></span>
