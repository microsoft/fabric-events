# Customer Loyalty Milestone: Workbook

[Business context and architecture](../customer-loyalty-milestone.md) | [Download package](../../../assets/downloads/customer-loyalty-milestone.zip) | [Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/customer-loyalty-milestone)

Prepare campaign candidates when a customer crosses the Gold spending threshold. Activator publishes the milestone; Eventstream adds campaign and benefit context, excludes inactive campaigns and saves the useful results as Lakehouse Delta tables. This exercise prepares marketing work, not an issued reward or a delivered message.

<span id="customer-loyalty-milestone-deployment-guide"></span>
<span id="deployment-architecture"></span>

## Resources and coverage

| Resource | Default item name | What the package prepares |
|---|---|---|
| Event Schema Set | `RetailCustomers` | Six-field `Retail.Customer.MilestoneReached` event and delegated Publish/Consume permissions |
| Source Eventhouse and KQL database | `LoyaltyData`, `LoyaltySource` | Empty `CustomerSpendSnapshots` table for customer-wide balances in USD |
| KQL Queryset | `LoyaltyQueries` | Spend observations and Current customer spend tabs |
| Lakehouse | `LoyaltyCampaigns` | Lakehouse with schemas disabled; campaign catalog and output tables are populated later |
| Catalog setup Notebook | `LoyaltyCatalogSetup` | Prepared catalog and destination; run it once manually before connecting reference data |
| Activator publisher | `LoyaltyPublisher` | Create the customer-keyed threshold rule and Business Event action manually |
| Eventstream | `LoyaltyCampaignStream` | Create the native subscription, reference join, filters and Delta destinations manually |
| Independent Business Event history | Not configured by this package | The source Eventhouse is not a milestone-history consumer |

The package creates **six Fabric items**. The two configured names for Activator and Eventstream are reserved for your manual setup; those connections are not created by deployment. The [public Eventstream definition API does not expose Business Events sources](https://learn.microsoft.com/fabric/real-time-hub/consume-events-with-event-stream-rest-api).

## Before you start

Use **Python 3.10+**, **Azure CLI** and **PowerShell on Windows**, an active supported Fabric capacity, and Contributor or higher access to the workspace. Use that workspace for the schema set and Eventstream so they share the required region. You also need permission to run Spark Notebooks and read and write the Lakehouse.

## 1. Configure your environment

Download **`customer-loyalty-milestone.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\customer-loyalty-milestone` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\customer-loyalty-milestone'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` to block cloud operations if you do not accept preview APIs.

Choose unused names for all eight roles. Six belong to the prepared resources; use the other two when creating Activator and Eventstream.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Open the configured workspace and find the six prepared items listed above. Deployment leaves the source table empty and does not run the catalog Notebook, publish milestones, create campaign output records or configure the two manual connections. Continue with Step 3 before testing.

## 3. Connect the milestone to campaign preparation

Use your configured names wherever they differ from the defaults.

### 3.1. Make the campaign catalog available

1. Open `LoyaltyCatalogSetup`, of type **Notebook**, in the deployed workspace.
2. Attach `LoyaltyCampaigns` as its default Lakehouse and select **Run all** once.
3. In `LoyaltyCampaigns → Tables`, open `LoyaltyCampaignCatalog`. Confirm the two rows: Gold at `store-west` has active campaign `gold-west-welcome`; Gold at `store-east` has inactive campaign `gold-east-welcome`. Both use benefit code `GOLD_WELCOME`.
4. Keep `store-north` unmapped for this exercise. Each tier/store pair must have exactly one catalog row.

The Notebook creates only the reference catalog. It does not publish milestones or write candidates. It will not overwrite an existing catalog when rerun.

### 3.2. Create the milestone publisher

Open the prepared `LoyaltyQueries` Queryset against `LoyaltySource`. The source table and both query tabs already exist; do not recreate them. Use your configured names wherever they differ from the defaults.

1. If the query needs sample rows to expose its fields, run only the **baseline** block in Step 4. Do not run the increased balances yet.
2. In **Spend observations**, select **Set alert** and create `LoyaltyPublisher`. In Activator, use `observed_at` as the event time and **only `customer_id` as the object identity**. Store, name, spend and timestamp are changing properties of that customer.
3. Monitor `total_spend`. Choose **Becomes greater than**, or **Increases above** in the rule editor, with value `5000`. Do not choose a condition that fires for every observation already above `5000`.
4. Configure **Publish a business event**, select `RetailCustomers` and `Retail.Customer.MilestoneReached`, and map:

    | Event field | Source property |
    |---|---|
    | `customer_id` | `customer_id` |
    | `customer_name` | `customer_name` |
    | `milestone_tier` | `milestone_tier` |
    | `total_spend` | `total_spend` |
    | `store_id` | `store_id` |
    | `detected_at` | `detected_at` |

5. Save the rule. Keep it stopped until the campaign flow below is ready.

Keep balances at or below `5000` in the source. They allow Activator to recognize a later crossing. A changing store must not create a new object; this is one customer's program-wide milestone.

See [publishing Business Events from Activator](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-activator).

### 3.3. Add campaign enrichment and Delta destinations

1. In **Real-Time Hub**, open `Retail.Customer.MilestoneReached` under `RetailCustomers`. Select **Add consumer → Eventstream**, use the same workspace, name it `LoyaltyCampaignStream` and select **Connect**. Use the native Business Event source, not a custom endpoint.
2. Open the Eventstream in **Edit** mode. Add a reference source with **Add source → Reference data sources → Select from OneLake**. Choose `LoyaltyCampaigns → Tables → LoyaltyCampaignCatalog`, select all five catalog columns and save.
3. Add a **Join** operation. Use the live Business Event stream on the left and the reference catalog on the right. Choose **Left outer**, with these two field pairs:

    | Milestone field | Catalog field |
    |---|---|
    | `milestone_tier` | `catalog_tier` |
    | `store_id` | `catalog_store_id` |

4. From the joined stream, create the two branches below. Select the milestone's payload properties if a field picker shows them under `data`; do not use CloudEvents envelope fields. In **Manage Fields**, use the flat output column names shown below and check the resulting fields in preview before adding the destinations.

    | Branch | Filter | Manage Fields: keep only | Lakehouse table |
    |---|---|---|---|
    | Campaign candidates | `campaign_id` is not null **and** `is_active` equals boolean `true` | `customer_id`, `milestone_tier`, `total_spend`, `store_id`, `detected_at`, `campaign_id`, `benefit_code` | `LoyaltyCampaignCandidates` |
    | Campaign mapping review | `campaign_id` is null | `customer_id`, `milestone_tier`, `total_spend`, `store_id`, `detected_at` | `LoyaltyCampaignReview` |

5. After each **Manage Fields** operation, select **Add destination → Lakehouse**, choose `LoyaltyCampaigns`, select **JSON** as the input data format and create the corresponding **Delta table**. For the small demonstration, use **Minimum rows: 1** and **Maximum duration: 1 minute**. These settings favor visible sample results over efficient production-sized files.
6. Publish the Eventstream. Confirm that the reference catalog is loaded and that both destination branches are running before starting the milestone exercise. Select a reference refresh schedule appropriate to later campaign changes; an edited catalog does not take effect until Eventstream refreshes it.

An inactive match is intentionally excluded from both outputs: it has a campaign mapping, but that campaign is not accepting candidates. An unmatched milestone goes to review so the team can decide whether to add a mapping. `customer_name` is not copied into either output because campaign preparation needs the customer ID, not another copy of their name.

This connection is manual because the [public Eventstream definition API does not expose Business Events sources](https://learn.microsoft.com/fabric/real-time-hub/consume-events-with-event-stream-rest-api). See [Eventstream transformations](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/route-events-based-on-content) and [Lakehouse destinations](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/add-destination-lakehouse).

Then start the rule in `LoyaltyPublisher`. No consumer Activator, Power Automate flow or CRM connection is needed.

## 4. Run the balances and inspect campaign results

Start `LoyaltyPublisher` only after the Eventstream and both destinations are ready. Run each source block separately, keeping time to observe the results between blocks. Use a fresh prefix instead of `loyalty-demo-001` throughout all three blocks when repeating the exercise.

### Establish the starting balances

In `LoyaltyQueries`, open a new tab connected to `LoyaltySource` and run:

```kusto
.set-or-append CustomerSpendSnapshots <|
let sample_id = 'loyalty-demo-001';
datatable(customer_suffix:string, store_id:string, total_spend:real)
[
    'eligible', 'store-east', 4900.0,
    'inactive', 'store-west', 4900.0,
    'unmatched', 'store-west', 4900.0,
    'boundary', 'store-west', 4900.0,
    'below', 'store-west', 4900.0
]
| project customer_id = strcat(sample_id, '-', customer_suffix),
    customer_name = strcat('Sample ', customer_suffix), total_spend, store_id, observed_at = now()
```

In `LoyaltyPublisher`, select the source event's **Live feed** and the customer object's properties. Wait until Activator has observed `4900` for all five IDs. Seeing the rows in the KQL database alone does not establish that the rule has seen them. No milestone or campaign candidate should exist for these starting balances.

### Introduce the new balances

After confirming those starting values in Activator, run:

```kusto
.set-or-append CustomerSpendSnapshots <|
let sample_id = 'loyalty-demo-001';
datatable(customer_suffix:string, store_id:string, total_spend:real)
[
    'eligible', 'store-west', 5100.0,
    'inactive', 'store-east', 5100.0,
    'unmatched', 'store-north', 5100.0,
    'boundary', 'store-west', 5000.0,
    'below', 'store-west', 4999.0
]
| project customer_id = strcat(sample_id, '-', customer_suffix),
    customer_name = strcat('Sample ', customer_suffix), total_spend, store_id, observed_at = now()
```

| Customer suffix | New balance | What you should see |
|---|---|---|
| `eligible` | `5100` | Gold milestone for `store-west`; candidate with campaign `gold-west-welcome` and benefit `GOLD_WELCOME` |
| `inactive` | `5100` | Gold milestone for `store-east`; inactive campaign excluded from both output tables |
| `unmatched` | `5100` | Gold milestone for `store-north`; record in `LoyaltyCampaignReview`, not the candidate table |
| `boundary` | `5000` | No milestone and no output; the rule requires spending **above** `5000` |
| `below` | `4999` | No milestone and no output |

The first three customers change stores while keeping their original customer IDs. Their balances remain customer-wide. In Business Event **Data preview** and Eventstream, look for those three milestone customer IDs with tier `gold` and spend `5100`. In the joined preview, compare the active, inactive and missing catalog results.

### Confirm that continued spending is not another crossing

After observing the eligible customer's `5100` milestone, run:

```kusto
.set-or-append CustomerSpendSnapshots <|
let sample_id = 'loyalty-demo-001';
print customer_id = strcat(sample_id, '-eligible'), customer_name = 'Sample eligible',
    total_spend = 5200.0, store_id = 'store-west', observed_at = now()
```

Wait until Activator shows `5200` for that same customer. This observation should not publish another milestone: the customer was already above `5000`. The campaign candidate still describes the original `5100` crossing.

### Evaluation criteria

Open `LoyaltyCampaigns → SQL analytics endpoint`. Allow time for Lakehouse ingestion and table visibility, then inspect the two Delta tables for this exercise:

```sql
SELECT customer_id, milestone_tier, total_spend, store_id,
       detected_at, campaign_id, benefit_code
FROM dbo.LoyaltyCampaignCandidates
WHERE customer_id LIKE 'loyalty-demo-001-%';

SELECT customer_id, milestone_tier, total_spend, store_id, detected_at
FROM dbo.LoyaltyCampaignReview
WHERE customer_id LIKE 'loyalty-demo-001-%';
```

Expect one distinct candidate customer, `loyalty-demo-001-eligible`, and one distinct review customer, `loyalty-demo-001-unmatched`. Both retain the `gold`, `5100`, store and observation time from their milestone. No inactive, boundary or below-threshold customer belongs in the candidate table. Continued spending at `5200` should not create a new milestone or replace the original candidate.

The tables are append outputs, not an exactly-once audience or a reward ledger. Repeated deliveries can create duplicate rows; compare customer IDs and milestone context rather than treating every row as another customer. Before outreach, select one candidate per customer, tier and campaign, check contact permission and confirm benefit eligibility.

These records do not show reward issuance, membership updates, message delivery or improved retention.

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `RetailCustomers`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Retail.Customer.MilestoneReached",
      "fields": [
        {"name": "customer_id", "type": "string", "doc": "Unique identifier of the customer"},
        {"name": "customer_name", "type": "string", "doc": "Full name of the customer"},
        {"name": "milestone_tier", "type": "string", "doc": "Loyalty tier reached: silver, gold, or platinum"},
        {"name": "total_spend", "type": "float", "doc": "Cumulative spend amount that triggered this milestone"},
        {"name": "store_id", "type": "string", "doc": "Identifier of the store where the milestone was reached"},
        {"name": "detected_at", "type": "string", "doc": "Timestamp when the milestone was detected, ISO 8601 format"}
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```kusto
    .create-merge table CustomerSpendSnapshots (customer_id:string, customer_name:string, total_spend:real, store_id:string, observed_at:datetime)
    ```

    ```kusto
    declare query_parameters(startTime:datetime = datetime(1970-01-01), endTime:datetime = datetime(9999-12-31));
    CustomerSpendSnapshots
    | where observed_at >= startTime and observed_at <= endTime
    | extend milestone_tier = 'gold',
        detected_at = strcat(format_datetime(observed_at, 'yyyy-MM-dd'), 'T', format_datetime(observed_at, 'HH:mm:ss.fff'), 'Z')
    | project observed_at, customer_id, customer_name, milestone_tier, total_spend, store_id, detected_at
    | sort by observed_at asc
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    ```python
    catalog_rows = [
        ("gold", "store-west", "gold-west-welcome", "GOLD_WELCOME", True),
        ("gold", "store-east", "gold-east-welcome", "GOLD_WELCOME", False),
    ]
    catalog_schema = (
        "catalog_tier string, catalog_store_id string, campaign_id string, "
        "benefit_code string, is_active boolean"
    )
    (
        spark.createDataFrame(catalog_rows, catalog_schema)
        .write.format("delta").mode("errorifexists")
        .option("delta.columnMapping.mode", "none")
        .option("delta.enableDeletionVectors", "false")
        .save("Tables/LoyaltyCampaignCatalog")
    )
    ```

## Further reading

- [Business Events requirements](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-overview)
- [creating a Business Event](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [Lakehouse reference-data source](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/enrich-events-with-reference-data)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="configure-the-milestone-rule"></span>
<span id="connect-and-shape-the-campaign-flow"></span>
<span id="customer-loyalty-milestone"></span>
<span id="prepare-the-campaign-catalog"></span>
<span id="prepare-the-spending-view"></span>
<span id="step-1-define-the-loyalty-milestone"></span>
<span id="step-2-recognize-the-customer-milestone"></span>
<span id="step-3-prepare-campaign-candidates-in-eventstream"></span>
<span id="step-4-observe-which-customers-need-campaign-preparation"></span>
<span id="why-this-matters"></span>
