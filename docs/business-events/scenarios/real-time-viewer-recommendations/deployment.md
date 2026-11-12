# Real-Time Viewer Recommendations: Workbook

[Business context and architecture](../real-time-viewer-recommendations.md) | [Download package](../../../assets/downloads/real-time-viewer-recommendations.zip) | [Implementation](https://github.com/microsoft/fabric-events/tree/main/cicd/real-time-viewer-recommendations)

Prepare three content suggestions when a viewer watches or likes a title. Use the sample interactions to check that relevant suggestions are saved for the session, while clicks, searches and skips leave the recommendation list unchanged.

<span id="deployment-architecture"></span>
<span id="real-time-viewer-recommendations-deployment-guide"></span>

## Resources and coverage

| Component | Default name | Package coverage |
|---|---|---|
| Event schema and delegated Publish/Consume permissions | `ViewerSchema` | Public preview; original five-string `Streaming.Viewer.BehaviorDetected` contract |
| User Data Function publisher | `ViewerPublisher` | Native Business Event connection; `publish_viewer_behavior_event` accepts the five original interaction fields |
| Activator (User Data Function) | `ViewerActions` | Native subscription includes `watch` or `like`; active `Refresh session recommendations` rule uses **On each value** |
| User Data Function response | `ViewerRefresh` | Five dynamic parameters; ranks three recommendations from the embedded synthetic catalog |
| Recommendation files | `ViewerRecommendations` | Empty `Files/ViewerRecommendations` folder; each qualifying interaction saves its own recommendation list |
| Independent managed event history | Not configured by this package | Creation-time option; see [Optional event history](#optional-event-history) |
| Trained model, viewer profile store and streaming serving layer | Not configured by this package | A persisted recommendation is not proof of personalization quality or display in an application |

The package creates five Fabric items and a small sample catalog in `catalog.json`. Its content tags and popularity scores help illustrate recommendation preparation without a trained model or a streaming application.

## Before you start

Use Python 3.10+, Azure CLI and PowerShell on Windows, an active supported Fabric capacity, and permission to create the scenario's workspace items and execute its source. Prepare any learner-owned connections listed in the coverage table.

## 1. Configure your environment

Download **`real-time-viewer-recommendations.zip`** or the [repository ZIP](https://github.com/microsoft/fabric-events/archive/refs/heads/main.zip), extract it, and open its `cicd\real-time-viewer-recommendations` folder.

Replace the three placeholders below:

```powershell
Set-Location '<extracted-folder>\cicd\real-time-viewer-recommendations'
$tenantId = '<your-tenant-uuid>'
$workspaceId = '<your-workspace-uuid>'

az login --tenant $tenantId
python .\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId --auth-provider azure_cli --output .\config.local.json
```

Review the tenant, workspace and unused resource names in `config.local.json`. **`accept_preview_apis` defaults to `true`**; set it to `false` if you do not accept preview APIs, which prevents deployment. Keep the schema name between 3 and 20 characters because it is the UDF connection alias.

Use Python 3.10 or later, Azure CLI, and a delegated user with access to an active Fabric capacity and permission to create items and execute the functions. The consumer execution identity needs write access to the destination Lakehouse. Configuration does not authenticate, deploy or overwrite an existing local configuration.

## 2. Deploy to Fabric

```powershell
.\deploy.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Deployment prepares both native functions, their managed connections, the schema, the operational folder and the started Activator rule. It sends no interactions and creates no recommendation snapshots.

In `ViewerActions`, review the `watch`/`like` subscription filter and the published `refresh_session_recommendations` target. Check the five dynamic mappings below. Do not use hardcoded sample values or **Test action** as a substitute for testing the publisher.

| Function parameter | Action type | Event field |
|---|---|---|
| `viewerId` | String | `viewer_id` |
| `sessionId` | String | `session_id` |
| `contentId` | String | `content_id` |
| `action` | String | `action` |
| `occurredAt` | String | `occurred_at` |

The recommendation folder should still be empty at this stage. Run the sample next to create its first lists. Dynamic Fabric item action parameters are a Preview capability.

## 3. Send interactions and inspect recommendations

```powershell
.\test.bat --config .\config.local.json --confirm-workspace $workspaceId
```

Run the sample once to represent six viewer interactions. The two watches and one like should each produce a recommendation list; the click, search and skip should not. Use the viewer and session IDs returned by the sample to find the results.

| Interaction | Content | Expected recommendations, in order |
|---|---|---|
| `watch` | `space-explorer` | `mars-mission`, `robot-future`, `ocean-depths` |
| `like` | `city-mystery` | `case-notes`, `hidden-case`, `quiet-city` |
| `watch` | `quiet-city` | `family-roads`, `case-notes`, `hidden-case` |
| `click` | `space-explorer` | No response |
| `search` | `space-explorer` | No response |
| `skip` | `city-mystery` | No response |

The first two interactions belong to the same viewer and session. The like has an earlier timestamp: both lists should remain available rather than one replacing the other.

### Evaluation criteria

1. Keep the viewer and session IDs returned by the sample, using `source_run_id` to distinguish this exercise from earlier runs.
2. In `Refresh session recommendations` and `ViewerRefresh`, check that the two watches and one like each received a successful response.
3. Open **ViewerRecommendations → Files → ViewerRecommendations**. Expect three `<recommendation_id>.json` files with `status: "ready_for_serving"`. Match their viewer, session, action and time to the sample, and compare the suggested content and its order with the table.
4. Confirm no response execution or file for `click`, `search` or `skip`. Event delivery is asynchronous; inspect the run over at least five minutes, not just the first file.
5. Confirm the shared session retains both different recommendation lists. The interacted content must never appear among its own recommendations.

Each file also records the ranking method, catalog identity/hash, rule and creation time for later review. An identical interaction keeps its original list and timestamp; different interactions remain separate. Changed results for the same interaction are reported as an error rather than replacing an existing list.

The exercise finishes with three saved recommendation lists and no lists for excluded actions. Displaying them in an application and measuring engagement are separate next steps.

## Optional event history

The dashed branch in the business architecture is an extension, not another completion requirement. Choose **Analyze in Eventhouse** and its destination **during Business Event creation** if you are configuring an event natively with managed history. The package does not configure this association. Skip these queries for its deployed event; this Workbook does not provide a supported post-creation association procedure.

A source Eventhouse, a same-name table or a direct table write is not a managed Business Event subscription. History records received findings, not completed actions. Received-record counts can include repeated publications or deliveries and do not describe the full source population.

The following queries apply only to that creation-time history destination. Check delivered notifications or saved business outcomes separately. Keep currencies separate when comparing amounts.

??? info "History queries"

    ```kusto
    ['Streaming.Viewer.BehaviorDetected']
    | where ingestion_time() > ago(24h)
    | summarize ReceivedInteractions = count() by ReceivedHour = bin(ingestion_time(), 1h), action
    | order by ReceivedHour asc
    ```

    ```kusto
    ['Streaming.Viewer.BehaviorDetected']
    | where ingestion_time() > ago(7d)
    | where action in ('watch', 'like')
    | summarize ReceivedInteractions = count() by content_id, action
    | order by ReceivedInteractions desc
    ```

    ```kusto
    ['Streaming.Viewer.BehaviorDetected']
    | where ingestion_time() > ago(1h)
    | where viewer_id in ("viewer-demo", "viewer-excluded")
    | project ReceivedAt = ingestion_time(), viewer_id, session_id, content_id, action, occurred_at
    | order by ReceivedAt desc
    ```

See [Business Events and Eventhouse](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse).

## Implementation reference

Expand the details you need to understand or adapt the solution. This is reference, not a second setup tutorial. The package's default schema-set name is `ViewerSchema`. Standalone examples may use the original illustrative alias; match the connection alias to your configured schema set before reusing code. Event names and payload fields remain unchanged.

??? info "Event contract"

    The shared event fields are unchanged. The package prepares this contract; inspect it rather than creating a second event.

    ```json
    {
      "type": "record",
      "name": "Streaming.Viewer.BehaviorDetected",
      "fields": [
        {"name": "viewer_id", "type": "string"},
        {"name": "session_id", "type": "string"},
        {"name": "content_id", "type": "string"},
        {"name": "action", "type": "string"},
        {"name": "occurred_at", "type": "string"}
      ]
    }
    ```

??? info "Source and publisher reference"

    These snippets explain the source and publication logic. Use the prepared publisher for the main exercise; standalone examples are not an additional source run.

    ```python
    import datetime
    import fabric.functions as fn

    udf = fn.UserDataFunctions()


    @udf.connection(argName="businessEventsClient", alias="ViewerSchema")
    @udf.function()
    def publish_viewer_behavior_event(
        businessEventsClient: fn.FabricBusinessEventsClient,
        viewerId: str,
        sessionId: str,
        contentId: str,
        action: str,
        occurredAt: str,
    ) -> dict:
        event = {
            "viewer_id": viewerId, "session_id": sessionId, "content_id": contentId,
            "action": action, "occurred_at": occurredAt,
        }
        if any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in event.values()):
            raise ValueError("Supply all five nonempty interaction strings, at most 128 characters each.")
        if action not in ("click", "search", "watch", "skip", "like"):
            raise ValueError("Use a supported lowercase interaction.")
        if datetime.datetime.fromisoformat(occurredAt.replace("Z", "+00:00")).utcoffset() != datetime.timedelta(0):
            raise ValueError("Use a timezone-aware UTC timestamp.")
        businessEventsClient.PublishEvent(
            type="Streaming.Viewer.BehaviorDetected", event_data=event, data_version="v1",
        )
        return {"event_published": True}
    ```

??? info "Response and mapping reference"

    Use these field and response details to understand the prepared resources. Complete only the connections identified in the main steps.

    ```python
    import fabric.functions as fn
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
    from urllib.parse import urlsplit

    EXPECTED_FILES_ENDPOINT = "https://onelake.dfs.fabric.microsoft.com/<workspace-id>/<lakehouse-id>/Files"
    CATALOG = {
        "catalog_id": "synthetic-streaming-catalog",
        "items": [
            {"content_id": "space-explorer", "tags": ["science", "space"], "popularity": 80},
            {"content_id": "mars-mission", "tags": ["science", "space"], "popularity": 70},
            {"content_id": "robot-future", "tags": ["science", "technology"], "popularity": 90},
            {"content_id": "ocean-depths", "tags": ["science", "nature"], "popularity": 85},
            {"content_id": "city-mystery", "tags": ["drama", "mystery"], "popularity": 75},
            {"content_id": "case-notes", "tags": ["drama", "mystery"], "popularity": 90},
            {"content_id": "hidden-case", "tags": ["drama", "mystery"], "popularity": 90},
            {"content_id": "quiet-city", "tags": ["drama", "family"], "popularity": 85},
            {"content_id": "family-roads", "tags": ["drama", "family"], "popularity": 80},
        ],
    }
    udf = fn.UserDataFunctions()


    @udf.connection(argName="recommendationLakehouse", alias="RecommendationData")
    @udf.function()
    def refresh_session_recommendations(
        recommendationLakehouse: fn.FabricLakehouseClient,
        viewerId: str,
        sessionId: str,
        contentId: str,
        action: str,
        occurredAt: str,
    ) -> dict:
        record = make_recommendations({
            "viewer_id": viewerId, "session_id": sessionId, "content_id": contentId,
            "action": action, "occurred_at": occurredAt,
        }, CATALOG)
        connection = recommendationLakehouse.connectToFiles()
        try:
            endpoint = urlsplit(connection.url)
            if (endpoint.scheme != "https" or endpoint.query or endpoint.fragment
                    or connection.url.rstrip("/") != EXPECTED_FILES_ENDPOINT.rstrip("/")):
                raise ValueError("The recommendation connection has the wrong public OneLake destination.")
            client = connection.get_file_client("ViewerRecommendations/" + record["recommendation_id"] + ".json")
            try:
                try:
                    client.create_file(match_condition=MatchConditions.IfMissing)
                except (ResourceExistsError, ResourceModifiedError) as error:
                    if error.status_code not in (409, 412):
                        raise
                    return check_recommendations(json.loads(client.download_file().readall()), record)
                data = json.dumps(record, allow_nan=False).encode("utf-8")
                client.append_data(data, offset=0, length=len(data))
                client.flush_data(len(data))
                return check_recommendations(json.loads(client.download_file().readall()), record)
            finally:
                client.close()
        finally:
            connection.close()
    ```

    | Function parameter | Action type | Business Event field |
    |---|---|---|
    | `viewerId` | String | `viewer_id` |
    | `sessionId` | String | `session_id` |
    | `contentId` | String | `content_id` |
    | `action` | String | `action` |
    | `occurredAt` | String | `occurred_at` |

??? info "Payload and inspection reference"

    These individual examples explain payloads and inspection. They are not another test dataset or a second set of expected results; use the complete sample in the main steps.

    | Parameter | Qualifying watch | Qualifying like | Excluded click |
    |---|---|---|---|
    | `viewerId` | `viewer-demo` | `viewer-demo` | `viewer-excluded` |
    | `sessionId` | `session-demo` | `session-demo` | `session-excluded` |
    | `contentId` | `space-explorer` | `city-mystery` | `space-explorer` |
    | `action` | `watch` | `like` | `click` |
    | `occurredAt` | Current UTC time | A distinct current UTC time | Current UTC time |

## Further reading

- [Real-Time Hub → Business Events → Create](https://learn.microsoft.com/fabric/real-time-hub/business-events/create-business-events)
- [UDF publisher documentation](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-user-data-function)
- [recommendation helper source](https://github.com/microsoft/fabric-events/blob/main/cicd/viewer_records.py)
- [public source-filter contract](https://learn.microsoft.com/fabric/real-time-hub/configure-event-filters-rest-api)
- [Fabric item action documentation](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-trigger-fabric-items)

<span id="architecture"></span>
<span id="business-context"></span>
<span id="connect-and-start-the-native-rule"></span>
<span id="consumer-1-prepare-recommendations-with-activator-user-data-function"></span>
<span id="consumer-2-analyze-received-interactions-in-eventhouse"></span>
<span id="deploy-this-scenario"></span>
<span id="prepare-the-catalog-and-response-function"></span>
<span id="real-time-viewer-recommendations"></span>
<span id="step-1-create-the-business-event"></span>
<span id="step-2-publisher-user-data-function"></span>
<span id="step-3-consumers"></span>
<span id="step-4-end-to-end-test"></span>
<span id="what-happens-next"></span>
