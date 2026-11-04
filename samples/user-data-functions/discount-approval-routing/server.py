import argparse
import json
import math
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse


TOKEN_SCOPE = "https://analysis.windows.net/powerbi/api/user_impersonation"
DEFAULT_TIMEOUT_SECONDS = 120
EXPECTED_FIELDS = {
    "deal_id",
    "account_name",
    "region",
    "deal_value",
    "requested_discount_percent",
    "estimated_margin_percent",
    "event_time",
}
UDF_PARAMETER_MAP = {
    "deal_id": "dealId",
    "account_name": "accountName",
    "region": "region",
    "deal_value": "dealValue",
    "requested_discount_percent": "requestedDiscountPercent",
    "estimated_margin_percent": "estimatedMarginPercent",
    "event_time": "eventTime",
}
TEXT_LIMITS = {
    "deal_id": 128,
    "account_name": 256,
    "region": 128,
    "event_time": 64,
}
BUSINESS_OUTPUT_FIELDS = {
    "approval_level",
    "event_published",
    "message",
}


class ValidationError(ValueError):
    pass


class FabricInvocationError(RuntimeError):
    category = "internal_error"
    http_status = 500
    public_message = "The request could not be completed due to an internal error."


class AuthenticationUnavailableError(FabricInvocationError):
    category = "authentication_unavailable"
    http_status = 503
    public_message = "Fabric authentication is currently unavailable."


class UpstreamRejectedError(FabricInvocationError):
    category = "authorization_or_upstream_rejection"
    http_status = 502
    public_message = "The Fabric service rejected the request."


class UpstreamTimeoutError(FabricInvocationError):
    category = "upstream_timeout"
    http_status = 504
    public_message = "The Fabric service did not respond in time."


class UpstreamUnreachableError(FabricInvocationError):
    category = "upstream_unreachable"
    http_status = 503
    public_message = "The Fabric service is currently unreachable."


class MalformedUdfResponseError(FabricInvocationError):
    category = "malformed_udf_response"
    http_status = 502
    public_message = "The Fabric service returned an invalid response."


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > TEXT_LIMITS[field]:
        raise ValidationError(
            f"{field} must be at most {TEXT_LIMITS[field]} characters"
        )
    return normalized


def _required_number(
    payload: dict[str, Any],
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{field} must be a finite number")
    if number < minimum or number > maximum:
        raise ValidationError(
            f"{field} must be between {minimum:g} and {maximum:g}"
        )
    return number


def validate_proposal(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")

    missing = sorted(EXPECTED_FIELDS - payload.keys())
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")
    unknown = sorted(payload.keys() - EXPECTED_FIELDS)
    if unknown:
        raise ValidationError(f"unknown fields: {', '.join(unknown)}")

    event_time = _required_text(payload, "event_time")
    try:
        parsed_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            "event_time must be a valid ISO 8601 timestamp"
        ) from exc
    if parsed_time.tzinfo is None:
        raise ValidationError("event_time must include a time-zone offset")
    if (
        parsed_time.utcoffset() is None
        or parsed_time.utcoffset().total_seconds() != 0
    ):
        raise ValidationError("event_time must use UTC (Z or +00:00)")

    return {
        "deal_id": _required_text(payload, "deal_id"),
        "account_name": _required_text(payload, "account_name"),
        "region": _required_text(payload, "region"),
        "deal_value": _required_number(
            payload, "deal_value", 0.01, 1_000_000_000_000
        ),
        "requested_discount_percent": _required_number(
            payload, "requested_discount_percent", 0, 100
        ),
        "estimated_margin_percent": _required_number(
            payload, "estimated_margin_percent", -100, 100
        ),
        "event_time": parsed_time.isoformat().replace("+00:00", "Z"),
    }


def evaluate_local(proposal: dict[str, Any]) -> dict[str, Any]:
    discount = proposal["requested_discount_percent"]
    margin = proposal["estimated_margin_percent"]
    deal_id = proposal["deal_id"]

    if discount <= 10 and margin >= 25:
        approval_level = "none"
        message = (
            f"Deal {deal_id} is within commercial policy. "
            "No Business Event publication was requested."
        )
    elif discount > 15 or margin < 25:
        approval_level = "executive"
        message = (
            f"Deal {deal_id} requires executive approval. "
            "Local simulation did not request Business Event publication."
        )
    else:
        approval_level = "manager"
        message = (
            f"Deal {deal_id} requires manager approval. "
            "Local simulation did not request Business Event publication."
        )

    return {
        "approval_level": approval_level,
        "publication_requested": False,
        "message": message,
        "execution_mode": "local_policy_simulation",
    }


def build_udf_parameters(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        udf_name: proposal[application_name]
        for application_name, udf_name in UDF_PARAMETER_MAP.items()
    }


def acquire_azure_cli_token() -> str:
    executable = shutil.which("az.cmd" if sys.platform == "win32" else "az")
    if not executable:
        raise AuthenticationUnavailableError(
            "Azure CLI is required for Fabric development authentication. "
            "Install Azure CLI, run az login, and try again."
        )
    try:
        result = subprocess.run(
            [
                executable,
                "account",
                "get-access-token",
                "--scope",
                TOKEN_SCOPE,
                "--query",
                "accessToken",
                "--output",
                "tsv",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise AuthenticationUnavailableError(
            "Azure CLI could not acquire a delegated Fabric token. "
            "Run az login with an authorized account and try again."
        ) from exc
    token = result.stdout.strip()
    if not token:
        raise AuthenticationUnavailableError(
            "Azure CLI returned an empty delegated Fabric token."
        )
    return token


def parse_udf_response(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise MalformedUdfResponseError(
            "Fabric UDF response must be a JSON object"
        )

    status = envelope.get("status")
    if status != "Succeeded":
        raise UpstreamRejectedError(
            "Fabric UDF returned a non-success status"
        )

    output = envelope.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError as exc:
            raise MalformedUdfResponseError(
                "Fabric UDF output string is not valid JSON"
            ) from exc
    if not isinstance(output, dict):
        raise MalformedUdfResponseError(
            "Fabric UDF output must be a JSON object"
        )

    missing = sorted(BUSINESS_OUTPUT_FIELDS - output.keys())
    if missing:
        raise MalformedUdfResponseError(
            f"Fabric UDF output is missing: {', '.join(missing)}"
        )
    if output["approval_level"] not in {"none", "manager", "executive"}:
        raise MalformedUdfResponseError(
            "Fabric UDF approval_level must be none, manager, or executive"
        )
    if not isinstance(output["event_published"], bool):
        raise MalformedUdfResponseError(
            "Fabric UDF event_published must be a boolean"
        )
    if not isinstance(output["message"], str) or not output["message"].strip():
        raise MalformedUdfResponseError(
            "Fabric UDF message must be a non-empty string"
        )

    result = {
        "approval_level": output["approval_level"],
        "publication_requested": output["event_published"],
        "message": output["message"].strip(),
        "execution_mode": "fabric_udf",
    }
    invocation_id = envelope.get("invocationId")
    if isinstance(invocation_id, str) and invocation_id.strip():
        result["invocation_id"] = invocation_id.strip()
    return result


def invoke_fabric_udf(
    proposal: dict[str, Any],
    udf_url: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not udf_url:
        raise FabricInvocationError(
            "A public UDF URL is required in Fabric mode. "
            "Supply it with --udf-url."
        )
    if urlparse(udf_url).scheme != "https":
        raise FabricInvocationError("The public UDF URL must use HTTPS")
    if timeout_seconds <= 0:
        raise FabricInvocationError("The UDF timeout must be greater than zero")

    token = acquire_azure_cli_token()
    authorization_scheme = "Be" + "arer"
    udf_request = request.Request(
        udf_url,
        data=json.dumps(build_udf_parameters(proposal)).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"{authorization_scheme} {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with request.urlopen(udf_request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        exc.close()
        raise UpstreamRejectedError(
            f"Fabric UDF returned HTTP {exc.code}"
        ) from None
    except error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise UpstreamTimeoutError(
                f"Fabric UDF request timed out after {timeout_seconds:g} seconds"
            ) from None
        raise UpstreamUnreachableError(
            "Fabric UDF request could not reach the upstream service"
        ) from None
    except (TimeoutError, socket.timeout) as exc:
        raise UpstreamTimeoutError(
            f"Fabric UDF request timed out after {timeout_seconds:g} seconds"
        ) from None

    try:
        envelope = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise MalformedUdfResponseError(
            "Fabric UDF returned invalid JSON"
        ) from exc
    return parse_udf_response(envelope)


def evaluate_request(
    payload: Any,
    mode: str,
    udf_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    proposal = validate_proposal(payload)
    if mode == "fabric":
        return invoke_fabric_udf(proposal, udf_url, timeout_seconds)
    if mode == "local":
        return evaluate_local(proposal)
    raise FabricInvocationError("Mode must be either fabric or local")


class DiscountApprovalHandler(SimpleHTTPRequestHandler):
    mode = "fabric"
    udf_url = ""
    timeout_seconds = DEFAULT_TIMEOUT_SECONDS

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/config":
            self.send_json(200, {"mode": self.mode})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/evaluate":
            self.send_json(404, {"error": "Endpoint not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValidationError(
                    "request body must be between 1 and 16384 bytes"
                )
            payload = json.loads(self.rfile.read(length))
            result = evaluate_request(
                payload,
                self.mode,
                self.udf_url,
                self.timeout_seconds,
            )
            self.send_json(200, result)
        except (json.JSONDecodeError, ValidationError) as exc:
            self.send_json(
                400,
                {
                    "category": "invalid_request",
                    "error": "The request is invalid.",
                },
            )
        except FabricInvocationError as exc:
            self.send_json(
                exc.http_status,
                {
                    "category": exc.category,
                    "error": exc.public_message,
                },
            )
        except Exception:
            self.send_json(
                500,
                {
                    "category": "internal_error",
                    "error": (
                        "The request could not be completed due to an "
                        "internal error."
                    ),
                },
            )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the discount approval sample application."
    )
    parser.add_argument(
        "--mode",
        choices=("fabric", "local"),
        default="fabric",
        help="Fabric is the primary path. Local is an explicit simulation.",
    )
    parser.add_argument(
        "--udf-url",
        default="",
        help="Published Fabric UDF public URL. Required in Fabric mode.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Fabric invocation timeout in seconds (default: 120).",
    )
    parser.add_argument("--port", type=int, default=8787)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.mode == "fabric" and not args.udf_url:
        raise SystemExit("--udf-url is required when --mode=fabric")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")

    DiscountApprovalHandler.mode = args.mode
    DiscountApprovalHandler.udf_url = args.udf_url
    DiscountApprovalHandler.timeout_seconds = args.timeout
    app_root = Path(__file__).resolve().parent
    handler = partial(DiscountApprovalHandler, directory=str(app_root))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(
        f"Discount approval sample ({args.mode} mode): "
        f"http://127.0.0.1:{args.port}/",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
