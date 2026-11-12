# ${viewer_helpers}

import fabric.functions as fn
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
from urllib.parse import urlsplit

CATALOG = json.loads(${catalog_json})
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
    }, CATALOG, "${rule_id}")
    connection = recommendationLakehouse.connectToFiles()
    try:
        endpoint = urlsplit(connection.url)
        if (endpoint.scheme != "https" or endpoint.query or endpoint.fragment
                or connection.url.rstrip("/") != "${lakehouse_files_endpoint}"):
            raise ValueError("The recommendation connection is not the exact public OneLake Files destination.")
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
