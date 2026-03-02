"""
schema.py — Target schema definition for the Kantar Media Data Mapping Agent.

This defines the *standard* Kantar-style media advertising schema.
Editing this dict is the ONLY change required when the business schema evolves.

Standard media columns:
  Date | Category | Sub_Category | Channel | Publisher | Spends | Impressions
"""

from __future__ import annotations
from typing import Any

# ---------------------------------------------------------------------------
# TARGET SCHEMA
# Each key = canonical (standard) column name in the output.
# Each value:
#   "dtype"       → expected pandas dtype  (str | int | float | date)
#   "required"    → bool – must be present in output
#   "description" → plain-English hint fed to the LLM when mapping
#   "aliases"     → list of common alternative names (used by heuristic matching)
# ---------------------------------------------------------------------------

TARGET_SCHEMA: dict[str, dict[str, Any]] = {
    "Date": {
        "dtype": "date",
        "required": True,
        "description": "Calendar date of the advertising record (DD/MM/YYYY). May also appear as split day/month/year columns or as wide column headers.",
        "aliases": [
            "date", "day", "report_date", "transaction_date", "period",
            "week", "month", "ad_date", "media_date", "flight_date", "air_date",
        ],
    },
    "Category": {
        "dtype": "str",
        "required": True,
        "description": "High-level product/brand category being advertised (e.g. Haircare, Beverages, Electronics).",
        "aliases": [
            "category", "product_category", "brand_category", "sector",
            "vertical", "segment", "product_type", "industry", "type",
        ],
    },
    "Sub_Category": {
        "dtype": "str",
        "required": True,
        "description": "Granular sub-category under the main category (e.g. Shampoo under Haircare, Cola under Beverages).",
        "aliases": [
            "sub_category", "subcategory", "sub category", "sub-category",
            "product_sub_category", "sub_type", "sub_segment", "variant",
            "product_variant", "item_type", "subtype", "product_group",
        ],
    },
    "Channel": {
        "dtype": "str",
        "required": True,
        "description": "Advertising channel or medium (e.g. TV, Digital, Radio, OOH, Print, Cinema).",
        "aliases": [
            "channel", "medium", "media_channel", "ad_channel", "media_type",
            "platform", "media", "network", "source", "ad_type", "media_format",
        ],
    },
    "Publisher": {
        "dtype": "str",
        "required": True,
        "description": "Publisher or media house serving the ad (e.g. Sony, Star, Zee, Google, Times of India).",
        "aliases": [
            "publisher", "distributor", "vendor", "media_owner", "broadcaster",
            "network_name", "station", "site", "media_partner", "partner",
            "ad_network", "outlet", "operator", "provider", "seller",
        ],
    },
    "Spends": {
        "dtype": "float",
        "required": True,
        "description": "Amount of money invested / spent on the advertisement (in currency units, e.g. USD).",
        "aliases": [
            "spends", "spend", "cost", "expenditure", "investment", "budget",
            "media_cost", "ad_spend", "amount_spent", "budget_spent",
            "media_spend", "ad_cost", "amount", "total_spend", "total_cost",
        ],
    },
    "Impressions": {
        "dtype": "int",
        "required": True,
        "description": "Number of times the advertisement was served / shown to the audience.",
        "aliases": [
            "impressions", "impr", "views", "reach", "exposures", "ads_served",
            "grps", "trps", "eyeballs", "ad_impressions", "total_impressions",
            "total_views", "audience", "contacts",
        ],
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS: list[str] = [k for k, v in TARGET_SCHEMA.items() if v["required"]]
ALL_COLUMNS: list[str] = list(TARGET_SCHEMA.keys())


def schema_description_for_llm() -> str:
    """Return a compact text representation of the schema for LLM prompts."""
    lines = [
        "=== KANTAR MEDIA STANDARD SCHEMA ===",
        "All 7 columns are REQUIRED in the final output.",
        "",
        "Canonical column  |  dtype  |  description",
        "-" * 70,
    ]
    for col, meta in TARGET_SCHEMA.items():
        req = "REQUIRED" if meta["required"] else "optional"
        lines.append(f"  {col:<18} ({meta['dtype']:<7}, {req}): {meta['description']}")
    lines.append("\nCommon aliases per column (for reference):")
    for col, meta in TARGET_SCHEMA.items():
        lines.append(f"  {col}: {', '.join(meta['aliases'][:6])}")
    return "\n".join(lines)
