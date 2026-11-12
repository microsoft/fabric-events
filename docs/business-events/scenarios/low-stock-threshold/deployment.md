# Low Stock Threshold: Workbook

[Business context and architecture](../low-stock-threshold.md) | [Download package](../../../assets/downloads/low-stock-threshold.zip)

Turn three low-stock findings into replenishment requests ready for approval. The sample represents findings from a warehouse management system (WMS); the team decides how to fulfill each request.

<span id="deployment-architecture"></span>
<span id="low-stock-threshold-quick-deployment"></span>

## Resources and coverage

| Resource | What the package sets up | What to expect |
| --- | --- | --- |
| Event Schema Set | `LowStockSchema` with `Retail.Inventory.LowStockThreshold` | Public preview API; requires a signed-in user and explicit preview consent |
| User Data Function | `LowStockPublisher` normalizes and publishes all seven finding fields | Shares the WMS finding, including product, store, stock, supplier and detection time |
| Activator (Data Pipeline) | `LowStockActions` passes all seven event fields to `PrepareReplenishmentRequest` | Starts request preparation for each finding; dynamic parameters are Preview |
| Data Pipeline and Notebook | `PrepareReplenishmentRequest` invokes `LowStockRequestWorker` | Saves a replenishment request linked to the pipeline run that prepared it |
| Lakehouse Files | `LowStockRequests/Files/ReplenishmentRequests/<request_id>.json` | Requests saved as JSON files with `pending_approval`, not a Delta or SQL table; open them under Files |
| Production WMS connection | Not configured by this package | The test calls the actual UDF using synthetic findings. Your WMS must authenticate and invoke the function in production |
| Business Event history in Eventhouse | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |

## Before you start

Use **Python 3.10+**, **Azure CLI** and **PowerShell on Windows**, with an active supported Fabric capacity, item-creation permission and tenant/workspace UUIDs. This sample uses a delegated user, not a service principal.

## 1. Configure your environment

Download **`low-stock-threshold.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\low-stock-threshold` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\low-stock-threshold'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` to block cloud operations if you do not accept preview APIs.

Schema names must be at most **20 characters** for the UDF connection alias.

Use an account that can run the functions and pipeline and write to the Lakehouse Files destination. This exercise prepares requests rather than sending Teams messages.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Open your workspace and find the schema, publisher, Activator (Data Pipeline), pipeline, worker and Lakehouse. The connections and request folder are prepared for you. Continue with the sample to create the first requests.

## 3. Send the findings and check requests

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Send the sample once to represent a keyboard, mouse and display that need replenishment. Keep the returned product IDs so you can find their requests. Allow time for the pipeline to finish before checking the Lakehouse.

### Evaluation criteria

- In Activator, find the product IDs ending in `-keyboard`, `-mouse`, and `-display`. The returned `source_run_id` distinguishes this sample from earlier exercises.
- Open Lakehouse `Files/ReplenishmentRequests` and expect **three requests** with `pending_approval`. Each `pipeline_run_id` should link to the completed run that prepared its request.
- Check `product_id`, `product_name`, `store_id`, `current_stock`, `threshold`, `supplier_id` and `detected_at` against the event. Keyboard, mouse and display stock must remain `4`, `0` and `-2`, with thresholds `10`, `5` and `8`. Negative stock is retained; `threshold` is not an order quantity.
- Each finding should have one request. A repeated delivery should leave the existing request unchanged rather than create another item for the team to review.

The expected outcome is **three saved requests ready for approval**. Purchase approval, order quantity and stock replenishment are later business steps. Wait for the requests rather than sending the sample again. Optional history is separate from this exercise; see the creation-time conditions below.

[Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/low-stock-threshold) | [Public UDF connection and invocation contract](https://learn.microsoft.com/fabric/real-time-hub/publish-business-events#publish-from-a-user-data-function)

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Retail.Inventory.LowStockThreshold']
    | order by ingestion_time() desc
    | take 50
    ```

    ```kusto
    ['Retail.Inventory.LowStockThreshold']
    | where ingestion_time() > ago(7d)
    | summarize FindingCount = count() by store_id
    | order by FindingCount desc
    ```

    ```kusto
    ['Retail.Inventory.LowStockThreshold']
    | where ingestion_time() > ago(30d)
    | summarize Occurrences = count(), AvgStock = avg(current_stock) by product_id, product_name
    | where Occurrences > 3
    | order by Occurrences desc
    ```

    ```kusto
    ['Retail.Inventory.LowStockThreshold']
    | where ingestion_time() > ago(1h)
    | where product_id == "prod-mx-7821"
    | where store_id == "store-mx-042"
    | order by ingestion_time() desc
    | project ReceivedAt = ingestion_time(), product_id, product_name,
        store_id, current_stock, threshold, supplier_id, detected_at
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `LowStockSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Retail.Inventory.LowStockThreshold",
      "fields": [
        {
          "name": "product_id",
          "type": "string",
          "doc": "Unique identifier of the product"
        },
        {
          "name": "product_name",
          "type": "string",
          "doc": "Display name of the product"
        },
        {
          "name": "store_id",
          "type": "string",
          "doc": "Identifier of the store or warehouse reporting the condition"
        },
        {
          "name": "current_stock",
          "type": "int",
          "doc": "Current units available at the time of detection"
        },
        {
          "name": "threshold",
          "type": "int",
          "doc": "Minimum stock level that triggered this alert"
        },
        {
          "name": "supplier_id",
          "type": "string",
          "doc": "Identifier of the preferred supplier for reordering"
        },
        {
          "name": "detected_at",
          "type": "string",
          "doc": "Timestamp when the condition was detected, ISO 8601 format"
        }
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    import fabric.functions as fn
    import logging

    udf = fn.UserDataFunctions()

    # The alias must match the connection configured in Manage connections
    @udf.connection(argName='businessEventsClient', alias='RetailInventory')
    @udf.function()
    def publish_low_stock_event(
        businessEventsClient: fn.FabricBusinessEventsClient,
        productId: str,
        productName: str,
        storeId: str,
        currentStock: int,
        threshold: int,
        supplierId: str,
        detectedAt: str
    ) -> str:
        logging.info("publish_low_stock_event invoked.")

        event_data = {
            'product_id': productId,
            'product_name': productName,
            'store_id': storeId,
            'current_stock': currentStock,
            'threshold': threshold,
            'supplier_id': supplierId,
            'detected_at': detectedAt
        }

        businessEventsClient.PublishEvent(
            type='Retail.Inventory.LowStockThreshold',
            event_data=event_data,
            data_version='v1'
        )

        return "Event 'Retail.Inventory.LowStockThreshold' published successfully"
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    | Pipeline parameter | Parameter type | Business Event field |
    |---|---|---|
    | `product_id` | String | `product_id` |
    | `product_name` | String | `product_name` |
    | `store_id` | String | `store_id` |
    | `current_stock` | Int | `current_stock` |
    | `threshold` | Int | `threshold` |
    | `supplier_id` | String | `supplier_id` |
    | `detected_at` | String | `detected_at` |

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    | Parameter | Value |
    |---|---|
    | `productId` | `prod-mx-7821` |
    | `productName` | `Wireless Keyboard` |
    | `storeId` | `store-mx-042` |
    | `currentStock` | `4` |
    | `threshold` | `10` |
    | `supplierId` | `sup-451` |
    | `detectedAt` | Current UTC time in ISO 8601 format |

## Further reading

- [Real-Time Hub → Business Events → Create](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [User Data Function publisher documentation](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-user-data-function)
- [end-to-end tutorial](https://learn.microsoft.com/fabric/real-time-hub/business-events/tutorial-business-events-user-data-function-activation-email)
- [Invoke a User Data Function from Python](https://learn.microsoft.com/fabric/data-engineering/user-data-functions/tutorial-invoke-from-python-app)
- [Fabric item action documentation](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-trigger-fabric-items)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="connect-the-event-to-the-pipeline"></span>
<span id="connect-to-the-schema-set"></span>
<span id="consumer-1-prepare-a-replenishment-request-with-activator-data-pipeline"></span>
<span id="consumer-2-analyze-shortages-in-eventhouse"></span>
<span id="create-the-user-data-function"></span>
<span id="deploy-this-scenario"></span>
<span id="function-code"></span>
<span id="low-stock-threshold"></span>
<span id="prepare-the-target-pipeline"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers-activator-data-pipeline-and-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
