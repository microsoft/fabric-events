# Real-Time Stream Alert: Workbook

[Business context and architecture](../real-time-stream-alert.md) | [Download package](../../../assets/downloads/real-time-stream-alert.zip)

Register high-value payments as cases for the review team. Send six sample payments and check that only amounts above 5000 create work; the exercise does not classify payments as fraudulent or block them.

<span id="deployment-architecture"></span>
<span id="real-time-stream-alert-quick-deployment"></span>

## Resources and coverage

| Resource | What the package sets up | What to expect |
| --- | --- | --- |
| Event Schema Set | `PaymentReviewSchema` with `Retail.Payment.HighValueTransaction` | Public preview API; requires a signed-in user and explicit preview consent |
| Eventstream | `PaymentReviewPublisher` with a custom endpoint, `amount > 5000` filter and six-field mapping | Receives sample payments and selects those that need review; connect your payment system separately for production |
| Activator (User Data Function) | `PaymentReviewActions` invokes `PaymentReviewFunctions.register_payment_review` | Passes the six payment details to case registration; dynamic parameters are Preview |
| User Data Function | `PaymentReviewFunctions` uses its managed `CaseRecords` Lakehouse connection | Saves the selected payment as a case for review |
| Lakehouse Files | `PaymentReviewCases/Files/PaymentReviewCases/<case_id>.json` | Cases saved as JSON files with `pending_review`, not a Delta or SQL table; open them under Files |
| Business Event history in Eventhouse | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |

## Before you start

Have **Python 3.10+**, **Azure CLI**, and **PowerShell on Windows** available. Choose a Fabric workspace on an active supported capacity where you can create items. Keep its workspace UUID and your tenant UUID ready.

## 1. Configure your environment

Download **`real-time-stream-alert.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\real-time-stream-alert` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\real-time-stream-alert'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` to block cloud operations if you do not accept preview APIs.

Use an account that can run the response function and write cases to Lakehouse Files. This exercise creates review cases rather than Teams messages.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Open your workspace and find the schema, Eventstream, Activator (User Data Function), response function and Lakehouse. The payment rule and case destination are prepared for you. Continue with the sample to create the first review cases.

## 3. Send the sample and check cases

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

The sample sends six payments with different amounts and currencies. Keep the returned transaction IDs so you can find this exercise's cases. **Eventstream, not the test script, filters the payments.** Expect three review cases and no case for payments at or below 5000.

### Evaluation criteria

- In Eventstream and Activator, check that only amounts **greater than 5000** continue to the review response. Use `source_run_id` and the returned transaction IDs to distinguish this exercise from earlier samples.
- In Lakehouse `Files/PaymentReviewCases`, expect **three cases** ending in `-above-usd`, `-above-eur`, and `-above-jpy`, with `pending_review`. Their amounts/currencies are `5000.01 USD`, `6000 EUR` and `9000 JPY`.
- Compare the six payment details: `transaction_id`, `payment_method`, `amount`, `currency`, `store_id` and `processed_at`. Each saved case should match the payment and its completed response.
- The `4999.99 USD` payment and both exact `5000` boundary payments should produce **no Business Event, UDF activation or case**. Check these exclusions using this exercise's transaction IDs.

Allow time for delivery and check the saved cases before drawing a conclusion. A repeated payment should retain the same case rather than create more work for reviewers. The outcome is a payment review queue, not a fraud decision or payment block. Run the sample once. Optional history is separate from this exercise; see the creation-time conditions below.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Retail.Payment.HighValueTransaction']
    | order by ingestion_time() desc
    | take 50
    ```

    ```kusto
    ['Retail.Payment.HighValueTransaction']
    | where ingestion_time() > ago(7d)
    | summarize ReceivedEvents = count(), LargestAmount = max(amount)
        by store_id, payment_method, currency
    | order by ReceivedEvents desc
    ```

    ```kusto
    ['Retail.Payment.HighValueTransaction']
    | where ingestion_time() > ago(24h)
    | summarize ReceivedEvents = count()
        by currency, ReceivedHour = bin(ingestion_time(), 1h)
    | order by ReceivedHour asc
    ```

    ```kusto
    ['Retail.Payment.HighValueTransaction']
    | where ingestion_time() > ago(1h)
    | where transaction_id in ("demo-review-6001", "demo-boundary-5000", "demo-below-4999")
    | project ReceivedAt = ingestion_time(), transaction_id, amount,
        currency, store_id, payment_method
    | order by ReceivedAt desc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `PaymentReviewSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Retail.Payment.HighValueTransaction",
      "fields": [
        {
          "name": "transaction_id",
          "type": "string",
          "doc": "Unique identifier of the payment transaction"
        },
        {
          "name": "payment_method",
          "type": "string",
          "doc": "Payment method used: credit_card, debit_card, or digital_wallet"
        },
        {
          "name": "amount",
          "type": "float",
          "doc": "Transaction amount in the store's local currency"
        },
        {
          "name": "currency",
          "type": "string",
          "doc": "ISO 4217 currency code, for example USD or MXN"
        },
        {
          "name": "store_id",
          "type": "string",
          "doc": "Identifier of the store where the transaction was processed"
        },
        {
          "name": "processed_at",
          "type": "string",
          "doc": "Timestamp when the transaction was processed, ISO 8601 format"
        }
      ]
    }
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    | Function parameter | Action type | Business Event field |
    |---|---|---|
    | `transactionId` | String | `transaction_id` |
    | `paymentMethod` | String | `payment_method` |
    | `amount` | Number | `amount` |
    | `currency` | String | `currency` |
    | `storeId` | String | `store_id` |
    | `processedAt` | String | `processed_at` |

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    | Field | Above-threshold record | Boundary record | Below-threshold record |
    |---|---|---|---|
    | `transaction_id` | `demo-review-6001` | `demo-boundary-5000` | `demo-below-4999` |
    | `payment_method` | `credit_card` | `credit_card` | `credit_card` |
    | `amount` | `6000` | `5000` | `4999` |
    | `currency` | `MXN` | `MXN` | `MXN` |
    | `store_id` | `store-mx-042` | `store-mx-042` | `store-mx-042` |
    | `processed_at` | Current UTC time in ISO 8601 format | Current UTC time in ISO 8601 format | Current UTC time in ISO 8601 format |

## Further reading

- [Real-Time Hub → Business Events → Create](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [Eventstream publisher documentation](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-event-stream-publisher)
- [end-to-end tutorial](https://learn.microsoft.com/fabric/real-time-hub/business-events/tutorial-business-events-event-stream-user-data-function-activator)
- [Fabric item action documentation](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-trigger-fabric-items)

<span id="add-a-filter-transformation"></span>
<span id="add-the-business-events-destination"></span>
<span id="architecture"></span>
<span id="business-context"></span>
<span id="configure-the-rule"></span>
<span id="consumer-1-register-a-payment-review-case-with-activator-user-data-function"></span>
<span id="consumer-2-analyze-high-value-activity-in-eventhouse"></span>
<span id="create-the-eventstream"></span>
<span id="deploy-this-scenario"></span>
<span id="map-the-schema-fields"></span>
<span id="prepare-the-target-function"></span>
<span id="real-time-stream-alert"></span>
<span id="set-up-the-alert-rule"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-eventstream"></span>
<span id="step-3-consumers-activator-user-data-function-and-eventhouse"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
