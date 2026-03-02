"""
generate_test_data.py
=====================
Generates 4 sample datasets for the Kantar Media Data Mapping Agent demo.

Standard Kantar media schema columns:
    Date | Category | Sub_Category | Channel | Publisher | Spends | Impressions

Files generated inside data/ directory:
  1. media_standard.csv         – 90 rows, columns match the standard schema exactly.
                                   <- Raw dataset for demonstration.
  2. media_alias_names.xlsx     – Same data but column headers use aliases/synonyms.
                                   <- Demonstrates Tool 1 (Rename Tool).
  3. media_wide_format.xlsx     – Dates spread as column headers (wide/pivoted).
                                   <- Demonstrates Tool 2 (Un-pivot Tool).
  4. media_hierarchical_date.xlsx – Date split into Day_Name, Day_Num, Month, Year cols.
                                   <- Demonstrates Tool 3 (Hierarchical Unstack Tool).

Run:
    python generate_test_data.py
"""

from __future__ import annotations

import calendar
import random
from pathlib import Path

import pandas as pd

SEED = 42
random.seed(SEED)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Media advertising reference data (Kantar-style)
# ---------------------------------------------------------------------------

CATEGORY_SUBCATEGORY: dict[str, list[str]] = {
    "Haircare":         ["Shampoo", "Conditioner", "Hair Oil", "Hair Colour", "Styling Gel"],
    "Skincare":         ["Face Wash", "Moisturiser", "Sunscreen", "Serum", "Toner"],
    "Beverages":        ["Cola", "Juice", "Energy Drink", "Tea", "Coffee"],
    "Electronics":      ["Smartphone", "Laptop", "Smart TV", "Earphones", "Tablet"],
    "Food & Nutrition": ["Protein Bar", "Cereal", "Cooking Oil", "Snacks", "Dairy"],
    "Automotive":       ["Sedan", "SUV", "Two-Wheeler", "Electric Vehicle", "Accessories"],
    "Fashion":          ["Apparel", "Footwear", "Watches", "Bags", "Jewellery"],
    "Insurance":        ["Life Insurance", "Health Insurance", "Motor Insurance", "Term Plan"],
    "FMCG":             ["Detergent", "Soap", "Toothpaste", "Deodorant", "Sanitiser"],
}

CHANNEL_PUBLISHER: dict[str, list[str]] = {
    "TV":      ["Sony LIV", "Star Plus", "Zee TV", "Colors", "Sun TV", "DD National"],
    "Digital": ["Google", "Facebook", "Instagram", "YouTube", "Snapchat", "Twitter"],
    "Radio":   ["Red FM", "Radio Mirchi", "Big FM", "Fever 104"],
    "OOH":     ["Clear Channel", "JCDecaux", "Times OOH", "Laqshya Media"],
    "Print":   ["Times of India", "Hindustan Times", "The Hindu", "Deccan Chronicle"],
    "Cinema":  ["PVR Cinemas", "INOX Leisure", "Carnival Cinemas"],
}

SPENDS_RANGE: dict[str, tuple[int, int]] = {
    "TV":      (50_000, 500_000),
    "Digital": (10_000, 200_000),
    "Radio":   (5_000,  50_000),
    "OOH":     (20_000, 150_000),
    "Print":   (15_000, 120_000),
    "Cinema":  (8_000,  80_000),
}

IMPRESSIONS_RANGE: dict[str, tuple[int, int]] = {
    "TV":      (500_000,  5_000_000),
    "Digital": (100_000,  2_000_000),
    "Radio":   (50_000,   500_000),
    "OOH":     (200_000,  1_500_000),
    "Print":   (80_000,   800_000),
    "Cinema":  (30_000,   300_000),
}


def _random_row(date: pd.Timestamp) -> dict:
    """Generate one Kantar-style media advertising row."""
    category    = random.choice(list(CATEGORY_SUBCATEGORY.keys()))
    sub_cat     = random.choice(CATEGORY_SUBCATEGORY[category])
    channel     = random.choice(list(CHANNEL_PUBLISHER.keys()))
    publisher   = random.choice(CHANNEL_PUBLISHER[channel])
    spends      = round(random.uniform(*SPENDS_RANGE[channel]), 2)
    impressions = random.randint(*IMPRESSIONS_RANGE[channel])
    return {
        "Date":         date.strftime("%d/%m/%Y"),
        "Category":     category,
        "Sub_Category": sub_cat,
        "Channel":      channel,
        "Publisher":    publisher,
        "Spends":       spends,
        "Impressions":  impressions,
    }


def make_standard_df(n: int = 90) -> pd.DataFrame:
    """Create n rows of Kantar-style media advertising data."""
    dates = pd.date_range("2023-01-07", periods=n, freq="W-SAT")  # weekly Saturdays
    return pd.DataFrame([_random_row(d) for d in dates])


# ---------------------------------------------------------------------------
# 1. Standard CSV  — Raw media dataset (matches schema exactly)
# ---------------------------------------------------------------------------

def gen_standard_csv():
    """
    90-row Kantar-style media advertising dataset.
    Example row: 07/01/2023 | Haircare | Shampoo | TV | Sony LIV | 51000.00 | 1300000
    """
    df = make_standard_df(90)
    path = DATA_DIR / "media_standard.csv"
    df.to_csv(path, index=False)
    print(f"[OK] {path}  ({len(df)} rows)  — raw media dataset")


# ---------------------------------------------------------------------------
# 2. Alias column names Excel  — Demonstrates TOOL 1 (Rename Tool)
# ---------------------------------------------------------------------------

def gen_alias_names_xlsx():
    """
    Same 90-row dataset but with aliased column headers.
    The Rename Tool should identify and normalise these back to standard names:
      Ad_Date     → Date
      Sector      → Category
      Sub-type    → Sub_Category
      Medium      → Channel
      Distributor → Publisher    ← key example from requirements
      Expenditure → Spends       ← key example from requirements
      Eyeballs    → Impressions
    """
    df = make_standard_df(90)
    df = df.rename(columns={
        "Date":         "Ad_Date",
        "Category":     "Sector",
        "Sub_Category": "Sub-type",
        "Channel":      "Medium",
        "Publisher":    "Distributor",
        "Spends":       "Expenditure",
        "Impressions":  "Eyeballs",
    })
    path = DATA_DIR / "media_alias_names.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as w:
        df.to_excel(w, index=False)
    print(f"[OK] {path}  ({len(df)} rows)  — Tool 1 (Rename) demo")


# ---------------------------------------------------------------------------
# 3. Wide / pivoted format Excel  — Demonstrates TOOL 2 (Un-pivot Tool)
#    Spends values spread across date-labelled columns instead of one Date col.
# ---------------------------------------------------------------------------

def gen_wide_format_xlsx():
    """
    Dataset where 12 date columns hold Spend values instead of a single Date column.
    Layout:
      Category | Sub_Category | Channel | Publisher | Impressions | 07/01/2023 | 14/01/2023 | ...
    The Un-pivot Tool should melt these date-header columns back into:
      Date | Category | Sub_Category | Channel | Publisher | Impressions | Spends
    Note: Impressions is kept as a fixed id-var column (total reach for the combo).
    """
    random.seed(SEED)
    combos = []
    for cat, sub_list in CATEGORY_SUBCATEGORY.items():
        for sub in sub_list[:1]:                         # 1 sub-cat per category
            for ch, pubs in CHANNEL_PUBLISHER.items():
                combos.append({
                    "Category":     cat,
                    "Sub_Category": sub,
                    "Channel":      ch,
                    "Publisher":    random.choice(pubs),
                    # Impressions as a fixed column (total reach for this combo)
                    "Impressions":  random.randint(*IMPRESSIONS_RANGE[ch]),
                })

    date_cols = pd.date_range("2023-01-07", periods=12, freq="W-SAT")

    rows = []
    for combo in combos[:90]:
        row = dict(combo)
        for d in date_cols:
            ch    = combo["Channel"]
            row[d.strftime("%d/%m/%Y")] = round(random.uniform(*SPENDS_RANGE[ch]), 2)
        rows.append(row)

    df = pd.DataFrame(rows)
    path = DATA_DIR / "media_wide_format.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as w:
        df.to_excel(w, index=False)
    print(
        f"[OK] {path}  ({len(df)} id-rows × {len(date_cols)} date-columns)"
        f"  — Tool 2 (Un-pivot) demo"
    )


# ---------------------------------------------------------------------------
# 4. Hierarchical date Excel  — Demonstrates TOOL 3 (Hierarchical Unstack)
# ---------------------------------------------------------------------------

def gen_hierarchical_date_xlsx():
    """
    Date is split across 4 separate columns:
      Day_Name  (Saturday)  |  Day_Num (7)  |  Month (1)  |  Year (2023)
    The Hierarchical Unstack Tool should produce: "07/01/2023, Saturday"
    Other standard columns remain: Category, Sub_Category, Channel, Publisher,
    Spends, Impressions.
    """
    random.seed(SEED)
    rows = []
    dates = pd.date_range("2023-01-07", periods=90, freq="W-SAT")
    for d in dates:
        cat = random.choice(list(CATEGORY_SUBCATEGORY.keys()))
        sub = random.choice(CATEGORY_SUBCATEGORY[cat])
        ch  = random.choice(list(CHANNEL_PUBLISHER.keys()))
        pub = random.choice(CHANNEL_PUBLISHER[ch])
        rows.append({
            "Day_Name":     calendar.day_name[d.weekday()],  # "Saturday"
            "Day_Num":      d.day,                            # 7
            "Month":        d.month,                          # 1
            "Year":         d.year,                           # 2023
            "Category":     cat,
            "Sub_Category": sub,
            "Channel":      ch,
            "Publisher":    pub,
            "Spends":       round(random.uniform(*SPENDS_RANGE[ch]), 2),
            "Impressions":  random.randint(*IMPRESSIONS_RANGE[ch]),
        })

    df = pd.DataFrame(rows)
    path = DATA_DIR / "media_hierarchical_date.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as w:
        df.to_excel(w, index=False)
    print(f"[OK] {path}  ({len(df)} rows)  — Tool 3 (Hierarchical Unstack) demo")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Generating Kantar Media sample datasets...\n")
    gen_standard_csv()
    gen_alias_names_xlsx()
    gen_wide_format_xlsx()
    gen_hierarchical_date_xlsx()
    print(
        "\n✅  All datasets written to data/\n"
        "    media_standard.csv            <- raw 90-row media dataset\n"
        "    media_alias_names.xlsx        <- Tool 1 (Rename) demo\n"
        "    media_wide_format.xlsx        <- Tool 2 (Un-pivot) demo\n"
        "    media_hierarchical_date.xlsx  <- Tool 3 (Hierarchical Unstack) demo\n"
    )

