import io
import http.client
import json
import math
import socket
import subprocess
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import error
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

import server
from server import (
    DEFAULT_TIMEOUT_SECONDS,
    AuthenticationUnavailableError,
    FabricInvocationError,
    MalformedUdfResponseError,
    UpstreamRejectedError,
    ValidationError,
    acquire_azure_cli_token,
    build_udf_parameters,
    evaluate_request,
    parse_udf_response,
    validate_proposal,
)


SAMPLE_ROOT = Path(__file__).resolve().parent


def proposal(discount: float = 8, margin: float = 32) -> dict:
    return {
        "deal_id": "DEAL-1001",
        "account_name": "Sample Retail",
        "region": "Central",
        "deal_value": 125000.0,
        "requested_discount_percent": discount,
        "estimated_margin_percent": margin,
        "event_time": "2026-09-15T17:10:00Z",
    }


def envelope(output=None, **extra) -> dict:
    if output is None:
        output = {
            "approval_level": "manager",
            "event_published": True,
            "message": "Business Event published.",
        }
    return {
        "status": "Succeeded",
        "output": output,
        "errors": [],
        **extra,
    }


class DiscountApprovalTests(unittest.TestCase):
    def test_exact_snake_case_to_camel_case_mapping(self) -> None:
        validated = validate_proposal(proposal())
        self.assertEqual(
            {
                "dealId": "DEAL-1001",
                "accountName": "Sample Retail",
                "region": "Central",
                "dealValue": 125000.0,
                "requestedDiscountPercent": 8.0,
                "estimatedMarginPercent": 32.0,
                "eventTime": "2026-09-15T17:10:00Z",
            },
            build_udf_parameters(validated),
        )

    def test_exact_proven_udf_parameters(self) -> None:
        self.assertEqual(
            {
                "dealId",
                "accountName",
                "region",
                "dealValue",
                "requestedDiscountPercent",
                "estimatedMarginPercent",
                "eventTime",
            },
            set(server.UDF_PARAMETER_MAP.values()),
        )
        self.assertNotIn("evaluationId", server.UDF_PARAMETER_MAP.values())

    def test_exact_policy_boundaries(self) -> None:
        cases = [
            (10, 25, "none"),
            (math.nextafter(10, math.inf), 25, "manager"),
            (15, 25, "manager"),
            (math.nextafter(15, math.inf), 25, "executive"),
            (10, math.nextafter(25, -math.inf), "executive"),
        ]
        for discount, margin, expected in cases:
            with self.subTest(discount=discount, margin=margin):
                result = evaluate_request(proposal(discount, margin), "local")
                self.assertEqual(expected, result["approval_level"])
                self.assertFalse(result["publication_requested"])

    def test_validation_rejects_missing_unknown_and_invalid_strings(self) -> None:
        invalid = proposal()
        del invalid["region"]
        with self.assertRaisesRegex(ValidationError, "region"):
            validate_proposal(invalid)
        invalid = proposal()
        invalid["approval_level"] = "manager"
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            validate_proposal(invalid)
        invalid = proposal()
        invalid["account_name"] = " "
        with self.assertRaisesRegex(ValidationError, "non-empty"):
            validate_proposal(invalid)
        invalid = proposal()
        invalid["account_name"] = "a" * 257
        with self.assertRaisesRegex(ValidationError, "at most 256"):
            validate_proposal(invalid)

    def test_validation_rejects_nan_infinity_and_ranges(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            invalid = proposal()
            invalid["deal_value"] = value
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValidationError, "finite"):
                    validate_proposal(invalid)
        for field, value, message in [
            ("deal_value", 0, "between 0.01"),
            ("requested_discount_percent", 101, "between 0 and 100"),
            ("estimated_margin_percent", -101, "between -100 and 100"),
        ]:
            invalid = proposal()
            invalid[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, message):
                    validate_proposal(invalid)

    def test_validation_requires_utc_timestamp(self) -> None:
        for timestamp, message in [
            ("not-a-time", "valid ISO 8601"),
            ("2026-09-15T17:10:00", "time-zone"),
            ("2026-09-15T10:10:00-07:00", "must use UTC"),
        ]:
            invalid = proposal()
            invalid["event_time"] = timestamp
            with self.subTest(timestamp=timestamp):
                with self.assertRaisesRegex(ValidationError, message):
                    validate_proposal(invalid)

    def test_response_requires_succeeded_status_without_copying_errors(self) -> None:
        with self.assertRaisesRegex(
            UpstreamRejectedError,
            "non-success status",
        ) as raised:
            parse_udf_response(
                {
                    "status": "Failed",
                    "output": None,
                    "errors": [
                        {
                            "name": "ValidationError",
                            "message": "discount is invalid",
                        }
                    ],
                }
            )
        self.assertNotIn("discount is invalid", str(raised.exception))

    def test_response_accepts_output_object_and_additional_fields(self) -> None:
        result = parse_udf_response(
            envelope(
                invocationId="inv-123",
                functionName="evaluate_discount_request",
                futureEnvelopeField={"allowed": True},
            )
        )
        self.assertEqual("manager", result["approval_level"])
        self.assertTrue(result["publication_requested"])
        self.assertEqual("inv-123", result["invocation_id"])
        self.assertEqual("fabric_udf", result["execution_mode"])

    def test_response_accepts_output_json_string_without_invocation_id(self) -> None:
        output = {
            "approval_level": "executive",
            "event_published": True,
            "message": "Business Event published.",
            "futureOutputField": 1,
        }
        result = parse_udf_response(envelope(json.dumps(output)))
        self.assertEqual("executive", result["approval_level"])
        self.assertTrue(result["publication_requested"])
        self.assertNotIn("invocation_id", result)

    def test_response_requires_proven_business_fields(self) -> None:
        for field in server.BUSINESS_OUTPUT_FIELDS:
            output = envelope()["output"].copy()
            del output[field]
            with self.subTest(field=field):
                with self.assertRaisesRegex(MalformedUdfResponseError, field):
                    parse_udf_response(envelope(output))

    @patch("server.subprocess.run")
    @patch("server.shutil.which", return_value="az.cmd")
    def test_azure_cli_token_scope_and_success(self, _which, run) -> None:
        run.return_value = MagicMock(stdout="delegated-token\n")
        self.assertEqual("delegated-token", acquire_azure_cli_token())
        command = run.call_args.args[0]
        self.assertIn(server.TOKEN_SCOPE, command)
        self.assertNotIn("delegated-token", command)

    @patch("server.shutil.which", return_value=None)
    def test_azure_cli_missing_is_explicit_error(self, _which) -> None:
        with self.assertRaisesRegex(AuthenticationUnavailableError, "Azure CLI"):
            acquire_azure_cli_token()

    @patch("server.subprocess.run")
    @patch("server.shutil.which", return_value="az.cmd")
    def test_azure_cli_acquisition_failure_is_explicit(self, _which, run) -> None:
        run.side_effect = subprocess.CalledProcessError(1, ["az"])
        with self.assertRaisesRegex(
            AuthenticationUnavailableError,
            "could not acquire",
        ):
            acquire_azure_cli_token()

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_default_and_configured_timeout(self, urlopen, _token) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(envelope()).encode()
        response.__enter__.return_value = response
        urlopen.return_value = response

        evaluate_request(
            proposal(12, 31),
            "fabric",
            "https://example.invalid/function",
        )
        self.assertEqual(
            DEFAULT_TIMEOUT_SECONDS,
            urlopen.call_args.kwargs["timeout"],
        )
        self.assertEqual(120, DEFAULT_TIMEOUT_SECONDS)
        sent_request = urlopen.call_args.args[0]
        self.assertEqual(
            "Bearer test-token",
            sent_request.get_header("Authorization"),
        )

        evaluate_request(
            proposal(12, 31),
            "fabric",
            "https://example.invalid/function",
            45,
        )
        self.assertEqual(45, urlopen.call_args.kwargs["timeout"])

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_fabric_timeout_is_explicit(self, urlopen, _token) -> None:
        urlopen.side_effect = error.URLError(socket.timeout())
        with self.assertRaisesRegex(
            FabricInvocationError,
            "timed out after 120 seconds",
        ):
            evaluate_request(
                proposal(),
                "fabric",
                "https://example.invalid/function",
            )

    def test_fabric_error_never_falls_back_to_local(self) -> None:
        with patch(
            "server.invoke_fabric_udf",
            side_effect=FabricInvocationError("token acquisition failed"),
        ):
            with self.assertRaisesRegex(
                FabricInvocationError,
                "token acquisition failed",
            ):
                evaluate_request(
                    proposal(),
                    "fabric",
                    "https://example.invalid/function",
                )

    def test_schema_v1_has_exact_proven_nine_fields(self) -> None:
        schema = json.loads(
            (SAMPLE_ROOT / "discount-approval-event-schema.avsc").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [
                "deal_id",
                "account_name",
                "region",
                "deal_value",
                "requested_discount_percent",
                "estimated_margin_percent",
                "approval_level",
                "approval_reason",
                "event_time",
            ],
            [field["name"] for field in schema["fields"]],
        )
        self.assertNotIn("evaluation_id", [f["name"] for f in schema["fields"]])


class HttpSmokeTests(unittest.TestCase):
    def run_server(self, mode: str, udf_url: str = ""):
        handler_type = type(
            "SmokeHandler",
            (server.DiscountApprovalHandler,),
            {
                "mode": mode,
                "udf_url": udf_url,
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            },
        )
        handler = partial(handler_type, directory=str(SAMPLE_ROOT))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(self, base_url: str, payload: dict) -> tuple[int, dict]:
        parsed_url = urlsplit(base_url)
        connection = http.client.HTTPConnection(
            parsed_url.hostname,
            parsed_url.port,
            timeout=5,
        )
        try:
            connection.request(
                "POST",
                "/api/evaluate",
                body=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def assert_public_error(
        self,
        status: int,
        body: dict,
        expected_status: int,
        expected_category: str,
        sensitive_markers: tuple[str, ...],
    ) -> None:
        self.assertEqual(expected_status, status)
        self.assertEqual(expected_category, body["category"])
        serialized = json.dumps(body)
        for marker in sensitive_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)

    def test_local_mode_http_smoke(self) -> None:
        base_url = self.run_server("local")
        status, result = self.post(base_url, proposal(12, 31))
        self.assertEqual(200, status)
        self.assertEqual("manager", result["approval_level"])
        self.assertFalse(result["publication_requested"])

    @patch("server.invoke_fabric_udf")
    def test_mocked_fabric_mode_http_smoke(self, invoke) -> None:
        invoke.return_value = {
            "approval_level": "manager",
            "publication_requested": True,
            "message": "Business Event published.",
            "execution_mode": "fabric_udf",
            "invocation_id": "smoke-invocation",
        }
        base_url = self.run_server(
            "fabric",
            "https://example.invalid/function",
        )
        status, result = self.post(base_url, proposal(12, 31))
        self.assertEqual(200, status)
        self.assertTrue(result["publication_requested"])
        self.assertEqual("smoke-invocation", result["invocation_id"])

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_fabric_error_envelope_is_sanitized(self, urlopen, _token) -> None:
        sensitive = "secret-validation-detail"
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "status": "Failed",
                "output": None,
                "errors": [{"name": "InternalError", "message": sensitive}],
            }
        ).encode()
        response.__enter__.return_value = response
        urlopen.return_value = response

        status, body = self.post(
            self.run_server("fabric", "https://example.invalid/function"),
            proposal(),
        )

        self.assert_public_error(
            status,
            body,
            502,
            "authorization_or_upstream_rejection",
            (sensitive, "InternalError", "errors"),
        )

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_fabric_http_error_body_is_sanitized(self, urlopen, _token) -> None:
        sensitive = "token=super-secret"
        urlopen.side_effect = error.HTTPError(
            "https://private.fabric.example/function",
            403,
            "Forbidden",
            {},
            io.BytesIO(sensitive.encode()),
        )

        status, body = self.post(
            self.run_server("fabric", "https://example.invalid/function"),
            proposal(),
        )

        self.assert_public_error(
            status,
            body,
            502,
            "authorization_or_upstream_rejection",
            (sensitive, "private.fabric.example", "403"),
        )

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_fabric_network_error_reason_is_sanitized(
        self,
        urlopen,
        _token,
    ) -> None:
        sensitive = "dns exposed private-host.internal"
        urlopen.side_effect = error.URLError(sensitive)

        status, body = self.post(
            self.run_server("fabric", "https://example.invalid/function"),
            proposal(),
        )

        self.assert_public_error(
            status,
            body,
            503,
            "upstream_unreachable",
            (sensitive, "private-host.internal", "example.invalid"),
        )

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_malformed_udf_output_is_sanitized(self, urlopen, _token) -> None:
        sensitive = "secret-output-marker"
        response = MagicMock()
        response.read.return_value = json.dumps(
            envelope(
                {
                    "approval_level": "manager",
                    "event_published": sensitive,
                    "message": "Business Event published.",
                }
            )
        ).encode()
        response.__enter__.return_value = response
        urlopen.return_value = response

        status, body = self.post(
            self.run_server("fabric", "https://example.invalid/function"),
            proposal(),
        )

        self.assert_public_error(
            status,
            body,
            502,
            "malformed_udf_response",
            (sensitive, "event_published"),
        )

    @patch(
        "server.acquire_azure_cli_token",
        side_effect=AuthenticationUnavailableError(
            "az login failed for sensitive-user@example.com"
        ),
    )
    def test_authentication_failure_is_sanitized(self, _token) -> None:
        sensitive = "sensitive-user@example.com"

        status, body = self.post(
            self.run_server("fabric", "https://example.invalid/function"),
            proposal(),
        )

        self.assert_public_error(
            status,
            body,
            503,
            "authentication_unavailable",
            (sensitive, "az login", "example.invalid"),
        )

    @patch("server.acquire_azure_cli_token", return_value="test-token")
    @patch("server.request.urlopen")
    def test_fabric_timeout_response_is_sanitized(self, urlopen, _token) -> None:
        urlopen.side_effect = error.URLError(socket.timeout("secret-timeout"))

        status, body = self.post(
            self.run_server("fabric", "https://example.invalid/function"),
            proposal(),
        )

        self.assert_public_error(
            status,
            body,
            504,
            "upstream_timeout",
            ("secret-timeout", "120", "example.invalid"),
        )

    def test_invalid_request_response_is_stable(self) -> None:
        invalid = proposal()
        invalid["account_name"] = " "

        status, body = self.post(self.run_server("local"), invalid)

        self.assert_public_error(
            status,
            body,
            400,
            "invalid_request",
            ("account_name", "non-empty"),
        )

    @patch(
        "server.evaluate_request",
        side_effect=RuntimeError("secret internal stack detail"),
    )
    def test_internal_error_response_is_sanitized(self, _evaluate) -> None:
        status, body = self.post(self.run_server("local"), proposal())

        self.assert_public_error(
            status,
            body,
            500,
            "internal_error",
            ("secret internal stack detail", "RuntimeError"),
        )


if __name__ == "__main__":
    unittest.main()
