# Abandoned Cart Recovery: Workbook

[Business context and architecture](../abandoned-cart-recovery.md) | [Download package](../../../assets/downloads/abandoned-cart-recovery.zip) | [Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/abandoned-cart-recovery)

Help the recovery team act on abandoned carts: send a reminder for carts above 30 and create a follow-up task for every cart. Set up the sample, connect your approved mailbox and SQL database, then check both outcomes.

<span id="abandoned-cart-recovery-deployment-guide"></span>
<span id="deployment-architecture"></span>

## Resources and coverage

| Component | Default name | Package coverage |
|---|---|---|
| Event schema and delegated Publish/Consume permissions | `CartSchema` | Public preview; original six-field `Ecommerce.Cart.Abandoned` contract |
| Native User Data Function publisher | `CartPublisher` | Managed schema connection and six camelCase inputs; publishes the original snake_case fields |
| Activator event source | `CartActions` | Native Business Event connection and prepared **Cart abandonment events** event |
| SQL database in Fabric | `CartFollowUp` | Holds follow-up tasks; execute the two included SQL scripts before testing |
| Recovery email rule and Power Automate flow | `Cart Recovery - Email` | Manual custom action, mappings, flow connection and activation; strict `cart_value > 30` |
| SQL follow-up rule and Power Automate flow | `Cart Recovery - SQL Follow-up` | Manual custom action, mappings and stored-procedure action; every valid cart |
| Approved mailbox and SQL connector identity | Learner-owned | Manual connection permissions; the SQL script supplies two synthetic customers, never real customer data |
| Independent managed event history | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |
| Storefront, profile store and offer engine | Not configured by this package | No inactivity detection, profile lookup, discount calculation or conversion measurement |

The package creates four Fabric items. Complete the SQL setup and connect the two Power Automate flows in Step 3 before sending carts. The database is a follow-up register for the team, not a complete CRM application.

## Before you start

Use Python 3.10+, Azure CLI and PowerShell on Windows, an active supported Fabric capacity, and permission to create the scenario's workspace items and execute its source. Prepare any learner-owned connections listed in the coverage table.

## 1. Configure your environment

Download **`abandoned-cart-recovery.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\abandoned-cart-recovery` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\abandoned-cart-recovery'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` if you do not accept preview APIs, which prevents deployment.

Set `test_recipient` to a mailbox approved to receive the three test reminders. Its default is `null`: deployment can prepare resources, but the source test will refuse to send carts until a recipient is configured. Do not put an action connection string, SQL password or customer mailing list in this file. Keep the schema name between 3 and 20 characters because it is the UDF connection alias.

Use Python 3.10+, Azure CLI, PowerShell on Windows and delegated access to an active supported Fabric capacity with SQL database creation enabled. The identity needs item creation and UDF execution permissions. The manual responses require the premium Data Activator and SQL Server connectors in Power Automate, an approved email connection, and Microsoft Entra ID access to the writable SQL database.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Open your workspace and find the schema, publishing function, Activator and `CartFollowUp` SQL database. Keep `sql_destination.server` and `sql_destination.database` from the command output for the SQL connection in Step 3. No carts have been sent yet; complete both response flows before running the sample.

The publisher inputs are `customerId`, `customerEmail`, `cartId`, `cartValue`, `itemCount` and `lastActivityAt`. Fabric rejects underscores in UDF parameter names; the original six event fields remain unchanged.

## 3. Complete manual setup before testing

1. Open the **SQL database** `CartFollowUp`, not its SQL analytics endpoint. In its SQL query editor, execute the package's `setup.sql` once, then `record-cart-task.sql` as a separate query. These create `dbo.Customers`, `dbo.CartRecoveryTasks` and `dbo.RecordCartRecoveryTask`. Confirm customers `test-cart-a` and `test-cart-b` and zero initial tasks. The scripts refuse existing tables instead of replacing data.
2. Open `CartActions`, select the prepared **Cart abandonment events** event, and create `Cart Recovery - Email` and `Cart Recovery - SQL Follow-up`. Do not create another Activator or another Business Event source.
3. Create a separate action named `Send cart recovery reminder` or `Create cart follow-up task`. Create each real custom action through **Action → Type → New custom action**, copy its own connection information, choose **Open flow builder**, authenticate the premium Data Activator connector and configure the matching trigger. The two actions need separate flows and their own connection information; never save signed connection information in configuration or shared evidence.
4. The email flow uses the dynamic `customer_email` as recipient and includes `cart_id` and the six original fields. For the other flow, add **SQL Server → Execute stored procedure**, authenticate with Microsoft Entra ID, and select `dbo.RecordCartRecoveryTask` on the actual `sql_destination.server` and `sql_destination.database`. Use the same authorized database-owner identity for this exercise. If a separate connection identity is required, the database owner must grant it database access and `EXECUTE` on the procedure; independent reviewers need `SELECT` on both tables. Select each saved custom action in its rule, choose **Edit action**, and map the six fields below.

    | Action input | Event field | Type |
    |---|---|---|
    | `customer_id` | `customer_id` | String |
    | `customer_email` | `customer_email` | String |
    | `cart_id` | `cart_id` | String |
    | `cart_value` | `cart_value` | Number |
    | `item_count` | `item_count` | Number |
    | `last_activity_at` | `last_activity_at` | String |

5. In **Execute stored procedure**, bind the six trigger inputs to the same-named SQL parameters and bind the trigger's **Activation ID** (`eventId`) to `activation_id`. Do not compose SQL text from event values. The procedure returns the persisted task row; connector/procedure errors mean follow-up failed.
6. For email, monitor `cart_value` with **Is greater than** `30` for each matching event. Do not use **Becomes greater than**, which can miss consecutive qualifying carts. For SQL follow-up, use **On each value** with no value filter. Save and activate both rules after checking the actual flow destinations.

Each registered customer can have several carts, but each cart has only one follow-up task. New tasks start as `pending`. A repeated delivery keeps the existing task, its status and creation time; conflicting cart details are reported as an error. Check the mailbox before retrying an uncertain email send, because the email connector can send a reminder more than once.

Check both saved flows and their destinations before sending the sample. Deployment leaves your manual action connections unchanged. Eventhouse history is an optional creation-time extension, not a requirement for this exercise.

## 4. Send carts and inspect recovery work

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Run the sample once to send six abandoned carts for the two sample customers. The three carts above 30 should receive a reminder; all six should create a follow-up task. The supplied last-activity time is 45 minutes earlier to represent abandonment.

| Sample cart | Cart value | Items | Expected email | Expected SQL task |
|---|---|---|---|---|
| high-value | `145.50` | `3` | Yes | Yes |
| above-boundary | `30.01` | `1` | Yes | Yes |
| large-cart | `250.00` | `5` | Yes | Yes |
| boundary | `30.00` | `2` | No | Yes |
| below-boundary | `29.99` | `1` | No | Yes |
| zero-value | `0.00` | `1` | No | Yes |

### Evaluation criteria

1. Keep the six cart IDs returned by the sample. Use `source_run_id` to distinguish this exercise from earlier runs.
2. In the approved mailbox, find three reminders with the qualifying cart IDs and match them to the email flow runs. Exact `30`, `29.99` and `0` should have no reminder or email response.
3. In `dbo.CartRecoveryTasks`, find six pending tasks and match them to the SQL flow runs. Check their task IDs, customer links and cart details.
4. Compare each task with its sample cart: customer, email, value, item count and last-activity time should agree. Two carts belonging to the same customer must remain separate tasks.
5. Allow at least five minutes for the responses. Check that the mailbox contains only the three qualifying reminders and the register contains six tasks.

This package does not measure checkout conversion, reward redemption or recovered revenue. Received-event history is an optional extension, not evidence that a reminder or task was completed.

Use the SQL query editor to inspect only the current run. Replace the placeholder with the actual `source_run_id`:

```sql
DECLARE @run_id uniqueidentifier = '<source_run_id>';
DECLARE @prefix nvarchar(42) = N'cicd-' + CONVERT(nvarchar(36), @run_id) + N'-';

SELECT t.task_id, t.cart_id, c.customer_id, c.display_name,
       t.customer_email, t.cart_value, t.item_count, t.last_activity_at,
       t.status, t.first_activation_id, t.created_at
FROM dbo.CartRecoveryTasks AS t
JOIN dbo.Customers AS c ON c.customer_id = t.customer_id
WHERE LEFT(t.cart_id, LEN(@prefix)) = @prefix
ORDER BY t.cart_id;
```

Compare the returned rows with the six sample carts, including their IDs and last-activity times. The task creation time is separate from the time the customer last used the cart. Values use single-precision numbers (float32, `real` in SQL), so minor display differences are expected; allow `1e-6` relative tolerance when comparing them and keep the strict above-30 reminder condition.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Ecommerce.Cart.Abandoned']
    | where ingestion_time() > ago(1h)
    | extend received_at = ingestion_time()
    | project received_at, customer_id, customer_email, cart_id, cart_value, item_count, last_activity_at
    | order by received_at desc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `CartSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Ecommerce.Cart.Abandoned",
      "fields": [
        {"name": "customer_id", "type": "string", "doc": "Unique identifier of the customer"},
        {"name": "customer_email", "type": "string", "doc": "Customer email address for recovery communication"},
        {"name": "cart_id", "type": "string", "doc": "Unique identifier of the abandoned cart"},
        {"name": "cart_value", "type": "float", "doc": "Total value of items in the cart at time of abandonment"},
        {"name": "item_count", "type": "int", "doc": "Number of distinct items in the cart"},
        {"name": "last_activity_at", "type": "string", "doc": "ISO 8601 timestamp of the last customer cart interaction"}
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    import datetime
    import math
    import re

    import fabric.functions as fn

    udf = fn.UserDataFunctions()


    @udf.connection(argName="businessEventsClient", alias="EcommerceCart")
    @udf.function()
    def publish_cart_abandoned_event(
        businessEventsClient: fn.FabricBusinessEventsClient,
        customerId: str,
        customerEmail: str,
        cartId: str,
        cartValue: float,
        itemCount: int,
        lastActivityAt: str,
    ) -> dict:
        for value in (customerId, cartId):
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 128:
                raise ValueError("Customer and cart identifiers must be nonempty strings of at most 128 characters.")
        if (not isinstance(customerEmail, str) or len(customerEmail) > 254
                or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", customerEmail)):
            raise ValueError("Provide one valid customer email address without whitespace.")
        if type(cartValue) not in (int, float) or not math.isfinite(cartValue) or not 0 <= cartValue <= 1_000_000_000_000:
            raise ValueError("Cart value must be finite and nonnegative.")
        if type(itemCount) is not int or not 1 <= itemCount <= 2_147_483_647:
            raise ValueError("Item count must be a positive Avro int.")
        if not isinstance(lastActivityAt, str):
            raise ValueError("Last activity must be a timezone-aware UTC timestamp.")
        instant = datetime.datetime.fromisoformat(lastActivityAt.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() != datetime.timedelta(0):
            raise ValueError("Last activity must be a timezone-aware UTC timestamp.")
        businessEventsClient.PublishEvent(type="Ecommerce.Cart.Abandoned", event_data={
            "customer_id": customerId, "customer_email": customerEmail, "cart_id": cartId,
            "cart_value": float(cartValue), "item_count": itemCount, "last_activity_at": lastActivityAt,
        }, data_version="v1")
        return {"event_published": True}
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    | Action input | Source event field | Type |
    |---|---|---|
    | `customer_id` | `customer_id` | String |
    | `customer_email` | `customer_email` | String |
    | `cart_id` | `cart_id` | String |
    | `cart_value` | `cart_value` | Number |
    | `item_count` | `item_count` | Number |
    | `last_activity_at` | `last_activity_at` | String |

    | Rule | Monitor and condition | Action |
    |---|---|---|
    | `Cart Recovery - Email` | `cart_value` **Is greater than** `30`; evaluate each matching event, not **Becomes greater than** | `Send cart recovery reminder` |
    | `Cart Recovery - SQL Follow-up` | Each `cart_id`, **On each value**; no value filter | `Create cart follow-up task` |

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    ```sql
    SELECT t.task_id, t.cart_id, c.customer_id, c.display_name,
           t.customer_email, t.cart_value, t.item_count, t.last_activity_at,
           t.status, t.first_activation_id, t.created_at
    FROM dbo.CartRecoveryTasks AS t
    JOIN dbo.Customers AS c ON c.customer_id = t.customer_id
    ORDER BY t.created_at, t.cart_id;
    ```

    | Cart label | `customerId` | `cartValue` | `itemCount` | Recovery email | SQL task |
    |---|---|---|---|---|---|
    | high-value | `test-cart-a` | `145.50` | `3` | Yes | Yes |
    | above-boundary | `test-cart-b` | `30.01` | `1` | Yes | Yes |
    | large-cart | `test-cart-a` | `250.00` | `5` | Yes | Yes |
    | boundary | `test-cart-b` | `30.00` | `2` | No | Yes |
    | below-boundary | `test-cart-a` | `29.99` | `1` | No | Yes |
    | zero-value | `test-cart-b` | `0.00` | `1` | No | Yes |

## Further reading

- [Real-Time Hub → Business events](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [setup.sql](https://github.com/microsoft/fabric-events/blob/main/cicd/abandoned-cart-recovery/setup.sql)
- [record-cart-task.sql](https://github.com/microsoft/fabric-events/blob/main/cicd/abandoned-cart-recovery/record-cart-task.sql)

<span id="abandoned-cart-recovery"></span>
<span id="architecture"></span>
<span id="business-context"></span>
<span id="deploy-this-scenario"></span>
<span id="inspect-independent-eventhouse-history"></span>
<span id="prepare-the-sql-follow-up-register"></span>
<span id="prepare-two-genuine-custom-actions"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers-recovery-email-sql-follow-up-and-history"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
