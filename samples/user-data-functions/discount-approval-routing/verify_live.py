import argparse
import json

from server import DEFAULT_TIMEOUT_SECONDS, invoke_fabric_udf, validate_proposal


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke a configured live discount approval UDF."
    )
    parser.add_argument("--udf-url", required=True)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    args = parser.parse_args()

    proposal = validate_proposal(
        {
            "deal_id": "DEAL-LIVE-VERIFY-001",
            "account_name": "Sample Account",
            "region": "West",
            "deal_value": 450000,
            "requested_discount_percent": 18,
            "estimated_margin_percent": 22,
            "event_time": "2026-09-15T17:15:00Z",
        }
    )
    result = invoke_fabric_udf(proposal, args.udf_url, args.timeout)
    print(json.dumps(result, indent=2))
    if (
        result["approval_level"] != "executive"
        or result["publication_requested"] is not True
    ):
        raise SystemExit("The live UDF returned an unexpected policy result.")
    print(
        "Publication was requested during UDF execution. Verify the matching "
        "deal_id separately in the Business Event publisher preview and "
        "Eventstream Data preview."
    )


if __name__ == "__main__":
    main()
