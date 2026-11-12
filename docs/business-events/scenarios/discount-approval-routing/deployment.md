# Discount Approval Evaluation and Event Publication: Workbook

[Business context and architecture](../discount-approval-routing.md) | [Download package](../../../assets/downloads/discount-approval-routing.zip) | [Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/discount-approval-routing)

Show sellers which proposals need manager or executive review, and notify the review team. Connect Eventstream to make the same review requirements available for further processing. This exercise requests review; it does not approve a discount.

<span id="deployment-architecture"></span>
<span id="discount-approval-evaluation-and-event-publication-deployment-guide"></span>

## Resources and coverage

| Component | Default name | Package coverage |
|---|---|---|
| Event schema and delegated Publish/Consume permissions | `DiscountSchema` | Public preview; exact nine-field `Sales.Deals.DiscountApprovalRequired` contract |
| User Data Function publisher | `DiscountPublisher` | Deployed with the native Business Event connection and seven typed proposal parameters |
| Activator (Teams) | `DiscountActions` | Event subscription and active `Notify discount review` rule; four dynamic notification fields |
| Eventstream | `DiscountRouting` | Create and connect manually before testing |
| Independent managed event history | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |
| Companion browser application | Not configured by this package | Optional source experience from the application sample; the package test invokes the same native UDF |

The package prepares three Fabric items. Connect Eventstream in Step 3; this connection is a manual step because the deployment API [does not expose Business Events as a source](https://learn.microsoft.com/fabric/real-time-hub/consume-events-with-event-stream-rest-api).

## Before you start

Use Python 3.10+, Azure CLI and PowerShell on Windows, an active supported Fabric capacity, and permission to create the scenario's workspace items and execute its source. Prepare any learner-owned connections listed in the coverage table.

## 1. Configure your environment

Download **`discount-approval-routing.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\discount-approval-routing` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\discount-approval-routing'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` if you do not accept preview APIs, which prevents deployment. Keep the schema name between 3 and 20 characters because it is also the native UDF connection alias.

Use Python 3.10 or later, Azure CLI, and a delegated user with access to an active Fabric capacity. `teams_recipients: ["@me"]` sends the notification to the same account as Azure CLI. Configuration does not authenticate, create resources or overwrite an existing local configuration.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Open your workspace and find `DiscountSchema`, `DiscountPublisher` and `DiscountActions`. The review notification is prepared. Continue with the Eventstream connection before submitting sample proposals.

## 3. Connect Eventstream before testing

1. In Real-Time Hub, open `Sales.Deals.DiscountApprovalRequired` under `DiscountSchema` in the configured workspace.
2. Select **Add consumer**, then **Eventstream**.
3. Select the same workspace and name the Eventstream `DiscountRouting`, or use your configured name.
4. Review the Business Event source and select **Connect**. Do not substitute a custom endpoint source.
5. Open the Eventstream and confirm the source uses the correct schema set and event, the default stream is running, and Data preview is available.
6. Open `DiscountActions`. Confirm `Notify discount review` uses **On each value**, its **Teams** action targets your configured recipient, and it includes `deal_id`, `approval_level`, `approval_reason` and `region`.

No history association or Eventhouse query is required for this package's response. See [Optional event history](#optional-event-history) for the creation-time conditions.

## 4. Evaluate proposals and inspect delivery

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Run the sample once to evaluate eight proposals. Two should stay within policy and need no notification. The other six should reach both Teams and Eventstream: two for manager review and four for executive review.

| Proposal | Discount % | Margin % | Expected level | Publication requested |
|---|---|---|---|---|
| Within policy | 5 | 30 | `none` | No |
| Seller boundary | 10 | 25 | `none` | No |
| Manager lower boundary | 10.01 | 25 | `manager` | Yes |
| Manager upper boundary | 15 | 25 | `manager` | Yes |
| Executive discount | 15.01 | 25 | `executive` | Yes |
| Executive margin | 10 | 24.99 | `executive` | Yes |
| Executive precedence | 12 | 24 | `executive` | Yes |
| Both executive reasons | 20 | 20 | `executive` | Yes |

### Evaluation criteria

1. Keep the eight deal IDs returned by the sample. Use `source_run_id` to find this exercise's proposals and compare their review levels with the table.
2. Inspect **six individual Teams notifications** for those IDs: **two manager and four executive** review requirements. Compare all four dynamic context fields.
3. In `DiscountRouting` Data preview, find the same six proposals. Check their deal details, `approval_level`, `approval_reason` and `event_time` against the sample.
4. Confirm the two within-policy IDs are absent from notifications and event payloads. Exactly 10% discount with 25% margin stays within policy; exactly 15% requires manager review; a margin below 25% takes executive precedence.
5. Match results by deal ID so that a repeated delivery is not mistaken for another proposal. Review is still required; none of these notifications is an approval decision.

The expected outcome is six proposals ready for review in both Teams and Eventstream, with no notification for the two proposals within policy.

## Optional application integration

The main exercise uses the prepared sample, not the browser application. If connecting your own sales application, use the already deployed function and the following adapter reference.

### Connect the sales application

The companion sample contains the [browser app](https://github.com/microsoft/fabric-events/blob/main/samples/user-data-functions/discount-approval-routing/index.html) and [Python proxy](https://github.com/microsoft/fabric-events/blob/main/samples/user-data-functions/discount-approval-routing/server.py).

The browser sends the demonstrated snake_case request:

```json
{
  "deal_id": "DEAL-2048",
  "account_name": "Sample Health",
  "region": "West",
  "deal_value": 450000.0,
  "requested_discount_percent": 18.0,
  "estimated_margin_percent": 22.0,
  "event_time": "2026-09-15T17:15:00Z"
}
```

The proxy maps it explicitly to the exact UDF parameters:

| Application field | UDF parameter |
|---|---|
| `deal_id` | `dealId` |
| `account_name` | `accountName` |
| `region` | `region` |
| `deal_value` | `dealValue` |
| `requested_discount_percent` | `requestedDiscountPercent` |
| `estimated_margin_percent` | `estimatedMarginPercent` |
| `event_time` | `eventTime` |

Publish the UDF, enable its public URL, and grant the developer Execute permission. Then authenticate with Azure CLI and start the sample:

```powershell
az login
python server.py --mode fabric --udf-url "<public UDF URL>"
```

The public UDF URL is supplied only at startup. It is not stored in browser code or committed. The proxy acquires a delegated development token server-side for:

```text
https://analysis.windows.net/powerbi/api/user_impersonation
```

Azure CLI delegated authentication is the demonstrated developer path. It is not a production identity design. Production hosting must use an approved, configurable identity and token lifecycle. Tokens must never be logged or exposed to the browser.

The proxy uses the demonstrated 120-second timeout by default. Use `--timeout` to configure it. If token acquisition or Fabric invocation fails, the proxy returns a clear error and does not fall back to local simulation.

### Understand the evaluation result {#parse-the-fabric-response}

The application shows the proposal's `approval_level` and `message`. It labels `event_published` as `publication_requested`: the review requirement has been submitted, while delivery to the review team is checked separately.

The adapter displays errors instead of a simulated policy result when Fabric cannot complete the evaluation. It accepts a successful `output` as a JSON object or string, requires the three result fields, and retains an optional `invocationId` as `invocation_id` for support. See the [Python proxy](https://github.com/microsoft/fabric-events/blob/main/samples/user-data-functions/discount-approval-routing/server.py) for response handling.

Local simulation is available in the sample README, but does not publish events or exercise this flow.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Sales.Deals.DiscountApprovalRequired']
    | where ingestion_time() > ago(24h)
    | project
        event_time,
        deal_id,
        account_name,
        region,
        deal_value,
        requested_discount_percent,
        estimated_margin_percent,
        approval_level,
        approval_reason
    | order by event_time desc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `DiscountSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Sales.Deals.DiscountApprovalRequired",
      "fields": [
        {
          "name": "deal_id",
          "type": "string",
          "doc": "Unique identifier of the proposal"
        },
        {
          "name": "account_name",
          "type": "string",
          "doc": "Customer account associated with the proposal"
        },
        {
          "name": "region",
          "type": "string",
          "doc": "Sales region responsible for the proposal"
        },
        {
          "name": "deal_value",
          "type": "double",
          "doc": "Total commercial value of the proposal"
        },
        {
          "name": "requested_discount_percent",
          "type": "double",
          "doc": "Discount percentage requested by the seller"
        },
        {
          "name": "estimated_margin_percent",
          "type": "double",
          "doc": "Estimated margin after applying the requested discount"
        },
        {
          "name": "approval_level",
          "type": "string",
          "doc": "Required approval authority: manager or executive"
        },
        {
          "name": "approval_reason",
          "type": "string",
          "doc": "Discount rule that triggered the approval request"
        },
        {
          "name": "event_time",
          "type": "string",
          "doc": "UTC time when the proposal was evaluated, ISO 8601 format"
        }
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    return {
        "approval_level": approval_level,
        "event_published": True,
        "message": (
            f"Deal {dealId} requires {approval_level} approval. "
            "Business Event published."
        ),
    }
    ```

    | Path | Condition | Synchronous result | Business Event |
    |---|---|---|---|
    | Within policy | Discount is at most 10% and margin is at least 25% | `approval_level: none` | Not published |
    | Manager approval | Discount is greater than 10% and at most 15%, and margin is at least 25% | `approval_level: manager` | Publication requested |
    | Executive approval | Discount is greater than 15% or margin is below 25% | `approval_level: executive` | Publication requested |

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    | Proposal | Discount % | Margin % | Expected level | Expected event |
    |---|---|---|---|---|
    | Within policy | 5 | 30 | `none` | None |
    | Seller boundary | 10 | 25 | `none` | None |
    | Manager lower boundary | 10.01 | 25 | `manager` | One |
    | Manager upper boundary | 15 | 25 | `manager` | One |
    | Executive discount | 15.01 | 25 | `executive` | One |
    | Executive margin | 10 | 24.99 | `executive` | One |
    | Executive precedence | 12 | 24 | `executive` | One |
    | Both executive reasons | 20 | 20 | `executive` | One |

## Further reading

- [companion application sample](https://github.com/microsoft/fabric-events/blob/main/samples/user-data-functions/discount-approval-routing/README.md)
- [Real-Time Hub, Business Events, Create](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [`discount-approval-event-schema.avsc`](https://github.com/microsoft/fabric-events/blob/main/samples/user-data-functions/discount-approval-routing/discount-approval-event-schema.avsc)
- [`evaluate_discount_request.py`](https://github.com/microsoft/fabric-events/blob/main/samples/user-data-functions/discount-approval-routing/evaluate_discount_request.py)
- [UDF publisher documentation](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-user-data-function)
- [Eventstream overview](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/overview)
- [schema versioning guidance](../../design-guides/schema-versioning.md)
- [retry guidance](../../design-guides/handling-retries.md)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="consumer-1-eventstream"></span>
<span id="consumer-2-eventhouse"></span>
<span id="consumer-3-activator-notification"></span>
<span id="create-and-connect-the-user-data-function"></span>
<span id="deploy-this-scenario"></span>
<span id="discount-approval-evaluation-and-event-publication"></span>
<span id="policy-paths"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
