"""Content-based session recommendations, not a trained model or serving application."""

import datetime
import hashlib
import json
import uuid

FIELDS = ("viewer_id", "session_id", "content_id", "action", "occurred_at")
ACTIONS = ("click", "search", "watch", "skip", "like")
REFRESH_ACTIONS = ("watch", "like")
METHOD = "shared_tags_then_popularity"


def utc_time(value):
    if not isinstance(value, str):
        raise ValueError("Viewer timestamps must be ISO 8601 strings.")
    instant = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Viewer timestamps must use timezone-aware UTC.")
    return instant


def validate_event(payload):
    if not isinstance(payload, dict) or set(payload) != set(FIELDS):
        raise ValueError("Supply the exact five original viewer event fields.")
    for field in FIELDS:
        value = payload[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError(f"Invalid viewer event field {field}.")
    if payload["action"] not in ACTIONS:
        raise ValueError("Use a supported lowercase viewer interaction.")
    utc_time(payload["occurred_at"])
    return dict(payload)


def catalog_items(catalog):
    if (not isinstance(catalog, dict) or set(catalog) != {"catalog_id", "items"}
            or not isinstance(catalog["catalog_id"], str) or not catalog["catalog_id"].strip()
            or not isinstance(catalog["items"], list) or not 4 <= len(catalog["items"]) <= 1000):
        raise ValueError("Supply a named content catalog with 4-1000 items.")
    by_id = {}
    for item in catalog["items"]:
        if (not isinstance(item, dict) or set(item) != {"content_id", "tags", "popularity"}
                or not isinstance(item["content_id"], str) or not item["content_id"].strip()
                or len(item["content_id"]) > 128 or item["content_id"] in by_id
                or type(item["popularity"]) is not int or not 0 <= item["popularity"] <= 100
                or not isinstance(item["tags"], list) or not item["tags"]
                or any(not isinstance(tag, str) or not tag.strip() for tag in item["tags"])
                or len(set(item["tags"])) != len(item["tags"])):
            raise ValueError("Catalog items require distinct IDs, tags and integer popularity from 0 to 100.")
        by_id[item["content_id"]] = item
    return by_id


def recommendation_id(payload):
    validate_event(payload)
    key = [payload[name] for name in FIELDS]
    key[-1] = utc_time(payload["occurred_at"]).isoformat()
    return hashlib.sha256(json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def make_recommendations(payload, catalog, rule_id=None):
    payload = validate_event(payload)
    if payload["action"] not in REFRESH_ACTIONS:
        raise ValueError("Only watch and like interactions may refresh recommendations.")
    items = catalog_items(catalog)
    if payload["content_id"] not in items:
        raise ValueError("The interaction content is missing from the configured catalog.")
    tags = set(items[payload["content_id"]]["tags"])
    candidates = []
    for content_id, item in items.items():
        overlap = len(tags.intersection(item["tags"]))
        if content_id != payload["content_id"] and overlap:
            candidates.append({"content_id": content_id, "shared_tag_count": overlap,
                               "popularity": item["popularity"]})
    candidates.sort(key=lambda item: (-item["shared_tag_count"], -item["popularity"], item["content_id"]))
    if len(candidates) < 3:
        raise ValueError("The catalog must provide at least three eligible recommendations.")
    catalog_hash = hashlib.sha256(json.dumps(
        catalog, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    record = {
        **payload, "recommendation_id": recommendation_id(payload), "status": "ready_for_serving",
        "method": METHOD, "catalog_id": catalog["catalog_id"], "catalog_hash": catalog_hash,
        "recommendations": candidates[:3],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if rule_id is not None:
        record["rule_id"] = str(uuid.UUID(rule_id))
    return record


def check_recommendations(actual, expected):
    if (not isinstance(actual, dict) or set(actual) != set(expected)
            or json.dumps({key: value for key, value in actual.items() if key != "created_at"},
                          sort_keys=True, allow_nan=False) !=
            json.dumps({key: value for key, value in expected.items() if key != "created_at"},
                       sort_keys=True, allow_nan=False)):
        raise ValueError("The persisted recommendations conflict with the original interaction or ranking.")
    utc_time(actual["created_at"])
    return actual
