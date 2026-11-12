# Revenue Below Target: Workbook

[Business context and architecture](../revenue-below-target.md) | [Download package](../../../assets/downloads/revenue-below-target-cicd.zip)

Help the revenue team investigate when a region falls more than 15% below target. Prepare the monitoring view and an investigation response, then check when a new investigation is needed and when an existing shortfall should not generate another one. Create the Real-Time Dashboard, review the preconfigured publisher and activate it before testing; the [Business context](../revenue-below-target.md) holds the canonical architecture.

<span id="deployment-architecture"></span>
<span id="revenue-below-target-deployment-guide"></span>

## Resources and coverage

| Resource | Default item names | What the package prepares |
| --- | --- | --- |
| Business Event and Event Schema Set | `RevenueSchema`, event `Sales.Revenue.BelowTarget` | Public preview; creates the six-field v1 schema and delegated Publish/Consume permissions |
| Source Eventhouse and KQL database | `RevenueData`, `RevenueSource` | Creates the source infrastructure and typed `RevenuePerformance` snapshot table; source inputs are entered manually |
| KQL Queryset | `RevenueQueries` | Creates Revenue observations and Current revenue tabs over the same source query |
| Real-Time Dashboard | `RevenueMonitor` | Create manually with Pin to dashboard from Current revenue; not created by the package |
| Activator (Business Event publication) | `RevenuePublisher` | Prepares the region-period monitoring rule and six event fields; review and start the rule manually |
| Activator (Data Pipeline) | `RevenueActions`, rule `Prepare revenue investigation` | Starts the investigation pipeline whenever a new below-target event arrives |
| Data Pipeline and Notebook | `RevenueInvestigation`, `RevenueInvestigationNotebook` | Passes the six event fields and pipeline run ID to prepare the analysis |
| Investigation Lakehouse | `RevenueInvestigations` | Creates `Files/RevenueInvestigations` to hold saved analyses ready for review |
| Optional Business Event history in Eventhouse | Not configured by this package | Independent enrichment; the required source Eventhouse is not Business Event history |

## Before you start

Use **Python 3.10+**, **Azure CLI** and **PowerShell on Windows**, an active supported Fabric capacity and item-creation permission.

## 1. Configure your environment

Download **`revenue-below-target-cicd.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\revenue-below-target` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\revenue-below-target'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` to block cloud operations if you do not accept preview APIs.

The package creates nine items; you create the Dashboard in Step 3 using the tenth configured name. Choose unused names so this exercise stays separate from earlier deployments. Its business outcome is a saved investigation rather than a Teams notification.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

After deployment, confirm that the nine Fabric items in the table appear in your workspace. The investigation consumer is enabled, while the revenue publisher remains stopped. **Continue with section 3** to create the Dashboard, review the prepared response and activate the publisher before testing. Deployment does not submit revenue snapshots, publish events or run the pipeline.

## 3. Complete manual setup before testing

Use your deployed item names if you changed the defaults.

In step 3.1, enter `region`, `period_start`, `revenue` and `target`. The source command timestamps the snapshot with `observed_at`; the query calculates `variance_pct` and formats `detected_at`. You do not enter those two calculated event values yourself.

### 3.1. KQL data and Real-Time Dashboard

1. Open [Microsoft Fabric](https://app.fabric.microsoft.com).
2. In the left navigation, select **Workspaces**, then open the workspace used in your configuration.
3. Open **RevenueQueries**, of type **KQL Queryset**. Confirm that its data source is **RevenueSource**, the KQL database in **RevenueData**.
4. In a new query tab, paste and run the command from `sample-input.kql`:

    ```kusto
    .set-or-append RevenuePerformance <|
    print region = 'West', period_start = '2026-11-01', revenue = 90000.0, target = 100000.0, observed_at = now()
    ```

5. Run **Revenue observations**. Confirm the six event fields, real `observed_at`, stable **Composite key** and baseline variance `-10`, not `-0.10`. Zero and negative targets produce null variance. This query returns all source states, not only findings.
6. Run **Current revenue**. It uses the same calculation and selects the most recent snapshot per region and reporting period.
7. Select **Pin to dashboard**, choose **In a new dashboard**, enter **RevenueMonitor** and tile name **Current revenue by region**, then select **Create**.
8. Open **RevenueMonitor** and confirm its baseline values. Enable refresh as appropriate. Its refresh setting controls the operational view, not Activator evaluation.

    | Field | Expected value |
    | --- | --- |
    | `region` | `West` |
    | `period_start` | `2026-11-01` |
    | `revenue` | `90000` |
    | `target` | `100000` |
    | `variance_pct` | `-10.00`, not `-0.10` |
    | `detected_at` | A UTC snapshot string, for example `20261108T185300Z` |

See [creating a Real-Time Dashboard from a Queryset](https://learn.microsoft.com/fabric/real-time-intelligence/tutorial-5-create-dashboard). The publisher monitors **Revenue observations**, not the current-state Dashboard query: collapsing the source to its most recent row can hide intermediate threshold states.

### 3.2. Check the prepared event-driven response

1. Return to your workspace and open **RevenueInvestigations**, of type **Lakehouse**.
2. Under **Files**, open **RevenueInvestigations**. This folder is empty before the first real event-driven investigation.
3. Return to the workspace and open **RevenueInvestigation**, of type **Data pipeline**. Its notebook activity targets **RevenueInvestigationNotebook**.
4. Select that activity and inspect its **Settings** and base parameters. The six event fields come from corresponding pipeline parameters; `pipeline_run_id` comes from `@pipeline().RunId`, not a supplied event field or a static value.
5. Open **RevenueActions**, of type **Activator**, and inspect **Prepare revenue investigation**. It uses **On each value**, with a **Data Pipeline** action targeting **RevenueInvestigation** and dynamic mappings for all six original event fields.

The response is connected for you. Check its settings without starting the pipeline: a below-target event will start it during the exercise. The analysis uses the event's revenue figures directly, without needing Business Event history.

### 3.3. Publisher rule: check the preconfigured publication

`RevenuePublisher` contains the native KQL connection, the **Revenue reporting period** object and the stopped **Revenue below target** rule. The deployment has already configured its condition, Business Event destination and six dynamic mappings. Do not create another alert from the Queryset or Dashboard when using this package.

1. Return to the workspace and open the existing **RevenuePublisher** item, of type **Activator**, not `RevenueActions`.
2. Switch Activator to **Edit**. Keep the baseline from section 3.1: revenue `90000`, target `100000`, variance `-10`.
3. In the left **Explorer**, expand **Revenue native source contract → Revenue reporting period** and select **Revenue below target**, identified by a lightning-bolt icon.

    Select the rule, not **Revenue native source event**, **Revenue period observations** or the `variance_pct` property. Selecting a source event shows **Live feed**, not the rule editor. Selecting the rule opens its **Definition** pane and rule controls.

4. Confirm that the rule is stopped: **Start** is enabled and **Stop** is disabled. If it is running, select **Stop** before continuing.
5. In **Definition**, check **Attribute: variance_pct**, **Condition: Decreases below** and **Value: `-15`**, not `-0.15`. Some editor layouts label the same crossing condition **Becomes → Less than → `-15`**. **Occurrence** is **Every time the condition is met**.
6. In **Definition → Action**, confirm **Publish a business event**, with `Sales.Revenue.BelowTarget` in `RevenueSchema`.
7. Select **View action** to open **Edit the action**. If the button is labeled **Edit action**, select that instead. Inspect the six preconfigured dynamic mappings without replacing them:

    | Event field | Source value |
    | --- | --- |
    | `region` | `region` |
    | `period_start` | `period_start` |
    | `revenue` | `revenue` |
    | `target` | `target` |
    | `variance_pct` | `variance_pct` |
    | `detected_at` | `detected_at` |

    Do not use fixed sample values or parse the combined **Composite key** to fill the event.

8. Close the action editor without changing the mappings, using its top-right **Close (X)** button, not **Apply**. Leave the publication rule stopped until section 3.4.

See [creating Activator rules and properties](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-create-activators) and [Activator Business Event publishing](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-activator).

**Keep the reporting period consistent.** The **Composite key** identifies the region and reporting period as a two-value array; changing revenue must not create a new monitored period. While the rule is stopped, open **Revenue period observations** and append a fresh baseline. Check that the key contains `West` and `2026-11-01`, without amounts or timestamps, and that all six observed properties match section 3.1. Use these observations to check received data; **Live feed** shows only arrivals after it was opened.

### 3.4. Activation: only after the publisher is ready

1. Check the publication rule, six field mappings and region-period key before starting the monitor.
2. Open the publication rule in **RevenuePublisher** and select **Start**.
3. Append a fresh `90000/100000` snapshot. Wait until **Revenue period observations** shows the new timestamp and `-10` baseline before continuing.

`RevenueActions` is already enabled and bound to `RevenueInvestigation`. Do not start the pipeline manually.

## 4. Test the flow from KQL

**Start with the monitor ready and the region-period key checked.** The exercise follows one reporting period as revenue falls below target, recovers and falls again.

1. With **RevenuePublisher** running, check that **Revenue period observations** shows revenue `90000`, target `100000` and variance `-10`.
2. Return to **RevenueQueries** and run the snapshot command from section 3.1 for each state below, editing only `revenue`. Keep target `100000`, region `West` and period `2026-11-01`.
3. Add each snapshot separately with `observed_at = now()`. **Wait until Activator shows that state before moving to the next row.** Keep all snapshots and the unfiltered monitoring query so recovery and repeated shortfalls can be distinguished. A Dashboard refresh or waiting a fixed time is not a substitute for checking Activator.

Follow this sequence in order:

| Source state | Revenue | Variance | Expected new Business Event / pipeline execution |
| --- | --- | --- | --- |
| Baseline | `90000` | `-10` | None |
| Exact boundary | `85000` | `-15` | None |
| First crossing below | `80000` | `-20` | One |
| Remaining below | `78000` | `-22` | None |
| Recovery above | `90000` | `-10` | None |
| Second crossing below | `80000` | `-20` | One |

Each crossing should publish `Sales.Revenue.BelowTarget` and automatically prepare an investigation. Allow time for monitoring, delivery and Spark startup. Leave the pipeline to start from the event rather than running it manually.

### Evaluation criteria

For each crossing, check the investigation before continuing, using the checks below:

- Only the two crossings below `-15` should create events and start the pipeline. The exact boundary and a further decline while already below target should not create another investigation.
- Open **RevenueInvestigations → Files → RevenueInvestigations**. Expect two new JSON analyses with status `ready_for_review`, each linked by `pipeline_run_id` to its completed pipeline run. Check the six event values and UTC `investigated_at`. For `80000/100000`, the analysis should show variance `-20%`, shortfall `20000` and required lift `25%`.
- Each analysis describes the revenue position that triggered it. A retry within the same pipeline run keeps the existing file; a separate pipeline run has its own file, so count incidents by the source events as well as saved files.
- Finally, try zero and negative targets. Expect KQL null variance and no event or pipeline response during two observed active source polls. These are invalid targets, not revenue shortfalls requiring investigation.

The outcome is analysis ready for the revenue team's review. It is not an approved recovery plan or evidence that revenue has recovered.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Sales.Revenue.BelowTarget']
    | order by ingestion_time() desc
    | take 50
    ```

    ```kusto
    ['Sales.Revenue.BelowTarget']
    | where ingestion_time() > ago(30d)
    | summarize ReceivedFindings = count(), WorstVariancePct = min(variance_pct)
        by region, period_start
    | order by ReceivedFindings desc, WorstVariancePct asc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `RevenueSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Sales.Revenue.BelowTarget",
      "fields": [
        {
          "name": "region",
          "type": "string",
          "doc": "Sales region whose revenue is below target"
        },
        {
          "name": "period_start",
          "type": "string",
          "doc": "Start of the reporting period in ISO 8601 format"
        },
        {
          "name": "revenue",
          "type": "double",
          "doc": "Revenue for the reporting period in the source currency"
        },
        {
          "name": "target",
          "type": "double",
          "doc": "Positive revenue target for the same reporting period and currency"
        },
        {
          "name": "variance_pct",
          "type": "double",
          "doc": "100 * (revenue - target) / target; negative values mean below target"
        },
        {
          "name": "detected_at",
          "type": "string",
          "doc": "UTC revenue snapshot from observed_at, ISO 8601 basic format yyyyMMddTHHmmssZ; not Activator ingestion time"
        }
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```kusto
    .create-merge table RevenuePerformance (region:string, period_start:string, revenue:real, target:real, observed_at:datetime)
    ```

    ```kusto
    declare query_parameters(startTime:datetime = datetime(1970-01-01), endTime:datetime = datetime(9999-12-31));
    RevenuePerformance
    | where observed_at >= startTime and observed_at <= endTime
    | extend variance_pct = iff(target > 0, 100.0 * (revenue - target) / target, real(null))
    | extend detected_at = strcat(format_datetime(observed_at, 'yyyyMMdd'), 'T', format_datetime(observed_at, 'HHmmss'), 'Z')
    | project observed_at, ['Composite key'] = tostring(pack_array(region, period_start)),
        region, period_start, revenue, target, variance_pct, detected_at
    | sort by observed_at asc
    ```

    ```kusto
    | summarize arg_max(observed_at, *) by region, period_start
    | project region, period_start, revenue, target, variance_pct, detected_at
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    | Parameter | Pipeline type | Notebook type | Value in the notebook activity |
    |---|---|---|---|
    | `region` | String | string | `@pipeline().parameters.region` |
    | `period_start` | String | string | `@pipeline().parameters.period_start` |
    | `revenue` | Float | float | `@pipeline().parameters.revenue` |
    | `target` | Float | float | `@pipeline().parameters.target` |
    | `variance_pct` | Float | float | `@pipeline().parameters.variance_pct` |
    | `detected_at` | String | string | `@pipeline().parameters.detected_at` |
    | `pipeline_run_id` | Not an event input | string | `@pipeline().RunId` |

    | Output | Calculation | Meaning |
    |---|---|---|
    | `shortfall` | `target - revenue` | Revenue needed to reach the target, in the source currency |
    | `revenue_lift_pct` | `100 * (target - revenue) / revenue` when `revenue > 0`; otherwise null | Growth from current revenue needed to reach the target, not the original variance percentage |

## Further reading

- [Real-Time Hub, Business Events, Create](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="configure-the-publisher"></span>
<span id="consumer-1-prepare-investigation-analysis"></span>
<span id="deploy-this-scenario"></span>
<span id="optional-consumer-analyze-shortfalls-in-eventhouse"></span>
<span id="prepare-the-source-and-dashboard"></span>
<span id="revenue-below-target"></span>
<span id="shared-resource-names"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-activator-on-a-kql-query"></span>
<span id="step-3-consumers-activator-data-pipeline-and-optional-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
