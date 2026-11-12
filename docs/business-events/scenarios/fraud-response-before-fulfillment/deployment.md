# Fraud Response Before Fulfillment: Workbook

[Business context and architecture](../fraud-response-before-fulfillment.md) | [Download package](../../../assets/downloads/fraud-response-before-fulfillment.zip) | [Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/fraud-response-before-fulfillment)

Apply the order-risk policy and check that fulfillment follows the decision. Connect three operational responses, then use six sample orders to compare releases, review requests and holds.

<span id="deployment-architecture"></span>
<span id="fraud-response-before-fulfillment-deployment-guide"></span>

## Resources and coverage

| Component | Default name | Package coverage |
|---|---|---|
| Event schema and delegated Publish/Consume permissions | `OrderRiskSchema` | Public preview; original five-field `Ecommerce.Order.FraudDecision` contract |
| User Data Function publisher | `OrderRiskPublisher` | Applies policy to the supplied order, customer, score and decision time |
| Activator event source | `OrderRiskActions` | Prepared `Order risk decisions` view; connect the three responses before testing |
| Three decision rules and Power Automate custom actions | Not configured by this package | Configure hold, review and release manually in the prepared Activator |
| Authenticated synthetic-order system | Not configured by this package | Supply an approved isolated test OMS, register the prepared orders and verify actual fulfillment behavior |
| Independent managed event history | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |

The package creates three Fabric items and no Azure backend or order system. Connect your three Power Automate responses manually using the [custom-action workflow](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows). The [Activator action reference](https://github.com/microsoft/skills-for-fabric/blob/6c11ad58c25992e5d1435ce7cd80d217d5598a31/plugins/fabric-skills/skills/activator-cli/references/authoring/action-types.md) describes the available action definitions.

## Before you start

### Test order system

Use an active Fabric capacity, permission to create Business Events and User Data Functions, and a Power Automate environment with the premium Data Activator connector. Supply an approved, isolated test OMS with authenticated operations for registering synthetic orders, holding, requesting review, releasing and checking fulfillment. This walkthrough does not supply or deploy an OMS.

Agree on these test-system semantics before connecting the flows:

| Operation | Required test behavior |
|---|---|
| Register an order | Store a fresh synthetic order as `pending`, with its customer and supplied risk score |
| Hold | Persist `held`; fulfillment remains prohibited |
| Request manual review | Persist `review_requested` and a stable review request ID; `review_completed` remains `false` |
| Release | Persist `released`; fulfillment becomes permitted |
| Read an order | Independently return its persisted state and action history |
| Attempt fulfillment | Return HTTP `409` for held or review-requested orders; allow a released order and persist `fulfilled` |

Use synthetic identifiers only. The OMS must authenticate callers, apply authorization and concurrency controls, make repeated delivery of the same Activation ID idempotent, and reject competing decisions rather than overwriting them.

## 1. Configure your environment

Download **`fraud-response-before-fulfillment.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\fraud-response-before-fulfillment` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\fraud-response-before-fulfillment'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; setting it to `false` prevents preview-dependent deployment. Keep the schema name between 3 and 20 characters because it is also the UDF connection alias.

Use Python 3.10 or later, Azure CLI, a delegated user with an active Fabric capacity, and an approved Power Automate environment with the premium Data Activator connector. Supply an authenticated, isolated test OMS meeting the [test order system requirements](#test-order-system). Configuration does not authenticate, create resources or overwrite an existing local configuration.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Deployment creates the schema, native UDF and Activator source. It does not create flows, register orders or invoke the UDF. The output supplies a fresh `source_run_id` and six `source_orders` containing the exact synthetic order IDs, customers and scores to prepare in your test OMS. Keep these values for the same deployment.

The three operational responses still need your connections. Complete Step 3 before evaluating the prepared orders.

## 3. Connect responses and prepare orders

1. Open the deployed `OrderRiskPublisher`. Inspect `evaluate_order_risk` and its managed connection to the deployed `OrderRiskSchema`. These are prepared resources, not new items to create.
2. Open `OrderRiskActions`, select the prepared **Order risk decisions** event, and create the three decision rules. Use the conditions, custom actions and mappings below. Do not create another Activator or another Business Event source.
3. Monitor `decision` with **Is equal to** for `hold`, `manual_review` and `release`. Do not use **Becomes equal to**: the two consecutive orders in each outcome must both be handled.
4. Create each custom action through **Action → Type → New custom action**, copy its own connection information and choose **Open flow builder**. Authenticate the Data Activator connector, connect the approved test OMS operation, save the flow, then return to Activator.

    | Decision | Action | Exact dynamic input mappings |
    |---|---|---|
    | `hold` | `Hold order` | `order_id` → `order_id`; `risk_score` → `risk_score` |
    | `manual_review` | `Request manual review` | `order_id` → `order_id`; `customer_id` → `customer_id`; `risk_score` → `risk_score` |
    | `release` | `Release order` | `order_id` → `order_id` |

5. In each rule, choose its saved custom action, select **Edit action**, map the exact event fields above, save and activate the rule. Use the trigger's **Activation ID** (`eventId`) for the OMS's `x-activation-id` idempotency header. Never put a signed connection string in configuration, code or exported evidence.
6. Using the test OMS's approved registration operation, register all six `source_orders` returned by deployment as sample `pending` orders. Keep the supplied IDs and check each saved order's customer and score before running the sample.
7. Confirm holds and review requests prohibit fulfillment, releases permit it, repeated Activation IDs are idempotent, and competing decisions cannot overwrite the stored action.

Before continuing, check that each flow uses the intended test order system and can perform its assigned action. This exercise focuses on fulfillment decisions; optional event history is separate from its completion criteria.

## 4. Evaluate orders and inspect enforcement

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Run the sample once to evaluate the six orders you registered. Expect two releases, two review requests and two holds. The order system should allow fulfillment only for the two released orders.

| Sample order | Supplied score | Decision | Expected saved OMS state |
|---|---|---|---|
| Minimum release | `0` | `release` | `released` |
| Below review | `0.5499` | `release` | `released` |
| Exact review | `0.55` | `manual_review` | `review_requested` |
| Below hold | `0.8499` | `manual_review` | `review_requested` |
| Exact hold | `0.85` | `hold` | `held` |
| Maximum hold | `1` | `hold` | `held` |

Use the prepared order IDs to check the flows and saved order states below. Allow time for the responses before checking fulfillment.

### Evaluation criteria

1. Use `source_run_id` and the six prepared order IDs to find this exercise's results. Compare each order's decision with the table.
2. Match the six Power Automate responses to their orders using the Activation ID and order ID. Expect two releases, two review requests and two holds, with no competing response for an order.
3. In the test OMS, check one saved action per order and the states in the table. The two review requests should have stable request IDs and `review_completed = false`; they are still awaiting human review.
4. Attempt fulfillment through the test OMS. The four held or review-requested orders should return HTTP `409`. The two released orders should complete fulfillment and show a saved `fulfilled` state.
5. Check that the response used the right order, customer and decision time. Scores use single-precision numbers (float32), so minor display differences are expected; allow `1e-6` when comparing them without rounding the original score before evaluation.

The exercise finishes when the two released orders can be fulfilled and the other four remain blocked, with review requests still awaiting human review. See [Optional event history](#optional-event-history) for received-event analysis and its creation-time conditions.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kql
    ['Ecommerce.Order.FraudDecision']
    | where order_id startswith "<your-fresh-order-prefix>"
    | project order_id, customer_id, risk_score, decision, decided_at
    | order by order_id asc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `OrderRiskSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    | Field | Type | Meaning |
    |---|---|---|
    | `order_id` | `string` | Order receiving the decision |
    | `customer_id` | `string` | Customer associated with the order |
    | `risk_score` | `float` | Supplied score between 0 and 1 |
    | `decision` | `string` | `hold`, `manual_review` or `release` |
    | `decided_at` | `string` | Decision time in timezone-aware UTC |

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    import datetime
    import math
    import fabric.functions as fn

    udf = fn.UserDataFunctions()

    def required_text(value, name, maximum):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        value = value.strip()
        if len(value) > maximum:
            raise ValueError(f"{name} must be at most {maximum} characters")
        return value

    @udf.connection(argName="businessEventsClient", alias="OrderRiskSchema")
    @udf.function()
    def evaluate_order_risk(
        businessEventsClient: fn.FabricBusinessEventsClient,
        orderId: str,
        customerId: str,
        riskScore: float,
        decidedAt: str,
    ) -> dict:
        orderId = required_text(orderId, "orderId", 128)
        customerId = required_text(customerId, "customerId", 128)
        if isinstance(riskScore, bool) or not isinstance(riskScore, (int, float)):
            raise ValueError("riskScore must be a number")
        if not math.isfinite(riskScore) or not 0 <= riskScore <= 1:
            raise ValueError("riskScore must be finite and between 0 and 1")
        decidedAt = required_text(decidedAt, "decidedAt", 64)
        instant = datetime.datetime.fromisoformat(decidedAt.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() != datetime.timedelta(0):
            raise ValueError("decidedAt must use timezone-aware UTC")
        decision = (
            "hold" if riskScore >= 0.85
            else "manual_review" if riskScore >= 0.55
            else "release"
        )
        businessEventsClient.PublishEvent(
            type="Ecommerce.Order.FraudDecision",
            event_data={
                "order_id": orderId,
                "customer_id": customerId,
                "risk_score": float(riskScore),
                "decision": decision,
                "decided_at": instant.isoformat().replace("+00:00", "Z"),
            },
            data_version="v1",
        )
        return {"decision": decision, "event_published": True}
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    | Rule | Match | Custom action | Exact input fields |
    |---|---|---|---|
    | `Hold order` | `hold` | `Hold order` | `order_id`, `risk_score` |
    | `Request manual review` | `manual_review` | `Request manual review` | `order_id`, `customer_id`, `risk_score` |
    | `Release order` | `release` | `Release order` | `order_id` |

    | Flow | Example test API operation | Dynamic body fields |
    |---|---|---|
    | Hold | `POST /orders/{order_id}/hold` | `risk_score` |
    | Review | `POST /orders/{order_id}/manual_review` | `customer_id`, `risk_score` |
    | Release | `POST /orders/{order_id}/release` | None |

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    | Score | Expected decision | Expected OMS state | Fulfillment check |
    |---|---|---|---|
    | `0` | `release` | `released` | Allowed; subsequently read `fulfilled` |
    | `0.5499` | `release` | `released` | Allowed; subsequently read `fulfilled` |
    | `0.55` | `manual_review` | `review_requested` | HTTP `409` |
    | `0.8499` | `manual_review` | `review_requested` | HTTP `409` |
    | `0.85` | `hold` | `held` | HTTP `409` |
    | `1` | `hold` | `held` | HTTP `409` |

## Further reading

- [documented custom-property expression](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows#pass-an-input-field-to-your-flow)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="consumer-1-activator-power-automate"></span>
<span id="consumer-2-independent-decision-history"></span>
<span id="deploy-this-scenario"></span>
<span id="fraud-response-before-fulfillment"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-create-the-policy-publisher"></span>
<span id="step-3-connect-the-operational-responses"></span>
<span id="step-4-test-from-the-original-source"></span>
<span id="what-happens-next"></span>
