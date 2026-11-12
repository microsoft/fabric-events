# ${record_helpers}

import fabric.functions as fn
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
from urllib.parse import urlsplit

udf = fn.UserDataFunctions()


@udf.connection(argName="caseLakehouse", alias="CaseRecords")
@udf.function()
def register_payment_review(
    caseLakehouse: fn.FabricLakehouseClient,
    transactionId: str,
    paymentMethod: str,
    amount: float,
    currency: str,
    storeId: str,
    processedAt: str
) -> dict:
    record = make_record("payment", {
        "transaction_id": transactionId, "payment_method": paymentMethod, "amount": amount,
        "currency": currency, "store_id": storeId, "processed_at": processedAt,
    }, "${rule_id}")
    connection = caseLakehouse.connectToFiles()
    try:
        endpoint = urlsplit(connection.url)
        if (endpoint.scheme != "https" or endpoint.query or endpoint.fragment
                or connection.url.rstrip("/") != "${lakehouse_files_endpoint}"):
            raise ValueError("The case connection is not the exact public OneLake Files destination.")
        client = connection.get_file_client("PaymentReviewCases/" + record["case_id"] + ".json")
        try:
            try:
                client.create_file(match_condition=MatchConditions.IfMissing)
            except (ResourceExistsError, ResourceModifiedError) as error:
                if error.status_code not in (409, 412):
                    raise
                existing = json.loads(client.download_file().readall())
                return check_record("payment", existing, record)
            data = json.dumps(record, allow_nan=False).encode("utf-8")
            client.append_data(data, offset=0, length=len(data))
            client.flush_data(len(data))
            return check_record("payment", json.loads(client.download_file().readall()), record)
        finally:
            client.close()
    finally:
        connection.close()
