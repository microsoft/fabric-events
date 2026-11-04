# Discount approval routing sample

This sample is a sanitized, reusable version of the Fabric UDF path demonstrated in the FabCon discount approval workflow. The browser sends proposal facts in snake_case to a Python server. The server validates them, maps them explicitly to the UDF's camelCase parameters, acquires a delegated development token with Azure CLI, and invokes the configured public UDF URL.

Start with the [scenario walkthrough](../../../docs/business-events/scenarios/discount-approval-routing.md).

## Artifacts

| File | Purpose |
|---|---|
| [`index.html`](index.html) | Browser application derived from the demonstrated commercial review desk |
| [`server.py`](server.py) | Server-side proxy, validation, Azure CLI authentication, response parsing, and explicit local simulation |
| [`evaluate_discount_request.py`](evaluate_discount_request.py) | Executable Fabric UDF with the demonstrated policy and validation |
| [`discount-approval-event-schema.avsc`](discount-approval-event-schema.avsc) | Exact nine-field v1 Business Event schema |
| [`verify_live.py`](verify_live.py) | Optional live invocation check without recording or browser automation |
| [`test_server.py`](test_server.py) | Unit and HTTP smoke tests |

## Fabric mode

Fabric mode is the primary path. Publish the function in [`evaluate_discount_request.py`](evaluate_discount_request.py), configure its `CommercialGovernance` connection alias, enable the public URL, and grant the signed-in developer Execute permission.

Sign in with Azure CLI:

```powershell
az login
```

Start the proxy with the public UDF URL supplied at runtime:

```powershell
python server.py --mode fabric --udf-url "<public UDF URL>"
```

Open `http://127.0.0.1:8787/`.

The server runs:

```text
az account get-access-token
  --scope https://analysis.windows.net/powerbi/api/user_impersonation
```

Token acquisition is server-side only. The URL and token are never embedded in browser code, returned by `/api/config`, or logged. Azure CLI delegated authentication is a developer path, not production identity guidance. A production host must use an approved identity and token lifecycle that is appropriate for its environment.

The invocation timeout defaults to the demonstrated 120 seconds. Override it only when needed:

```powershell
python server.py --mode fabric --udf-url "<public UDF URL>" --timeout 180
```

If Azure CLI, token acquisition, the UDF call, or response parsing fails, the server returns a stable public error category without Fabric response details, URLs, tokens, or exception reasons. It never switches to local simulation automatically.

## Request and response contract

The browser sends only the seven demonstrated proposal facts:

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

The proxy maps them to:

| Browser field | UDF parameter |
|---|---|
| `deal_id` | `dealId` |
| `account_name` | `accountName` |
| `region` | `region` |
| `deal_value` | `dealValue` |
| `requested_discount_percent` | `requestedDiscountPercent` |
| `estimated_margin_percent` | `estimatedMarginPercent` |
| `event_time` | `eventTime` |

For success, the Fabric envelope must contain `status: "Succeeded"`. The proxy treats `errors` as private upstream diagnostics and never returns them to the browser. It accepts `output` as a JSON object or JSON string and requires `approval_level`, `event_published`, and `message`. Additional envelope and output fields are tolerated. `invocationId` is optional and is preserved as `invocation_id` when present.

Public failures use `{ "category": "...", "error": "..." }` with stable categories for invalid requests, unavailable authentication, authorization or upstream rejection, timeout, unreachable upstream service, malformed UDF responses, and internal errors. Diagnostic response bodies and exception details remain private.

The application exposes the UDF's proven `event_published` value as `publication_requested`. A true value means `PublishEvent` returned during UDF execution. It does not confirm delivery to publisher preview, Eventstream, Eventhouse, or another consumer.

## Local policy simulation

Local mode is an explicit fallback for development and tests. It never calls Fabric and always returns `publication_requested: false`.

```powershell
python server.py --mode local
```

The policy remains:

- Discount at most 10% and margin at least 25%: within policy, no Business Event.
- Discount greater than 10% and at most 15%, with margin at least 25%: manager approval and publication in Fabric mode.
- Discount greater than 15% or margin below 25%: executive approval and publication in Fabric mode.

## Tests

```powershell
python -m unittest -v
```

The suite covers request mapping, validation, policy boundaries, response envelopes, Azure CLI failures, configurable timeout behavior, no automatic fallback, and local plus mocked Fabric HTTP paths. It does not require a deployed endpoint.

## Optional live verification

Run this only when you already have a safe public UDF URL:

```powershell
python verify_live.py --udf-url "<public UDF URL>"
```

The script invokes the same server-side path and checks for the executive result. Then verify the matching `deal_id` separately in the Business Event publisher preview and Eventstream Data preview. No endpoint is stored in this repository.

## Schema and workflow scope

[`discount-approval-event-schema.avsc`](discount-approval-event-schema.avsc) preserves the demonstrated v1 schema exactly: nine fields and `data_version="v1"`. It does not add `evaluation_id`.

For revision-safe correlation and idempotency, define an explicit schema v2 that adds an immutable `evaluation_id`. Do not silently add it to v1.

The executable sample stops at policy evaluation and event publication request. The scenario walkthrough separately configures Eventstream, Eventhouse analytics, and an Activator Teams notification for review-required events. No consumer is deployed by this sample. The walkthrough does not assign an approver, capture approve or reject decisions, manage an SLA or escalation, or update a CRM.

Treat `account_name` as customer data. Prefer a pseudonymous identifier when a display name is unnecessary, limit access to the event and analytical stores, and retain it only for the documented business purpose. Never log authentication tokens or expose them to the browser.
