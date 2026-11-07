Fabric Business Events / Prototype card: fraud

# Order risk decision

[Back to the scenario](../fraud-response-before-fulfillment.md) | [Browse the implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/fraud-response-before-fulfillment)

`Set-Location` examples start at the repository root. Run subsequent commands from `cicd\fraud-response-before-fulfillment`; for an extracted package, open that scenario folder instead.

The [source folder](https://github.com/microsoft/fabric-events/tree/main/cicd/fraud-response-before-fulfillment) contains a live-validated native reference with separate deployment/test launchers and an executable-only ZIP builder. This site publishes the complete runbook and links to the implementation; the prototype downloads your reviewed configuration, not cloud resources.

**Reference implementation available; deployment and testing remain separate.** Two fresh edited-name deployments and three independent six-boundary test runs completed through the real UDF, native Activator rules, Power Automate flows, Eventhouse and isolated persistent order APIs. One deployment also provisioned its keyless Azure backend from scratch. Deployment resume and verification after persisted fulfillment were independently exercised. This is not a generally available customer provisioning flow or a production OMS adapter.

## Card contract

Ecommerce marketplace -> native User Data Function -> `Ecommerce.Order.FraudDecision` -> Activator and Eventhouse. Activator must invoke **exactly one matching custom action**, backed by its actual Power Automate flow.

Default review names: `OrderRiskSchema`, `OrderRiskPublisher`, `OrderRiskActions`, `OrderRiskHistory` and `OrderRiskAnalytics`. The history table follows the event name: `['Ecommerce.Order.FraudDecision']`. Choose and review names for your own resources; the reference does not silently adopt existing items with those names.

A risk score of 0.85 or above selects `hold`; 0.55 up to but excluding 0.85 selects `manual_review`; below 0.55 selects `release`. The score must be finite and between 0 and 1. The marketplace supplies the score; this function is not a fraud-scoring model. The score is not proof of fraud.

## Implemented source

[schema.avsc](https://github.com/microsoft/fabric-events/blob/main/cicd/fraud-response-before-fulfillment/schema.avsc) retains exactly the card's five v1 fields, including `risk_score:float`. [scenario.json](https://github.com/microsoft/fabric-events/blob/main/cicd/fraud-response-before-fulfillment/scenario.json) records the exact action parameter mappings, reference assets and validation scope. [function_app.py](https://github.com/microsoft/fabric-events/blob/main/cicd/fraud-response-before-fulfillment/items/OrderRiskPublisher.UserDataFunction/function_app.py), its native definition and function metadata implement the decision publisher. Policy stays in the Fabric UDF artifact, not in an orchestration script.

The [native Activator definition](https://github.com/microsoft/fabric-events/blob/main/cicd/fraud-response-before-fulfillment/items/OrderRiskActions.Reflex/ReflexEntities.json) contains the Business Events source, three decision-filtered rules and three native custom actions. The [flow templates](https://github.com/microsoft/fabric-events/tree/main/cicd/fraud-response-before-fulfillment/flows) preserve the management API definitions of the three working flows, with target identities, native connection information and credentials replaced by placeholders. The [test order API](https://github.com/microsoft/fabric-events/tree/main/cicd/fraud-response-before-fulfillment/test-order-api) implements real persistent synthetic-order operations.

The callable uses camelCase arguments; the event retains snake_case fields. It normalizes identifiers and a supplied timezone-aware UTC `decidedAt` for deterministic external tests. Publish failures propagate. The Avro float transport has single-precision limits; use an appropriate numeric tolerance when comparing history, but exact equality for the decision and other fields.

**Card scope:** this reference publishes only the decision event. The separate `Ecommerce.Order.HighRiskDetected` signal in the full public guide is a distinct extension, not automatically caused by the decision and not silently added to this v1 contract. Do not query fields such as `payment_method` or `amount` from this five-field decision table.

## Native bindings and connection ownership

The native files contain unbound `${event_name}`, `${schema_name}`, `${schema_id}`, `${workspace_id}`, `${event_id}`, `${source_id}` and `${rule_enabled}` tokens. The Risk launcher binds these tokens to the reviewed configuration and actual returned item IDs. Do not directly import the unbound templates or run them with another scenario's deployer.

The launcher binds the UDF connection to the actual schema-set identity and alias, enables the required schema permissions, associates the event with the actual child KQL database and reads the native definitions back. For manual setup, open the UDF in Fabric and select **Home > Manage connections > Add connection**. Search for the actual Event Schema Set, select it and connect. Its generated alias follows the schema-set name, `OrderRiskSchema` by default, and must match both the connection decorator and the native definition after binding. Save the connection.

The verified rules monitor the decision event, select the `decision` column and use **Is equal to** with `hold`, `manual_review` or `release`. This evaluates each matching event, including consecutive events with the same decision. It is not a transition-only "becomes equal" condition. Keep all three rules stopped while preparing their native custom actions and flow connections.

| Native action | Decision filter | Actual input fields | Backend operation |
|---|---|---|---|
| Hold order | `hold` | `order_id`, `risk_score` | `POST /orders/{order_id}/hold` |
| Manual review | `manual_review` | `order_id`, `customer_id`, `risk_score` | `POST /orders/{order_id}/manual_review` |
| Release order | `release` | `order_id` | `POST /orders/{order_id}/release` |

Map each parameter to the corresponding event field, not a literal field name or a value from a previous test. The captured native `inputFields` mapping uses `KeyValuePair` arguments with `complexArray` values and `EventFieldReference` entries of type `complexReference`. That type differs from a top-level `complex` argument; substituting the latter is rejected by native definition validation. The templates contain field references only, without rich-text padding that could change an order identifier.

### Actual Power Automate connections

The launcher generates new connection information through the native workload for each new action and creates the matching Power Automate flow. It does not copy a previous deployment's signed value. The initial Activator import contains only its source and event view; rules are added and activated only after all three actions and flows have passed readback.

For manual setup, define the exact parameter names in the native action wizard, select **Copy** and use **Open flow builder**. Authenticate the Data Activator connector in the actual builder when required. An API-reported connection status alone is not proof that the designer can use it. Do not modify or select an unrelated historical connection.

The native connection information is opaque and specific to the target action. Generate it through the native workload or copy it from that action's wizard; do not fabricate it, reuse another action's value, decode it as an authentication workaround or publish it. The reference flow placeholders are:

| Placeholder | Required binding |
|---|---|
| `${flow_name}` | Fresh flow display name recorded with the returned flow ID |
| `${native_connection_info}` | Genuine newly generated connection information for this native action |
| `${data_activator_connection_name}` | Actual authenticated Data Activator connector reference |
| `${order_api_base_url}` | Approved protected synthetic-order API base URL |
| `${order_api_key}` | Function credential supplied in memory, never committed or included in a package |

The native trigger operation is `CreatePowerAutomateWorkflow`. The management API uses `host.connectionName`; the native designer's code view can expose the same alias as `host.connection`. Preserve this distinction when exporting an actual trigger into a flow creation request.

The HTTP actions read `triggerBody()?['customProperties/order_id']`, plus the action-specific fields above. Scores are converted with `float(...)`. The native trigger also supplies `eventId`, which is passed as `x-activation-id` for persistent idempotence. Actual runs returned the same mapped values in both `customProperties` and `inputFields`.

Enable secure inputs and outputs on the HTTP action so the Function key is not exposed in run history. Do not save an unredacted service-held definition locally. Record the exact flow identity before continuing after creation; if the response is ambiguous, inspect the saved attempt instead of creating another flow.

Use isolated test flows and an approved order-system test endpoint. Do not issue production holds or resume production fulfillment during sample validation. A flow invocation is not proof that the order system enforced a hold; OMS enforcement needs separate evidence before fulfillment. Likewise, a review request is not a completed review.

### Persistent synthetic-order backend

The reference API requires Function authentication through `x-functions-key`. Its Azure storage uses managed identity, shared-key access disabled, and role assignments scoped to the isolated storage account. It is not a production OMS adapter.

Order IDs must have the form `cicd-<canonical-lowercase-run-uuid>-<lowercase-case-name>`, and customer IDs must begin with `test-`. Registration accepts exactly `order_id`, `customer_id` and `risk_score`. Use a fresh run UUID for every complete test rather than overwriting old rows.

| Operation | Persisted result |
|---|---|
| `POST /orders` | New `pending` order with no actions |
| `GET /orders/{order_id}` | Independently read current state and action count |
| `POST /orders/{order_id}/hold` | `held`, fulfillment prohibited |
| `POST /orders/{order_id}/manual_review` | `review_requested`, stable review request ID, `review_completed=false` |
| `POST /orders/{order_id}/release` | `released`, fulfillment permitted |
| `POST /orders/{order_id}/fulfill` | HTTP 409 unless released; successful fulfillment persists `fulfilled` |

All decision operations require `x-activation-id` and validate their exact input fields against the registered order. Repeating the same activation and decision is idempotent; competing decisions are rejected. Storage updates use the original ETag to detect concurrent changes. The API does not recalculate the UDF's risk thresholds.

## Repeatable deployment and testing

### Prerequisites and configuration

Use Python 3.11 or later, Azure CLI with an existing supported delegated session, an approved Fabric workspace with active capacity, and an authenticated Data Activator connection in the selected Power Automate target. The launcher never opens a login window. Azure provisioning requires permission to create the configured resources and assign storage roles in the explicitly selected subscription and existing resource group.

The reference checks native workload compatibility before provisioning. An unsupported target fails preflight rather than falling back to another workspace, connector, consumer or publisher. This remains a reference implementation, not a generally available customer provisioning flow.

Download the reviewed configuration from the scenario wizard or copy the example:

```powershell
Set-Location '.\cicd\fraud-response-before-fulfillment'
Copy-Item '.\config.card.example.json' '.\fraud.local.json'
$configFile = '.\fraud.local.json'
$ledger = '.\.deployments\my-first-attempt\deployment.json'
```

Complete the approved targets and names before deploying. Configuration contains references, never Function keys or signed action connection information.

| Configuration | Binding |
|---|---|
| `workspace_id` | Actual approved Fabric workspace UUID, not the prototype's display-only workspace |
| `resource_prefix` | Fresh portable namespace for the deployment; use a different prefix for a separate installation |
| `resource_names` | Exact reviewed schema, publisher, Activator, Eventhouse and child database names |
| `event_name` | Avro-qualified event name; also determines the managed history table |
| `flow_environment_id` | Approved Power Automate target UUID |
| `data_activator_connection_name` | Existing authenticated Data Activator connector reference in that target |
| `flow_names` | Three distinct, fresh display names for hold, manual-review and release flows |
| `azure.subscription_id`, `azure.resource_group` | Explicitly approved Azure scope; the launcher does not switch the CLI's default subscription |
| `azure.mode` | `provision` creates a new isolated backend; `existing` explicitly selects a ready synthetic backend |
| Remaining `azure` fields | Location, fresh storage/Function/identity names and table name |

Fabric and flow names use 3-100 portable characters and must be distinct within their groups. The resource prefix uses 3-40 letters, digits or underscores and starts with a letter. Storage names use 3-24 lowercase letters/digits. Do not put credentials in any configuration field. In `existing` mode, the API must independently report `test_only=true`, readiness and the exact configured table; an arbitrary order endpoint is not accepted.

### Plan and deploy without test inputs

```powershell
python .\card.py plan --config $configFile
.\deploy.bat --config $configFile --state-file "$ledger"
```

The offline plan performs no cloud writes and can run before target UUIDs are supplied. Deployment requires a fresh ledger and refuses unmanaged name collisions. It creates native Fabric items, schema permissions and the Eventhouse association; provisions or verifies the protected backend; creates three genuine action connections and flows; then binds and activates the three decision rules. It independently reads definitions and identities back before reporting success.

In `provision` mode, Azure resources include keyless storage, a user-assigned managed identity, storage-scoped Blob/Table role assignments, a Python Azure Function and its required hosting resources. The launcher deploys the actual API source with a remote build, configures managed-identity runtime storage, disables shared-key storage access and verifies authenticated health. It does not modify unrelated resources in the selected group.

Deployment does not register orders, invoke the publisher or execute actions. The newly provisioned backend was independently empty and all three real flows had zero runs before the explicit test. Keep fresh deployment identities, exact native connection and flow references, tracked asynchronous operations and readback evidence under ignored `.deployments`; never silently adopt resources by name. The wizard's reviewed-name configuration contract was validated before enabling its download.

### Resume the exact deployment

```powershell
python .\card.py resume --config $configFile --state-file "$ledger"
```

Do not edit the configuration or names associated with an existing ledger. Resume uses recorded identities and import operation locations, not name-based discovery or an older successful deployment. Every creation intent is persisted before submission. Accepted or ambiguous operations without sufficient readback are not automatically replayed. Inspect the exact saved operation when the launcher reports an unresolved request.

Function keys remain in memory and service-held secure flow definitions. Local records never contain those keys. Native connection information is temporarily retained only in the ignored private attempt record while creating its flow; the signed value is removed after successful flow readback. Never publish or package local records.

### Run, verify and start a distinct batch

```powershell
.\test.bat --config $configFile --state-file "$ledger"
.\test.bat --config $configFile --state-file "$ledger" --verify-only
.\test.bat --config $configFile --state-file "$ledger" --new-run
```

The first command registers six run-scoped synthetic orders, invokes the actual UDF, observes genuine native flow runs and verifies complete Eventhouse payloads, exact native activation/rule/parameter correlation and real fulfillment enforcement. It does not replace the detector or consumer with an orchestration script.

The default test invocation resumes the selected test rather than republishing completed or ambiguously accepted UDF invocations. `--verify-only` never registers or publishes a new batch. Completed release orders remain `fulfilled`; verification reads that persisted result without calling fulfillment again. `--new-run` requires the previous batch to be fully verified and allocates new order IDs, timestamps, run baselines and an independent evidence file. It preserves all earlier proof.

Exit code `0` means the selected operation succeeded; `1` means configuration, preflight, service operations or evidence verification failed. A definition or successful publisher response alone never makes the end-to-end test pass.

### Pause and package

```powershell
python .\card.py pause --config $configFile --state-file "$ledger"
python .\bundle.py --output '..\fraud-response-before-fulfillment.zip'
```

Pause stops only the three exact owned native rules, with replay disabled. It does not delete flows, connector connections, Fabric items or Azure resources. The next explicit test can reactivate those rules without replaying old events.

The optional ZIP uses an executable-asset allowlist: launchers, runtime, native definitions, flow templates, configuration contracts, fixtures, offline tests and backend source. It excludes documentation, local configurations, `.deployments`, browser captures, credentials and compiled caches. Documentation stays exclusively on this nested site page, with the navigation and references above.

The repository's validation workflow runs the offline Risk publisher/domain and deployment-runtime tests and the unbound plan without cloud credentials. Live deployment/testing remains an explicit operation using the supported authenticated session and approved scopes; no unattended app-only provisioning is implied.

## Live boundary verification

The native reference and reusable package completed these six external cases with exactly one successful matching flow and one persisted action per order. The repeatable-package verification used three independent runs, totaling 18 genuine flow runs, complete history payloads and fulfillment checks:

| Score | Decision | Persisted action state | Fulfillment result |
|---|---|---|---|
| `0` | `release` | `released` | HTTP 200, then independently read `fulfilled` |
| `0.5499` | `release` | `released` | HTTP 200, then independently read `fulfilled` |
| `0.55` | `manual_review` | `review_requested` | HTTP 409 |
| `0.8499` | `manual_review` | `review_requested` | HTTP 409 |
| `0.85` | `hold` | `held` | HTTP 409 |
| `1` | `hold` | `held` | HTTP 409 |

For another verification, first independently confirm the protected API is ready, all three rules have the correct native sink references, and the flows use the intended authenticated connection. Record existing flow run IDs as the baseline. Register six fresh pending orders, invoke the actual UDF once per case and preserve each invocation intent before submission.

Query all five event fields from `['Ecommerce.Order.FraudDecision']`, restricted to the fresh run's order ID prefix. Allow for asynchronous Eventhouse ingestion; a successful flow may precede the history row by several minutes. Require exactly one complete payload per case, exact identifiers/decision/timestamp and float32-aware score comparison.

Read each actual native flow trigger output and correlate it to the order ID and matching rule. Require six successful runs across the three flows, no competing decision flow for an order, independently persisted action counts of one, and actual fulfillment responses and subsequent states from the API. Do not infer this evidence from publisher output, a message, the presence of a flow definition or direct API component tests.

Preserve proof before cleanup. Stop the exact owned rules when the isolated test ends; do not delete unrelated flows, connector connections, Fabric items or Azure resources. Keep review requests explicitly incomplete until a genuine human-review process finishes.

## Offline publisher tests

```powershell
Set-Location '.\cicd\fraud-response-before-fulfillment'
python -m unittest discover -s tests -p "test_*.py"
```

Six external cases in [data/test-cases.json](https://github.com/microsoft/fabric-events/blob/main/cicd/fraud-response-before-fulfillment/data/test-cases.json) cover 0, just below 0.55, exactly 0.55, just below 0.85, exactly 0.85 and 1. Offline tests execute the actual UDF source with an isolated SDK/client double and cover metadata, exact payloads, invalid inputs, normalization and surfaced publish failures. Separate backend domain tests cover synthetic-only inputs, persisted transitions, idempotence, review-request semantics and fulfillment enforcement.

The nine offline tests do not themselves prove native delivery. The live verification above additionally used actual UDF invocations, native Activator routing, six successful Power Automate runs, six Eventhouse payloads and six real fulfillment checks.

Reference: [full public risk scenario](../fraud-response-before-fulfillment.md).

## Continue reading

- [Full scenario and manual setup](../fraud-response-before-fulfillment.md)
- [Other scenarios and deployment guides](../index.md#deployment-implementations)
