"""
tests/test_agent.py — Pytest unit tests for the Data Mapping Agent.

Run with:
    pytest tests/ -v
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.agent import ColumnMapping, DataMappingAgent
from agent.schema import ALL_COLUMNS, REQUIRED_COLUMNS, TARGET_SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> DataMappingAgent:
    """Agent without Gemini API key — heuristic-only mode."""
    return DataMappingAgent(api_key=None)


@pytest.fixture
def standard_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "Category": ["Haircare", "Beverages", "Haircare"],
            "Sub_Category": ["Shampoo", "Cola", "Conditioner"],
            "Channel": ["TV", "Digital", "TV"],
            "Publisher": ["Sony", "Google", "Star"],
            "Spends": [51_000.0, 32_000.0, 45_000.0],
            "Impressions": [1_300_000, 800_000, 1_100_000],
        }
    )


@pytest.fixture
def alias_df() -> pd.DataFrame:
    """DataFrame with Kantar-schema alias column names."""
    return pd.DataFrame(
        {
            "Report Date": pd.to_datetime(["2024-02-01", "2024-02-02"]),
            "Sector": ["Haircare", "Beverages"],
            "Variant": ["Shampoo", "Cola"],
            "Medium": ["TV", "Digital"],
            "Distributor": ["Sony", "Google"],
            "Cost": [250.0, 400.0],
            "Eyeballs": [5_000, 8_000],
        }
    )


@pytest.fixture
def split_date_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "day": [1, 15, 28],
            "month": [3, 3, 3],
            "year": [2024, 2024, 2024],
            "Campaign_Name": ["Alpha", "Beta", "Gamma"],
            "Channel": ["Google Ads", "Facebook", "Email"],
            "Impressions": [10_000, 20_000, 5_000],
            "Clicks": [200, 400, 100],
            "Spend": [500.0, 1000.0, 250.0],
        }
    )


@pytest.fixture
def wide_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Campaign_Name": ["Alpha", "Beta"],
            "Channel": ["Google Ads", "Facebook"],
            "Jan-2024": [1000.0, 2000.0],
            "Feb-2024": [1500.0, 2500.0],
            "Mar-2024": [1200.0, 1800.0],
        }
    )


# ---------------------------------------------------------------------------
# Tests — Ingestion
# ---------------------------------------------------------------------------


class TestIngestion:
    def test_ingest_csv(self, agent, standard_df, tmp_path):
        csv_path = tmp_path / "test.csv"
        standard_df.to_csv(csv_path, index=False)
        df = agent.ingest(csv_path, filename="test.csv")
        assert df.shape[0] == 3
        assert "Category" in df.columns  # Kantar schema

    def test_ingest_excel(self, agent, standard_df, tmp_path):
        xl_path = tmp_path / "test.xlsx"
        with pd.ExcelWriter(xl_path, engine="xlsxwriter") as w:
            standard_df.to_excel(w, index=False)
        df = agent.ingest(xl_path, filename="test.xlsx")
        assert df.shape[0] == 3

    def test_ingest_bytesio_csv(self, agent, standard_df):
        buf = io.BytesIO(standard_df.to_csv(index=False).encode())
        df = agent.ingest(buf, filename="test.csv")
        assert len(df) == 3

    def test_ingest_bad_file_raises(self, agent, tmp_path):
        # A binary file with .xlsx extension but garbage content should fail Excel parsing
        bad = tmp_path / "bad.xlsx"
        bad.write_bytes(b"PK\x00\x00THIS_IS_GARBAGE_CONTENT_NOT_A_ZIP")
        with pytest.raises(Exception):
            agent.ingest(bad, filename="bad.xlsx")


# ---------------------------------------------------------------------------
# Tests — Heuristic Mapping
# ---------------------------------------------------------------------------


class TestHeuristicMapping:
    def test_exact_alias_match(self, agent, alias_df):
        mappings = agent.suggest_mappings(alias_df)
        # "Cost" → Spends (Kantar schema)
        assert mappings["Cost"].target_column == "Spends"
        assert mappings["Cost"].confidence > 0.5

    def test_standard_columns_high_confidence(self, agent, standard_df):
        mappings = agent.suggest_mappings(standard_df)
        for col in standard_df.columns:
            if col in ALL_COLUMNS:
                assert mappings[col].target_column == col
                assert mappings[col].confidence >= 0.70

    def test_unmapped_column_gets_none(self, agent):
        df = pd.DataFrame({"XYZ_UnknownCol_999": [1, 2, 3]})
        mappings = agent.suggest_mappings(df)
        # Should exist but may have low confidence or None target
        assert "XYZ_UnknownCol_999" in mappings


# ---------------------------------------------------------------------------
# Tests — Transformations
# ---------------------------------------------------------------------------


class TestTransformations:
    def test_combine_split_date(self, agent, split_date_df):
        df_out = agent.combine_split_date(split_date_df, "year", "month", "day")
        assert "Date" in df_out.columns
        assert "day" not in df_out.columns
        # Tool 3 returns formatted date strings (DD/MM/YYYY) for Excel readability
        assert df_out["Date"].iloc[0] == "01/03/2024"

    def test_melt_wide_format(self, agent, wide_df):
        id_vars = ["Campaign_Name", "Channel"]
        value_vars = ["Jan-2024", "Feb-2024", "Mar-2024"]
        df_long = agent.apply_melt(wide_df, id_vars=id_vars, value_vars=value_vars)
        assert "Period" in df_long.columns
        assert "Value" in df_long.columns
        assert len(df_long) == 2 * 3  # 2 campaigns × 3 months

    def test_detect_wide_format(self, agent, wide_df):
        info = agent.detect_wide_format(wide_df)
        assert info["is_wide"] is True
        assert len(info["value_vars"]) == 3

    def test_detect_split_date(self, agent, split_date_df):
        info = agent.detect_split_date(split_date_df)
        assert info["has_split_date"] is True
        assert info["year_col"] == "year"
        assert info["month_col"] == "month"
        assert info["day_col"] == "day"

    def test_detect_no_wide_format(self, agent, standard_df):
        info = agent.detect_wide_format(standard_df)
        assert info["is_wide"] is False

    def test_detect_no_split_date(self, agent, standard_df):
        info = agent.detect_split_date(standard_df)
        assert info["has_split_date"] is False

    def test_detect_schema_wide_format(self, agent):
        """Tool 2b: Channel values (TV, Digital, Radio) used as column headers."""
        df = pd.DataFrame({
            "Category": ["Haircare", "Beverages"],
            "Publisher": ["Sony", "Google"],
            "TV": [51_000.0, 32_000.0],
            "Digital": [28_000.0, 45_000.0],
            "Radio": [12_000.0, 8_000.0],
        })
        info = agent.detect_schema_wide_format(df)
        assert info["is_schema_wide"] is True
        assert info["schema_field"] == "Channel"
        assert "TV" in info["value_vars"]
        assert "Digital" in info["value_vars"]
        assert "Radio" in info["value_vars"]

    def test_schema_wide_melt_produces_long_format(self, agent):
        """Tool 2b: Melt schema-wide (Channel as headers) produces Channel + Spends cols."""
        df = pd.DataFrame({
            "Category": ["Haircare", "Beverages"],
            "Publisher": ["Sony", "Google"],
            "TV": [51_000.0, 32_000.0],
            "Digital": [28_000.0, 45_000.0],
        })
        df_long = agent.apply_unpivot_tool(
            df,
            id_vars=["Category", "Publisher"],
            value_vars=["TV", "Digital"],
            var_name="Channel",
            value_name="Spends",
        )
        assert "Channel" in df_long.columns
        assert "Spends" in df_long.columns
        assert len(df_long) == 4  # 2 rows × 2 channels
        assert set(df_long["Channel"].unique()) == {"TV", "Digital"}

    def test_detect_metric_hierarchy(self, agent):
        """Tool 3b: TV_Spends, Digital_Spends, Radio_Spends detected as metric hierarchy."""
        df = pd.DataFrame({
            "Date": ["01/01/2024", "02/01/2024"],
            "Category": ["Haircare", "Beverages"],
            "tv_spends": [51_000.0, 32_000.0],
            "digital_spends": [28_000.0, 45_000.0],
            "radio_spends": [12_000.0, 8_000.0],
        })
        info = agent.detect_metric_hierarchy(df)
        assert info["has_metric_hierarchy"] is True
        assert info["metric_col"] == "Spends"
        assert info["split_field"] == "Channel"
        assert len(info["matched_columns"]) == 3

    def test_metric_hierarchy_unstack(self, agent):
        """Tool 3b: apply_metric_hierarchy_unstack produces long format with split field."""
        df = pd.DataFrame({
            "Category": ["Haircare", "Beverages"],
            "tv_spends": [51_000.0, 32_000.0],
            "digital_spends": [28_000.0, 45_000.0],
        })
        df_long = agent.apply_metric_hierarchy_unstack(
            df,
            matched_columns=["tv_spends", "digital_spends"],
            id_vars=["Category"],
            metric_col="Spends",
            split_field="Channel",
        )
        assert "Channel" in df_long.columns
        assert "Spends" in df_long.columns
        assert len(df_long) == 4  # 2 rows × 2 channels
        # Channel labels should have metric prefix/suffix stripped
        assert "tv" in df_long["Channel"].str.lower().tolist() or \
               "TV" in df_long["Channel"].tolist() or \
               all("spends" not in v.lower() for v in df_long["Channel"].tolist())


# ---------------------------------------------------------------------------
# Tests — Apply Mapping
# ---------------------------------------------------------------------------


class TestApplyMapping:
    def test_rename_columns(self, agent, alias_df):
        mappings = {
            "Report Date": ColumnMapping(source_column="Report Date", target_column="Date",
                                         confidence=0.95, reasoning="test"),
            "Sector":      ColumnMapping(source_column="Sector", target_column="Category",
                                         confidence=0.95, reasoning="test"),
            "Variant":     ColumnMapping(source_column="Variant", target_column="Sub_Category",
                                         confidence=0.95, reasoning="test"),
            "Medium":      ColumnMapping(source_column="Medium", target_column="Channel",
                                         confidence=0.95, reasoning="test"),
            "Distributor": ColumnMapping(source_column="Distributor", target_column="Publisher",
                                         confidence=0.90, reasoning="test"),
            "Cost":        ColumnMapping(source_column="Cost", target_column="Spends",
                                         confidence=0.90, reasoning="test"),
            "Eyeballs":    ColumnMapping(source_column="Eyeballs", target_column="Impressions",
                                         confidence=0.90, reasoning="test"),
        }
        df_out = agent.apply_mapping(alias_df, mappings)
        assert "Date" in df_out.columns
        assert "Spends" in df_out.columns
        assert "Publisher" in df_out.columns
        assert "Report Date" not in df_out.columns
        assert "Cost" not in df_out.columns

    def test_drop_unmapped_column(self, agent):
        df = pd.DataFrame({"Agency": ["A", "B"], "Spends": [100.0, 200.0]})
        mappings = {
            "Agency": ColumnMapping(source_column="Agency", target_column=None,
                                    confidence=0.0, reasoning="no match"),
            "Spends": ColumnMapping(source_column="Spends", target_column="Spends",
                                    confidence=0.95, reasoning="exact match"),
        }
        df_out = agent.apply_mapping(df, mappings)
        assert "Agency" not in df_out.columns
        assert "Spends" in df_out.columns


# ---------------------------------------------------------------------------
# Tests — Type Coercion
# ---------------------------------------------------------------------------


class TestTypeCoercion:
    def test_coerce_numeric_columns(self, agent, standard_df):
        df = standard_df.copy()
        df["Impressions"] = df["Impressions"].astype(str)  # Corrupt type
        df_out = agent.coerce_types(df)
        assert pd.api.types.is_integer_dtype(df_out["Impressions"])

    def test_coerce_float_columns(self, agent, standard_df):
        df = standard_df.copy()
        df["Spends"] = df["Spends"].astype(str)  # Kantar schema uses 'Spends'
        df_out = agent.coerce_types(df)
        assert pd.api.types.is_float_dtype(df_out["Spends"])


# ---------------------------------------------------------------------------
# Tests — Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_all_required_present(self, agent, standard_df):
        missing = agent.validate(standard_df)
        assert missing == []

    def test_missing_required_column(self, agent):
        df = pd.DataFrame({"Date": ["2024-01-01"], "Channel": ["TV"]})
        missing = agent.validate(df)
        # Kantar required: Date, Category, Sub_Category, Channel, Publisher, Spends, Impressions
        assert "Category" in missing
        assert "Sub_Category" in missing
        assert "Publisher" in missing
        assert "Spends" in missing
        assert "Impressions" in missing


# ---------------------------------------------------------------------------
# Tests — Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_returns_bytes(self, agent, standard_df):
        excel_bytes = agent.export_excel(standard_df)
        assert isinstance(excel_bytes, bytes)
        assert len(excel_bytes) > 0

    def test_export_has_two_sheets(self, agent, standard_df):
        excel_bytes = agent.export_excel(standard_df)
        xl = pd.ExcelFile(io.BytesIO(excel_bytes))
        assert "Results" in xl.sheet_names
        assert "Logs" in xl.sheet_names

    def test_results_sheet_content(self, agent, standard_df):
        excel_bytes = agent.export_excel(standard_df)
        df_results = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Results")
        assert df_results.shape[0] == standard_df.shape[0]

    def test_export_to_disk(self, agent, standard_df, tmp_path):
        out = tmp_path / "output.xlsx"
        agent.export_excel(standard_df, output_path=out)
        assert out.exists()
        assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# Tests — Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_pipeline_standard_csv(self, agent, standard_df, tmp_path):
        csv_path = tmp_path / "in.csv"
        standard_df.to_csv(csv_path, index=False)

        # Suggest
        df_raw, _, suggestions = agent.run_pipeline(csv_path, "in.csv")
        assert suggestions is not None

        # Build confirmed mappings (identity for Kantar standard columns)
        confirmed = {
            col: ColumnMapping(source_column=col, target_column=col,
                               confidence=1.0, reasoning="test")
            for col in ["Date", "Category", "Sub_Category", "Channel", "Publisher", "Spends", "Impressions"]
        }

        # Process
        agent2 = DataMappingAgent()
        df_out, xl_bytes, _ = agent2.run_pipeline(csv_path, "in.csv", confirmed_mappings=confirmed)
        assert df_out.shape[0] == 3
        assert len(xl_bytes) > 0

    def test_pipeline_split_date(self, agent, split_date_df, tmp_path):
        csv_path = tmp_path / "split.csv"
        split_date_df.to_csv(csv_path, index=False)

        confirmed = {
            "Campaign_Name": ColumnMapping(source_column="Campaign_Name",
                                           target_column="Category",
                                           confidence=1.0, reasoning="test"),
            "Channel":       ColumnMapping(source_column="Channel", target_column="Channel",
                                           confidence=1.0, reasoning="test"),
            "Impressions":   ColumnMapping(source_column="Impressions",
                                           target_column="Impressions",
                                           confidence=1.0, reasoning="test"),
            "Clicks":        ColumnMapping(source_column="Clicks", target_column=None,
                                           confidence=0.0, reasoning="not in Kantar schema"),
            "Spend":         ColumnMapping(source_column="Spend", target_column="Spends",
                                           confidence=1.0, reasoning="test"),
        }

        agent2 = DataMappingAgent()
        df_out, xl_bytes, _ = agent2.run_pipeline(csv_path, "split.csv", confirmed_mappings=confirmed)
        assert "Date" in df_out.columns
        assert len(xl_bytes) > 0
