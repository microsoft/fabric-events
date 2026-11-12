"""Validate the campaign lookup before creating its native Delta reference table."""


def validate_catalog(rows):
    fields = {"catalog_tier", "catalog_store_id", "campaign_id", "benefit_code", "is_active"}
    if not isinstance(rows, list) or not rows:
        raise ValueError("Provide a nonempty campaign catalog.")
    keys = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("Each campaign row requires the five declared catalog fields.")
        if any(not isinstance(row[field], str) or not row[field].strip() or row[field] != row[field].strip()
               for field in fields - {"is_active"}):
            raise ValueError("Campaign lookup keys and benefit codes must be nonempty trimmed strings.")
        if type(row["is_active"]) is not bool:
            raise ValueError("Campaign activity must be a boolean.")
        key = row["catalog_tier"], row["catalog_store_id"]
        if key in keys:
            raise ValueError("Each tier and store must have exactly one campaign mapping.")
        keys.add(key)
    return rows
