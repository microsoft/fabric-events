import json
import logging
import os

import azure.functions as func
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from orders import OrderError, fulfill, identity, new_order, transition

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def table():
    service = TableServiceClient(
        endpoint=os.environ["ORDER_TABLE_ENDPOINT"],
        credential=ManagedIdentityCredential(client_id=os.environ["ORDER_IDENTITY_CLIENT_ID"]),
    )
    return service.create_table_if_not_exists(os.environ["ORDER_TABLE_NAME"])


def body(request):
    try:
        return request.get_json()
    except ValueError as error:
        raise OrderError(400, "The request must contain valid JSON.") from error


def read_order(client, order_id):
    run_id, row_id = identity(order_id)
    try:
        return client.get_entity(partition_key=run_id, row_key=row_id)
    except ResourceNotFoundError as error:
        raise OrderError(404, "The synthetic order has not been registered.") from error


def persist(client, previous, current):
    try:
        client.update_entity(
            current, mode=UpdateMode.REPLACE,
            etag=previous.metadata["etag"], match_condition=MatchConditions.IfNotModified,
        )
    except ResourceModifiedError as error:
        raise OrderError(409, "The order changed concurrently; read its current state.") from error


def response(value, status=200):
    return func.HttpResponse(json.dumps(value), status_code=status, mimetype="application/json")


def rejected(error):
    logging.warning("Synthetic order operation rejected (%s): %s", error.status, error)
    return response({"error": str(error)}, error.status)


@app.route(route="test/health", methods=["GET"], trigger_arg_name="request")
def health(request: func.HttpRequest) -> func.HttpResponse:
    client = table()
    has_orders = next(iter(client.list_entities(results_per_page=1)), None) is not None
    return response({
        "status": "ready", "table": client.table_name,
        "test_only": True, "has_orders": has_orders,
    })


@app.route(route="test/orders", methods=["POST"], trigger_arg_name="request")
def register(request: func.HttpRequest) -> func.HttpResponse:
    try:
        order = new_order(body(request))
        try:
            table().create_entity(order)
        except ResourceExistsError as error:
            raise OrderError(409, "The synthetic order already exists; use a fresh run.") from error
        return response(order, 201)
    except OrderError as error:
        return rejected(error)


@app.route(route="test/orders/{order_id}", methods=["GET"], trigger_arg_name="request")
def get_order(request: func.HttpRequest) -> func.HttpResponse:
    try:
        return response(dict(read_order(table(), request.route_params["order_id"])))
    except OrderError as error:
        return rejected(error)


@app.route(route="test/orders/{order_id}/{action}", methods=["POST"], trigger_arg_name="request")
def operate(request: func.HttpRequest) -> func.HttpResponse:
    try:
        client = table()
        current = read_order(client, request.route_params["order_id"])
        action = request.route_params["action"]
        updated = (
            fulfill(current) if action == "fulfill"
            else transition(current, action, body(request), request.headers.get("x-activation-id"))
        )
        if dict(current) != updated:
            persist(client, current, updated)
        return response(updated)
    except OrderError as error:
        return rejected(error)
