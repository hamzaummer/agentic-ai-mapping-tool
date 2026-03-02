"""
agent.py — Kantar Media Data Mapping Agent (Agentic AI Core)

This agent autonomously processes media advertising datasets and normalises
them to the Kantar standard schema:
    Date | Category | Sub_Category | Channel | Publisher | Spends | Impressions

Tools implemented:
  Tool 1 — Rename Tool
      Detects aliased / synonym column headers (e.g. Distributor → Publisher,
      Expenditure → Spends) and renames them to the standard names.
      Uses Pydantic for full type validation of every mapping.

  Tool 2 — Un-pivot Tool
      Detects wide/pivoted datasets where dates (or other values) appear as
      column headers instead of a single stacked column, and melts them back
      into long format.  Works on date columns AND non-date value columns.

  Tool 3 — Hierarchical Unstack Tool
      Detects split date hierarchies (Day_Name, Day_Num, Month, Year spread
      across multiple columns) and combines them into a single standard Date
      column formatted as DD/MM/YYYY, DayName (e.g. 08/02/2026, Saturday).

Workflow:
  1. Ingest → analyse structure (rows, cols, dtypes, nulls).
  2. Heuristic + LLM column mapping with confidence scores.
  3. LLM verification of all confirmed high-confidence mappings.
  4. Group columns → MAPPED (>=threshold) and NOT-MAPPED (<threshold or None).
  5. Flag anything ambiguous for human review; never assume.
  6. Apply confirmed transformations and export clean Excel.
"""

from __future__ import annotations

import io
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, field_validator

# --- Optional Gemini import (graceful fallback if key missing) ---------------
try:
    from google import genai as _genai  # type: ignore

    _GENAI_AVAILABLE = True
except ImportError:
    _genai = None  # type: ignore
    _GENAI_AVAILABLE = False

from .schema import ALL_COLUMNS, REQUIRED_COLUMNS, TARGET_SCHEMA, schema_description_for_llm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gemini-2.5-flash"
CONFIDENCE_THRESHOLD = 0.70  # below this → flag for human review

# Day-name look-up for hierarchical date parsing
DAY_NAMES_FULL  = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
DAY_NAMES_SHORT = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
MONTH_NAMES     = ["January","February","March","April","May","June",
                   "July","August","September","October","November","December"]
MONTH_SHORT     = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


# ---------------------------------------------------------------------------
# Pydantic model for a single mapping suggestion
# ---------------------------------------------------------------------------


class ColumnMapping(BaseModel):
    source_column: str
    target_column: str | None  # None means "drop / ignore"
    confidence: float  # 0.0 – 1.0
    reasoning: str

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


# ---------------------------------------------------------------------------
# Log entry
# ---------------------------------------------------------------------------


class LogEntry(BaseModel):
    timestamp: str
    operation: str
    detail: str
    status: str  # "ok" | "warning" | "error" | "user_input"


# ---------------------------------------------------------------------------
# Pydantic models for Tool 1 — Rename Tool
# ---------------------------------------------------------------------------

class RenameMapping(BaseModel):
    """Single column rename decision, fully validated by Pydantic."""
    source_column: str
    target_column: str | None      # None → drop this column
    confidence: float
    reasoning: str
    tool: str = "rename"

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class RenameResult(BaseModel):
    """Structured output of the Rename Tool execution."""
    mapped: list[RenameMapping]        # successfully renamed columns
    not_mapped: list[RenameMapping]    # columns with no standard match → will be dropped
    flagged_for_review: list[RenameMapping]  # low-confidence → need human decision
    columns_dropped: list[str]


# ---------------------------------------------------------------------------
# Pydantic model for mapping groups (mapped vs not-mapped)
# ---------------------------------------------------------------------------

class MappingGroup(BaseModel):
    """Grouped result of column mapping analysis."""
    mapped_columns: dict[str, ColumnMapping]      # conf >= threshold, target found
    not_mapped_columns: dict[str, ColumnMapping]  # no target or conf < threshold
    flagged_columns: list[str]                     # columns needing human input


# ---------------------------------------------------------------------------
# Pydantic model for human-review flags
# ---------------------------------------------------------------------------

class HumanFlag(BaseModel):
    """A question the agent cannot resolve autonomously; needs human input."""
    column: str
    question: str
    options: list[str]          # suggested options for the human
    default_suggestion: str | None = None


# ---------------------------------------------------------------------------
# DataMappingAgent
# ---------------------------------------------------------------------------


class DataMappingAgent:
    """End-to-end agent that maps an uploaded file to the target schema."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.model = model
        self.logs: list[LogEntry] = []
        self._client = None

        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if key and _GENAI_AVAILABLE:
            try:
                self._client = _genai.Client(api_key=key)
                self._log("init", f"Gemini client initialised (model={model})", "ok")
            except Exception as exc:
                self._log("init", f"Gemini init failed: {exc}", "warning")
        else:
            self._log(
                "init",
                "No GEMINI_API_KEY or google-genai not installed – using heuristic mapping only.",
                "warning",
            )

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log(self, operation: str, detail: str, status: str = "ok") -> None:
        self.logs.append(
            LogEntry(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                operation=operation,
                detail=detail,
                status=status,
            )
        )

    def logs_as_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([e.model_dump() for e in self.logs])

    # ------------------------------------------------------------------
    # 1. Ingestion
    # ------------------------------------------------------------------

    def ingest(self, source: str | Path | io.BytesIO, filename: str = "") -> pd.DataFrame:
        """Load a CSV or Excel file and return a raw DataFrame."""
        fname = filename or (str(source) if isinstance(source, (str, Path)) else "uploaded_file")
        ext = Path(fname).suffix.lower()

        try:
            if ext in (".xls", ".xlsx", ".xlsm"):
                df = pd.read_excel(source, engine="openpyxl")
            elif ext == ".csv":
                df = pd.read_csv(source)
            else:
                # Try CSV first, fall back to Excel
                try:
                    df = pd.read_csv(source)
                except Exception:
                    df = pd.read_excel(source, engine="openpyxl")

            self._log(
                "ingest",
                f"Loaded '{fname}': {df.shape[0]} rows × {df.shape[1]} cols. "
                f"Columns: {list(df.columns)}",
                "ok",
            )
            return df
        except Exception as exc:
            self._log("ingest", f"Failed to load '{fname}': {exc}", "error")
            raise

    # ------------------------------------------------------------------
    # 2. Heuristic alias matching (fast, no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"[\s_\-]+", "_", name.strip().lower())

    def _heuristic_mapping(self, columns: list[str]) -> dict[str, ColumnMapping]:
        """Quick alias-based matching before calling the LLM.

        Confidence tiers:
          1.00 — Exact column name match (case-insensitive, same as target)
          0.90 — Exact alias match (column normalised form found in alias list)
          0.65 — Partial / substring alias match
        """
        results: dict[str, ColumnMapping] = {}
        alias_index: dict[str, str] = {}
        for target, meta in TARGET_SCHEMA.items():
            for alias in meta["aliases"]:
                alias_index[self._normalize(alias)] = target

        for col in columns:
            norm = self._normalize(col)

            # ── Tier 1: exact column-name match (e.g. "Impressions" → "Impressions") ──
            if col in TARGET_SCHEMA:
                results[col] = ColumnMapping(
                    source_column=col,
                    target_column=col,
                    confidence=1.0,
                    reasoning=f"Exact column-name match: '{col}' is already a standard column.",
                )
                continue

            # ── Tier 2: exact alias match ──────────────────────────────────────────
            if norm in alias_index:
                target = alias_index[norm]
                results[col] = ColumnMapping(
                    source_column=col,
                    target_column=target,
                    confidence=0.90,
                    reasoning=f"Exact alias match: '{col}' → '{target}'",
                )
            else:
                # ── Tier 3: partial / substring alias match ────────────────────────
                matched = [t for alias, t in alias_index.items() if alias in norm or norm in alias]
                if matched:
                    target = matched[0]
                    results[col] = ColumnMapping(
                        source_column=col,
                        target_column=target,
                        confidence=0.65,
                        reasoning=f"Partial alias match: '{col}' ≈ '{target}'",
                    )
        return results

    # ------------------------------------------------------------------
    # 3. LLM-based mapping
    # ------------------------------------------------------------------

    def _llm_mapping(self, columns: list[str], sample_values: dict[str, list]) -> list[ColumnMapping]:
        """Ask Gemini to suggest mappings for given columns."""
        if not self._client:
            return []

        schema_text = schema_description_for_llm()
        cols_text = "\n".join(
            f"  - {c}: sample values = {sample_values.get(c, [])[:3]}" for c in columns
        )

        prompt = f"""You are a data engineering assistant for a Kantar Media advertising analytics platform.
Your task is to map column headers from an uploaded dataset to the standard Kantar media schema.

{schema_text}

The uploaded dataset has these columns (with sample values):
{cols_text}

For each source column, decide which standard target column it maps to (or null if it should be dropped).

Return a JSON array ONLY, with this exact structure:
[
  {{
    "source_column": "<exact source column name>",
    "target_column": "<one of: Date, Category, Sub_Category, Channel, Publisher, Spends, Impressions — or null>",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<one concise sentence explaining the match>"
  }},
  ...
]

Rules:
- Each source column maps to at most ONE target column.
- If a column clearly doesn't match any standard column, set target_column to null.
- Use confidence < 0.70 when uncertain.
- Columns like 'Distributor', 'Vendor', 'Media Owner' → Publisher.
- Columns like 'Expenditure', 'Investment', 'Cost', 'Budget' → Spends.
- Columns like 'Eyeballs', 'Reach', 'Exposures', 'Views' → Impressions.
- Return ONLY valid JSON. No markdown fences, no explanation outside the array.
"""

        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            data = json.loads(raw)
            mappings = [ColumnMapping(**item) for item in data]
            self._log("llm_mapping", f"LLM returned {len(mappings)} mapping suggestions", "ok")
            return mappings
        except Exception as exc:
            self._log("llm_mapping", f"LLM mapping failed: {exc}", "warning")
            return []

    def _llm_verify_mappings(
        self,
        mappings: dict[str, ColumnMapping],
        sample_values: dict[str, list],
    ) -> dict[str, ColumnMapping]:
        """
        Ask the LLM to double-check all HIGH-confidence mappings.
        Returns updated mappings with LLM-adjusted confidence / reasoning.
        """
        if not self._client:
            return mappings

        high_conf = {
            src: m for src, m in mappings.items()
            if m.target_column and m.confidence >= CONFIDENCE_THRESHOLD
        }
        if not high_conf:
            return mappings

        schema_text = schema_description_for_llm()
        pairs_text = "\n".join(
            f"  '{src}' → '{m.target_column}'  (confidence={m.confidence:.0%})  "
            f"sample={sample_values.get(src, [])[:2]}"
            for src, m in high_conf.items()
        )

        prompt = f"""You are auditing proposed column mappings for a Kantar Media dataset.

{schema_text}

The following mappings have been proposed (column → target):
{pairs_text}

For EACH mapping confirm whether it is correct, adjust confidence if needed.
Return a JSON array ONLY:
[
  {{
    "source_column": "<exact source column name>",
    "target_column": "<verified target or null>",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief verification note>"
  }},
  ...
]
Return ONLY valid JSON.
"""
        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            data = json.loads(raw)
            for item in data:
                cm = ColumnMapping(**item)
                # ── SAFETY: only accept target_column if it is a valid schema column ──
                # This prevents the LLM from inventing arbitrary column names and
                # breaking the required-column coverage check.
                if cm.target_column is not None and cm.target_column not in ALL_COLUMNS:
                    self._log(
                        "llm_verify",
                        f"LLM returned invalid target '{cm.target_column}' for '{cm.source_column}' "
                        f"— keeping original mapping.",
                        "warning",
                    )
                    continue  # keep the original heuristic mapping
                if cm.source_column in mappings:
                    # Only update if LLM is more confident OR confirms the same target
                    original = mappings[cm.source_column]
                    same_target = (original.target_column == cm.target_column)
                    if same_target or cm.confidence > original.confidence:
                        mappings[cm.source_column] = cm
            self._log(
                "llm_verify",
                f"LLM verification complete for {len(high_conf)} high-confidence mappings.",
                "ok",
            )
        except Exception as exc:
            self._log("llm_verify", f"LLM verification failed: {exc}", "warning")
        return mappings

    # ------------------------------------------------------------------
    # 4. Build full mapping suggestion set
    # ------------------------------------------------------------------

    def analyse_structure(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Step 1 of workflow: Analyse and summarise the raw dataset structure.
        Returns a dict with rows, columns, dtypes, null counts, sample values.
        """
        info = {
            "rows":         len(df),
            "columns":      len(df.columns),
            "column_names": list(df.columns),
            "dtypes":       {c: str(df[c].dtype) for c in df.columns},
            "null_counts":  {c: int(df[c].isna().sum()) for c in df.columns},
            "sample":       {c: df[c].dropna().head(3).tolist() for c in df.columns},
        }
        self._log(
            "analyse_structure",
            f"File structure: {info['rows']} rows x {info['columns']} columns. "
            f"Columns: {info['column_names']}",
            "ok",
        )
        return info

    def group_mappings(
        self,
        mappings: dict[str, ColumnMapping],
        threshold: float | None = None,
    ) -> MappingGroup:
        """
        Step 4 of workflow: Group column mappings into MAPPED and NOT-MAPPED.

        MAPPED     : target found AND confidence >= threshold.
        NOT-MAPPED : no target OR confidence < threshold → will be dropped.

        Returns a MappingGroup Pydantic model.
        """
        t = threshold if threshold is not None else CONFIDENCE_THRESHOLD
        mapped:     dict[str, ColumnMapping] = {}
        not_mapped: dict[str, ColumnMapping] = {}
        flagged:    list[str] = []

        for src, m in mappings.items():
            if m.target_column and m.confidence >= t:
                mapped[src] = m
            else:
                not_mapped[src] = m
                if m.target_column and m.confidence < t:
                    flagged.append(src)  # has a guess but not confident

        self._log(
            "group_mappings",
            f"Column groups — MAPPED: {len(mapped)}, "
            f"NOT-MAPPED (will drop): {len(not_mapped)}, "
            f"FLAGGED (need review): {len(flagged)}",
            "ok",
        )
        return MappingGroup(
            mapped_columns=mapped,
            not_mapped_columns=not_mapped,
            flagged_columns=flagged,
        )

    def get_human_flags(
        self,
        mappings: dict[str, ColumnMapping],
        df: pd.DataFrame,
        threshold: float | None = None,
    ) -> list[HumanFlag]:
        """
        Step 5-7 of workflow: Identify columns where the agent is unsure and
        needs a human decision.  Returns a list of HumanFlag objects.

        The agent will NEVER silently assume — all ambiguous cases are surfaced.
        Now accepts an optional `threshold` to match the UI-configured value.
        """
        t = threshold if threshold is not None else CONFIDENCE_THRESHOLD
        flags: list[HumanFlag] = []
        sample_values = {c: df[c].dropna().head(3).tolist() for c in df.columns}

        for src, m in mappings.items():
            if m.confidence < t:
                # Build helpful question for the human
                sv = sample_values.get(src, [])
                question = (
                    f"Column '{src}' (sample values: {sv}) could not be confidently "
                    f"mapped (confidence={m.confidence:.0%}, threshold={t:.0%}). "
                    + (f"Best guess: '{m.target_column}'. " if m.target_column else "No match found. ")
                    + "What should this column map to?"
                )
                options = ALL_COLUMNS + ["Drop / Ignore this column"]
                flag = HumanFlag(
                    column=src,
                    question=question,
                    options=options,
                    default_suggestion=m.target_column,
                )
                flags.append(flag)
                self._log(
                    "human_flag",
                    f"FLAG: '{src}' needs human decision — {question}",
                    "user_input",
                )

        return flags

    def suggest_mappings(self, df: pd.DataFrame) -> dict[str, ColumnMapping]:
        """
        Full mapping pipeline:
          1. Heuristic alias matching  (fast, no LLM)
          2. LLM mapping for unresolved / low-confidence columns
          3. LLM verification of all high-confidence mappings
          4. Fill remaining truly unknown columns with confidence=0

        Returns a dict {source_column: ColumnMapping}.
        Use group_mappings() to split into MAPPED / NOT-MAPPED groups.
        """
        columns = list(df.columns)
        sample_values: dict[str, list] = {
            c: df[c].dropna().head(5).tolist() for c in columns
        }

        # Step 1: heuristic pass
        mappings = self._heuristic_mapping(columns)

        # Columns still unresolved or low-confidence → ask LLM
        unresolved = [
            c for c in columns
            if c not in mappings or mappings[c].confidence < CONFIDENCE_THRESHOLD
        ]

        if unresolved and self._client:
            llm_results = self._llm_mapping(unresolved, sample_values)
            for m in llm_results:
                existing = mappings.get(m.source_column)
                if existing is None or m.confidence > existing.confidence:
                    mappings[m.source_column] = m

        # Step 2: LLM double-checks high-confidence matches
        if self._client:
            mappings = self._llm_verify_mappings(mappings, sample_values)

        # Fill remaining truly unknown columns
        for col in columns:
            if col not in mappings:
                mappings[col] = ColumnMapping(
                    source_column=col,
                    target_column=None,
                    confidence=0.0,
                    reasoning="No match found — please decide manually.",
                )

        confident = sum(1 for m in mappings.values() if m.confidence >= CONFIDENCE_THRESHOLD)
        self._log(
            "suggest_mappings",
            f"Mapping complete. Confident ({'>='}{CONFIDENCE_THRESHOLD:.0%}): {confident}, "
            f"Need review: {len(mappings) - confident}",
            "ok",
        )
        return mappings

    # ------------------------------------------------------------------
    # 5. Detect special structural patterns
    # ------------------------------------------------------------------

    def detect_wide_format(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Detect if the DataFrame is in wide (pivoted) format.

        Two patterns are detected:
          A) Period-name columns  : Jan-2024, Feb-2024, Q1-2024, 2024-01
          B) Date-string columns  : 07/01/2023, 14-01-2023, 2023-01-07
             (dates stored as column headers instead of a single Date column)

        Returns a dict with:
          - is_wide (bool)
          - id_vars  : columns that are fixed identifiers
          - value_vars: columns that represent time periods / dates
          - wide_type : "period_name" | "date_string" | None
        """
        # Normalise column names — pandas may read date-header columns from
        # Excel as Timestamp / datetime objects; convert them to "DD/MM/YYYY" strings
        # so the regexes below can match reliably.
        def _col_str(c) -> str:
            if hasattr(c, "strftime"):          # Timestamp / datetime
                return c.strftime("%d/%m/%Y")
            s = str(c)
            # "2023-01-07 00:00:00" → "2023-01-07"
            s = re.sub(r"\s+\d{2}:\d{2}:\d{2}$", "", s).strip()
            return s

        col_map: dict[str, Any] = {_col_str(c): c for c in df.columns}
        col_names = list(col_map.keys())

        # Pattern A: month/quarter/year-range column names
        period_re = re.compile(
            r"""
            ^(
              jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec
              |january|february|march|april|june|july|august
              |september|october|november|december
              |q[1-4]
              |20\d{2}[-_]?\d{0,2}
              |\d{4}[-_/]\d{2}
            )
            """,
            re.IGNORECASE | re.VERBOSE,
        )

        # Pattern B: actual date strings as columns (dd/mm/yyyy, yyyy-mm-dd, etc.)
        date_col_re = re.compile(
            r"^(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}[/\-]\d{2}[/\-]\d{2})$",
            re.IGNORECASE,
        )

        period_vars = [c for c in col_names if period_re.match(c)]
        date_vars   = [c for c in col_names if date_col_re.match(c)]
        value_vars_names  = date_vars if len(date_vars) >= 2 else period_vars

        if len(value_vars_names) >= 2:
            id_var_names = [c for c in col_names if c not in value_vars_names]
            wide_type    = "date_string" if date_vars else "period_name"
            # Map back to original column objects (preserves Timestamps etc.)
            id_vars    = [col_map[c] for c in id_var_names]
            value_vars = [col_map[c] for c in value_vars_names]
            return {
                "is_wide":    True,
                "id_vars":    id_vars,
                "value_vars": value_vars,
                "wide_type":  wide_type,
            }
        return {"is_wide": False, "id_vars": [], "value_vars": [], "wide_type": None}

    def detect_split_date(self, df: pd.DataFrame) -> dict[str, Any]:
        """Legacy alias — calls detect_hierarchical_date()."""
        return self.detect_hierarchical_date(df)

    def detect_hierarchical_date(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Tool 3 detector: checks whether the date is split across multiple hierarchy
        columns such as Day_Name, Day_Num (or Day), Month, Year.

        Recognises:
          day_name_col : column whose values are weekday names (Monday/Tue/…)
          day_num_col  : column with integer day-of-month values (1-31)
          month_col    : column with integer month (1-12) or month name (Jan/…)
          year_col     : column with 4-digit year values (2000-2099)

        Returns a dict with:
          has_split_date (bool) — True when at least year + month + day_num are found
          day_name_col  (str|None)
          day_num_col   (str|None)
          month_col     (str|None)
          year_col      (str|None)
        """
        col_lower = {c.lower(): c for c in df.columns}

        # ---- Regex-based column name hints ----
        day_name_col = next(
            (col_lower[k] for k in col_lower
             if re.search(r"\bday[_\s]?name\b|\bday_of_week\b|\bweekday\b", k)), None
        )
        day_num_col = next(
            (col_lower[k] for k in col_lower
             if re.search(r"\bday[_\s]?(num|no|number)?\b", k)
             and "name" not in k and "week" not in k), None
        )
        month_col = next(
            (col_lower[k] for k in col_lower
             if re.search(r"\bmonth\b", k)), None
        )
        year_col = next(
            (col_lower[k] for k in col_lower
             if re.search(r"\byear\b|\byr\b", k)), None
        )

        # ---- Value-based verification ----
        def _col_looks_like_day_name(col: str) -> bool:
            sample = df[col].dropna().astype(str).str.strip().head(5).tolist()
            valid  = DAY_NAMES_FULL + DAY_NAMES_SHORT
            return any(v[:3].title() in [d[:3] for d in valid] for v in sample)

        def _col_looks_like_day_num(col: str) -> bool:
            try:
                nums = pd.to_numeric(df[col].dropna().head(10), errors="coerce").dropna()
                return nums.between(1, 31).all() and len(nums) > 0
            except Exception:
                return False

        def _col_looks_like_month(col: str) -> bool:
            sample = df[col].dropna().head(10)
            try:
                nums = pd.to_numeric(sample, errors="coerce")
                if nums.notna().all():
                    return nums.between(1, 12).all()
                return sample.astype(str).str.strip().str[:3].str.title().isin(
                    [m[:3] for m in MONTH_NAMES + MONTH_SHORT]
                ).any()
            except Exception:
                return False

        def _col_looks_like_year(col: str) -> bool:
            try:
                nums = pd.to_numeric(df[col].dropna().head(10), errors="coerce").dropna()
                return nums.between(2000, 2099).all() and len(nums) > 0
            except Exception:
                return False

        # Verify candidates via value inspection (fallback: scan all columns)
        if day_name_col and not _col_looks_like_day_name(day_name_col):
            day_name_col = None
        if day_num_col and not _col_looks_like_day_num(day_num_col):
            day_num_col = None
        if month_col and not _col_looks_like_month(month_col):
            month_col = None
        if year_col and not _col_looks_like_year(year_col):
            year_col = None

        # Fallback scan if name-based heuristic missed
        if not year_col:
            for c in df.columns:
                if _col_looks_like_year(c) and c not in [day_name_col, day_num_col, month_col]:
                    year_col = c; break
        if not month_col:
            for c in df.columns:
                if _col_looks_like_month(c) and c not in [day_name_col, day_num_col, year_col]:
                    month_col = c; break
        if not day_num_col:
            for c in df.columns:
                if _col_looks_like_day_num(c) and c not in [day_name_col, month_col, year_col]:
                    day_num_col = c; break
        if not day_name_col:
            for c in df.columns:
                if _col_looks_like_day_name(c) and c not in [day_num_col, month_col, year_col]:
                    day_name_col = c; break

        has_split = bool(year_col and month_col and day_num_col)
        return {
            "has_split_date": has_split,
            "day_name_col":   day_name_col,
            "day_num_col":    day_num_col,
            "month_col":      month_col,
            "year_col":       year_col,
            # legacy keys for backward-compat with app.py
            "day_col":        day_num_col,
        }

    # ------------------------------------------------------------------
    # 5b. Detect schema-column values used as column headers (Tool 2 ext.)
    # ------------------------------------------------------------------

    # Known categorical values per schema field — used for heuristic detection
    _KNOWN_CHANNEL_VALUES = {
        "tv", "digital", "radio", "ooh", "out-of-home", "print", "cinema",
        "online", "mobile", "social", "social media", "youtube", "facebook",
        "instagram", "programmatic", "email", "search", "display",
    }
    _KNOWN_PUBLISHER_VALUES = {
        "sony", "star", "zee", "sun", "google", "facebook", "instagram",
        "youtube", "times", "amazon", "netflix", "spotify", "disney",
        "ndtv", "cnn", "bbc", "viacom", "colors", "hotstar",
    }
    _KNOWN_CATEGORY_VALUES = {
        "haircare", "skincare", "beverages", "electronics", "fmcg",
        "automotive", "fashion", "food", "personal care", "telecom",
        "finance", "health", "pharma", "education", "retail",
    }

    _SCHEMA_CATEGORY_LOOKUP: dict[str, frozenset] = {}

    @classmethod
    def _schema_category_lookup(cls) -> dict[str, frozenset]:
        if not cls._SCHEMA_CATEGORY_LOOKUP:
            cls._SCHEMA_CATEGORY_LOOKUP = {
                "Channel":      frozenset(cls._KNOWN_CHANNEL_VALUES),
                "Publisher":    frozenset(cls._KNOWN_PUBLISHER_VALUES),
                "Category":     frozenset(cls._KNOWN_CATEGORY_VALUES),
            }
        return cls._SCHEMA_CATEGORY_LOOKUP

    def detect_schema_wide_format(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Tool 2 (Extended): Detect when a categorical schema field's values appear
        as column headers instead of being stacked in a single column.

        Example — Channel values used as headers:
          Input:  Category | Publisher | TV    | Digital | Radio
          Output: Category | Publisher | Channel | Spends   (after melt)

        Detection strategy:
          1. Heuristic: Check if ≥2 column headers match known values for
             Channel / Publisher / Category (case-insensitive).
          2. LLM fallback: If heuristic finds ≥1 match but < threshold, ask the
             LLM to confirm and identify the schema field.

        Returns a dict:
          is_schema_wide   (bool)
          schema_field     (str|None)  — e.g. "Channel"
          id_vars          (list[str]) — fixed identifier columns
          value_vars       (list[str]) — columns to melt (the field values)
          suggested_value_col (str)   — suggested name for the melted value column
        """
        lookup = self._schema_category_lookup()
        col_names = [str(c) for c in df.columns]

        best_field: str | None = None
        best_matches: list[str] = []

        for field, known_values in lookup.items():
            matches = [
                c for c in col_names
                if c.strip().lower() in known_values
            ]
            if len(matches) > len(best_matches):
                best_matches = matches
                best_field = field

        # Require at least 2 matching headers for heuristic confidence
        if len(best_matches) >= 2 and best_field:
            id_vars   = [c for c in col_names if c not in best_matches]
            # Suggest a numeric target column for the values
            numeric_schema_cols = [
                k for k, v in TARGET_SCHEMA.items() if v["dtype"] in ("int", "float")
            ]
            # Pick most likely numeric column not already in the df
            existing_numerics = [
                c for c in col_names
                if c in numeric_schema_cols and c in id_vars
            ]
            suggested_val = existing_numerics[0] if existing_numerics else numeric_schema_cols[0]

            self._log(
                "detect_schema_wide",
                f"Schema-wide format detected: field='{best_field}', "
                f"value_vars={best_matches}, id_vars={id_vars}",
                "ok",
            )
            return {
                "is_schema_wide":       True,
                "schema_field":         best_field,
                "id_vars":              id_vars,
                "value_vars":           best_matches,
                "suggested_value_col":  suggested_val,
                "detection_method":     "heuristic",
            }

        # LLM fallback — ask Gemini if any column headers look like categorical values
        if self._client and len(col_names) > 1:
            prompt = f"""You are a data engineering assistant.
Examine the following column headers from an uploaded dataset:
  {col_names}

Determine if 2 or more of these column headers are VALUES of one of these Kantar schema fields:
  Channel (e.g. TV, Digital, Radio, OOH, Print, Cinema)
  Publisher (e.g. Sony, Google, Star, Zee, Facebook, YouTube)
  Category (e.g. Haircare, Beverages, Electronics, FMCG)

If yes, respond with EXACTLY this JSON (no markdown fences):
{{"is_schema_wide": true, "schema_field": "<field name>", "value_columns": ["<col1>", "<col2>", ...]}}

If no, respond with:
{{"is_schema_wide": false}}
"""
            try:
                raw = self._client.models.generate_content(model=self.model, contents=prompt).text.strip()
                raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n")
                result = json.loads(raw)
                if result.get("is_schema_wide") and result.get("schema_field") in TARGET_SCHEMA:
                    val_cols = [c for c in result.get("value_columns", []) if c in col_names]
                    if len(val_cols) >= 2:
                        id_vars = [c for c in col_names if c not in val_cols]
                        numeric_schema_cols = [
                            k for k, v in TARGET_SCHEMA.items() if v["dtype"] in ("int", "float")
                        ]
                        existing_numerics = [c for c in col_names if c in numeric_schema_cols and c in id_vars]
                        suggested_val = existing_numerics[0] if existing_numerics else numeric_schema_cols[0]
                        self._log(
                            "detect_schema_wide",
                            f"LLM detected schema-wide format: field='{result['schema_field']}', "
                            f"value_vars={val_cols}",
                            "ok",
                        )
                        return {
                            "is_schema_wide":       True,
                            "schema_field":         result["schema_field"],
                            "id_vars":              id_vars,
                            "value_vars":           val_cols,
                            "suggested_value_col":  suggested_val,
                            "detection_method":     "llm",
                        }
            except Exception as exc:
                self._log("detect_schema_wide", f"LLM schema-wide detection failed: {exc}", "warning")

        return {
            "is_schema_wide":       False,
            "schema_field":         None,
            "id_vars":              [],
            "value_vars":           [],
            "suggested_value_col":  None,
            "detection_method":     None,
        }

    # ------------------------------------------------------------------
    # 5c. Detect metric-split columns (Tool 3 ext.)
    # ------------------------------------------------------------------

    def detect_metric_hierarchy(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Tool 3 (Extended): Detect when a numeric schema metric is split across
        multiple columns using a categorical value as a prefix or suffix.

        Example patterns:
          TV_Spends, Digital_Spends, Radio_Spends
            → metric='Spends', split_field='Channel' (values: TV, Digital, Radio)
          Impressions_Haircare, Impressions_Beverages
            → metric='Impressions', split_field='Category'
          Q1_Spends, Q2_Spends, Q3_Spends, Q4_Spends
            → metric='Spends', split_field='Period'

        Detection strategy:
          Heuristic: For every pair of schema numeric metrics + categorical
          lookup tables, scan column names matching `{value}_{metric}` or
          `{metric}_{value}` (case-insensitive).  Require ≥2 matches.
          LLM: If heuristic finds ≥1 match, ask Gemini to confirm.

        Returns a dict:
          has_metric_hierarchy (bool)
          metric_col           (str|None)  — e.g. "Spends"
          split_field          (str|None)  — e.g. "Channel"
          split_values         (list[str]) — e.g. ["TV", "Digital", "Radio"]
          matched_columns      (list[str]) — exact column names to melt
          id_vars              (list[str]) — fixed identifier columns
          detection_method     (str|None)
        """
        col_names = [str(c) for c in df.columns]
        col_lower_map = {c.lower(): c for c in col_names}

        numeric_fields = [k for k, v in TARGET_SCHEMA.items() if v["dtype"] in ("int", "float")]
        lookup = self._schema_category_lookup()
        # Add period patterns as a pseudo-category
        period_values = frozenset({"q1", "q2", "q3", "q4", "jan", "feb", "mar", "apr",
                                   "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
                                   "h1", "h2", "fy", "ytd"})
        all_lookups = {**lookup, "Period": period_values}

        best_result: dict[str, Any] = {"has_metric_hierarchy": False}
        best_count = 0

        for metric in numeric_fields:
            metric_l = metric.lower()
            for field, known_vals in all_lookups.items():
                matched_cols: list[str] = []
                split_vals:   list[str] = []

                for col_l, col_orig in col_lower_map.items():
                    # pattern: {value}_{metric}  or {metric}_{value}
                    for val in known_vals:
                        if (col_l == f"{val}_{metric_l}" or col_l == f"{metric_l}_{val}"
                                or col_l.startswith(f"{val}_") and col_l.endswith(f"_{metric_l}")
                                or col_l.startswith(f"{metric_l}_") and col_l.endswith(f"_{val}")):
                            if col_orig not in matched_cols:
                                matched_cols.append(col_orig)
                                split_vals.append(val)
                            break

                if len(matched_cols) >= 2 and len(matched_cols) > best_count:
                    best_count = len(matched_cols)
                    id_vars = [c for c in col_names if c not in matched_cols]
                    best_result = {
                        "has_metric_hierarchy": True,
                        "metric_col":           metric,
                        "split_field":          field,
                        "split_values":         split_vals,
                        "matched_columns":      matched_cols,
                        "id_vars":              id_vars,
                        "detection_method":     "heuristic",
                    }

        if best_result.get("has_metric_hierarchy"):
            self._log(
                "detect_metric_hierarchy",
                f"Metric-split detected: metric='{best_result['metric_col']}', "
                f"split_field='{best_result['split_field']}', "
                f"columns={best_result['matched_columns']}",
                "ok",
            )
            return best_result

        # LLM fallback
        if self._client and col_names:
            prompt = f"""You are a data engineering assistant.
Examine these column headers from an uploaded dataset:
  {col_names}

Numeric schema metrics available: {numeric_fields}
Categorical schema fields: {list(lookup.keys())}

Do any 2+ column names follow a pattern like:
  {{categorical_value}}_{{metric}}  e.g. TV_Spends, Digital_Spends
  {{metric}}_{{categorical_value}}  e.g. Impressions_Haircare
  {{period}}_{{metric}}             e.g. Q1_Spends, Q2_Spends

If yes, respond with EXACTLY this JSON (no markdown):
{{"has_metric_hierarchy": true, "metric_col": "<Spends|Impressions>", "split_field": "<Channel|Category|Publisher|Period>", "matched_columns": ["<col1>", "<col2>", ...]}}

If no:
{{"has_metric_hierarchy": false}}
"""
            try:
                raw = self._client.models.generate_content(model=self.model, contents=prompt).text.strip()
                raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n")
                result = json.loads(raw)
                if result.get("has_metric_hierarchy"):
                    matched = [c for c in result.get("matched_columns", []) if c in col_names]
                    if len(matched) >= 2 and result.get("metric_col") in numeric_fields:
                        id_vars = [c for c in col_names if c not in matched]
                        self._log(
                            "detect_metric_hierarchy",
                            f"LLM metric-split detected: metric='{result['metric_col']}', "
                            f"columns={matched}",
                            "ok",
                        )
                        return {
                            "has_metric_hierarchy": True,
                            "metric_col":           result["metric_col"],
                            "split_field":          result.get("split_field", "Unknown"),
                            "split_values":         [],
                            "matched_columns":      matched,
                            "id_vars":              id_vars,
                            "detection_method":     "llm",
                        }
            except Exception as exc:
                self._log("detect_metric_hierarchy", f"LLM metric-hierarchy detection failed: {exc}", "warning")

        return {
            "has_metric_hierarchy": False,
            "metric_col":           None,
            "split_field":          None,
            "split_values":         [],
            "matched_columns":      [],
            "id_vars":              [],
            "detection_method":     None,
        }

    def apply_metric_hierarchy_unstack(
        self,
        df: pd.DataFrame,
        matched_columns: list[str],
        id_vars: list[str],
        metric_col: str,
        split_field: str,
    ) -> pd.DataFrame:
        """
        Tool 3 (Extended): Melt metric-split columns into long format, restoring
        the categorical split field alongside the metric.

        Example:
          Input:  Category | TV_Spends | Digital_Spends | Radio_Spends
          Output: Category | Channel   | Spends

        Parameters
        ----------
        df             : input DataFrame
        matched_columns: column names to melt (e.g. ["TV_Spends", "Digital_Spends"])
        id_vars        : columns to keep fixed
        metric_col     : name of the target metric (e.g. "Spends")
        split_field    : schema field that was used to split (e.g. "Channel")
        """
        df_long = pd.melt(
            df,
            id_vars=id_vars,
            value_vars=matched_columns,
            var_name=split_field,
            value_name=metric_col,
        )
        # Clean up the split_field column: strip the metric suffix/prefix
        metric_l = metric_col.lower()
        def _clean_label(val: str) -> str:
            v = val.strip()
            # Remove metric suffix: "TV_Spends" → "TV"
            if v.lower().endswith(f"_{metric_l}"):
                return v[: -(len(metric_l) + 1)]
            # Remove metric prefix: "Spends_TV" → "TV"
            if v.lower().startswith(f"{metric_l}_"):
                return v[len(metric_l) + 1:]
            return v

        df_long[split_field] = df_long[split_field].apply(_clean_label)

        self._log(
            "tool3_metric_hierarchy",
            f"Metric-hierarchy unstack: {matched_columns} → '{split_field}' + '{metric_col}'. "
            f"Output: {len(df_long)} rows.",
            "ok",
        )
        return df_long

    # ------------------------------------------------------------------
    # 6. Transformations  (the three named Agentic Tools)
    # ------------------------------------------------------------------

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # TOOL 1 — Rename Tool
    # Pydantic-validated renaming of aliased column headers to the
    # standard Kantar schema names.
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def apply_rename_tool(
        self,
        df: pd.DataFrame,
        mappings: dict[str, ColumnMapping],
    ) -> tuple[pd.DataFrame, RenameResult]:
        """
        Tool 1: Rename aliased column headers to their standard names.

        Parameters
        ----------
        df       : input DataFrame
        mappings : confirmed ColumnMapping objects keyed by source column name

        Returns
        -------
        (transformed_df, RenameResult)
          - RenameResult is a Pydantic model containing mapped / not_mapped /
            flagged_for_review / columns_dropped lists for full auditability.
        """
        df = df.copy()
        mapped_list:   list[RenameMapping] = []
        unmapped_list: list[RenameMapping] = []
        flagged_list:  list[RenameMapping] = []
        dropped: list[str] = []

        rename_dict: dict[str, str] = {}

        for src, m in mappings.items():
            rm = RenameMapping(
                source_column=m.source_column,
                target_column=m.target_column,
                confidence=m.confidence,
                reasoning=m.reasoning,
            )
            if m.target_column:
                rename_dict[src] = m.target_column
                if m.confidence >= CONFIDENCE_THRESHOLD:
                    mapped_list.append(rm)
                    self._log(
                        "tool1_rename",
                        f"RENAMED '{src}' -> '{m.target_column}'  "
                        f"(confidence={m.confidence:.0%})  {m.reasoning}",
                        "ok",
                    )
                else:
                    flagged_list.append(rm)
                    self._log(
                        "tool1_rename",
                        f"LOW-CONFIDENCE rename '{src}' -> '{m.target_column}'  "
                        f"({m.confidence:.0%}) — flagged for human review",
                        "warning",
                    )
            else:
                dropped.append(src)
                unmapped_list.append(rm)
                self._log(
                    "tool1_rename",
                    f"DROPPING column '{src}' — not mapped to any standard column",
                    "warning",
                )

        df = df.rename(columns=rename_dict)
        df = df.drop(
            columns=[c for c in dropped if c in df.columns],
            errors="ignore",
        )

        result = RenameResult(
            mapped=mapped_list,
            not_mapped=unmapped_list,
            flagged_for_review=flagged_list,
            columns_dropped=dropped,
        )
        self._log(
            "tool1_rename",
            f"Rename Tool complete. Renamed={len(rename_dict)}, "
            f"Dropped={len(dropped)}, Flagged={len(flagged_list)}",
            "ok",
        )
        return df, result

    # Keep backward-compat alias
    def apply_mapping(
        self,
        df: pd.DataFrame,
        mappings: dict[str, ColumnMapping],
    ) -> pd.DataFrame:
        """Backward-compatible wrapper — calls apply_rename_tool."""
        df_out, _ = self.apply_rename_tool(df, mappings)
        return df_out

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # TOOL 2 — Un-pivot Tool
    # Melts wide-format datasets where dates / metrics appear as
    # individual column headers back into a normalised long format.
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def apply_unpivot_tool(
        self,
        df: pd.DataFrame,
        id_vars: list[str],
        value_vars: list[str],
        var_name: str = "Date",
        value_name: str = "Spends",
    ) -> pd.DataFrame:
        """
        Tool 2: Un-pivot (melt) a wide-format DataFrame to long format.

        Parameters
        ----------
        df          : wide-format input DataFrame
        id_vars     : columns to keep as fixed identifiers (Category, Channel, …)
        value_vars  : columns to melt (date strings or period names)
        var_name    : name for the new variable column (default 'Date')
        value_name  : name for the new value column (default 'Spends')

        Example
        -------
        Input:  Category | Channel | 07/01/2023 | 14/01/2023 | 21/01/2023
        Output: Category | Channel | Date       | Spends
        """
        df_long = pd.melt(
            df,
            id_vars=id_vars,
            value_vars=value_vars,
            var_name=var_name,
            value_name=value_name,
        )
        # Normalize the new variable column:
        # If headers were Timestamp objects, convert to "DD/MM/YYYY" strings first
        if var_name in df_long.columns:
            col_series = df_long[var_name]
            if hasattr(col_series.iloc[0], "strftime") if len(df_long) > 0 else False:
                df_long[var_name] = col_series.apply(lambda x: x.strftime("%d/%m/%Y") if hasattr(x, "strftime") else str(x))
            else:
                # Clean "2023-01-07 00:00:00" → "07/01/2023" if needed
                sample_val = str(col_series.iloc[0]) if len(df_long) > 0 else ""
                if re.match(r"\d{4}-\d{2}-\d{2}\s", sample_val):
                    parsed_ts = pd.to_datetime(col_series, errors="coerce")
                    df_long[var_name] = parsed_ts.dt.strftime("%d/%m/%Y").where(parsed_ts.notna(), col_series.astype(str))

        # If var_name is 'Date', attempt to parse the date strings
        if var_name == "Date":
            cleaned = df_long[var_name].astype(str).str.extract(
                r"^(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}[/\-]\d{2}[/\-]\d{2})",
                expand=False,
            ).fillna(df_long[var_name].astype(str))
            parsed = pd.to_datetime(cleaned, dayfirst=True, errors="coerce")
            # Store as formatted date string for consistent output
            df_long[var_name] = parsed.dt.strftime("%d/%m/%Y").where(parsed.notna(), df_long[var_name].astype(str))
        self._log(
            "tool2_unpivot",
            f"Un-pivot Tool: melted {len(value_vars)} columns "
            f"({value_vars[:3]}{'...' if len(value_vars)>3 else ''}) "
            f"into '{var_name}' / '{value_name}'.  "
            f"Output: {len(df_long)} rows.",
            "ok",
        )
        return df_long

    # Keep backward-compat alias
    def apply_melt(
        self,
        df: pd.DataFrame,
        id_vars: list[str],
        value_vars: list[str],
        var_name: str = "Period",
        value_name: str = "Value",
    ) -> pd.DataFrame:
        """Backward-compatible alias for apply_unpivot_tool."""
        return self.apply_unpivot_tool(df, id_vars, value_vars, var_name, value_name)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # TOOL 3 — Hierarchical Unstack Tool
    # Combines split date hierarchy columns (Day_Name, Day_Num, Month,
    # Year) into a single standard Date column.
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def apply_hierarchical_unstack_tool(
        self,
        df: pd.DataFrame,
        year_col: str,
        month_col: str,
        day_num_col: str,
        day_name_col: str | None = None,
        output_col: str = "Date",
        date_format: str = "%d/%m/%Y",
    ) -> pd.DataFrame:
        """
        Tool 3: Combine hierarchical date columns into a single Date column.

        Supports:
          ① year_col + month_col + day_num_col → DD/MM/YYYY
          ② + day_name_col                    → DD/MM/YYYY, DayName

        Parameters
        ----------
        df           : input DataFrame containing the split date columns
        year_col     : column name holding year values (e.g. 2023)
        month_col    : column name holding month values (1-12 or Jan-Dec)
        day_num_col  : column name holding day-of-month values (1-31)
        day_name_col : optional column holding weekday names (Monday / Sat / …)
        output_col   : name for the combined output column (default 'Date')
        date_format  : strftime format for the date portion (default '%d/%m/%Y')

        Example
        -------
        Input row : Day_Name=Saturday  Day_Num=7  Month=1  Year=2023
        Output    : "07/01/2023, Saturday"
        """
        df = df.copy()

        def _month_to_int(val: Any) -> int:
            """Convert month value to integer (handles names and numbers)."""
            try:
                return int(val)
            except (ValueError, TypeError):
                s = str(val).strip()[:3].title()
                idx = {m[:3]: i+1 for i, m in enumerate(MONTH_NAMES)}
                return idx.get(s, 1)

        # Build a real date column
        dates_dt = pd.to_datetime(
            {
                "year":  pd.to_numeric(df[year_col],    errors="coerce"),
                "month": df[month_col].apply(_month_to_int),
                "day":   pd.to_numeric(df[day_num_col], errors="coerce"),
            },
            errors="coerce",
        )

        if day_name_col and day_name_col in df.columns:
            # Format: "07/01/2023, Saturday"
            df[output_col] = dates_dt.dt.strftime(date_format) + ", " + df[day_name_col].astype(str)
            src_cols = [year_col, month_col, day_num_col, day_name_col]
        else:
            # Format: "07/01/2023"
            df[output_col] = dates_dt.dt.strftime(date_format)
            src_cols = [year_col, month_col, day_num_col]

        # Drop the now-redundant hierarchy columns
        df = df.drop(columns=[c for c in src_cols if c in df.columns], errors="ignore")

        # Move Date to first position
        cols = [output_col] + [c for c in df.columns if c != output_col]
        df = df[cols]

        self._log(
            "tool3_hierarchical_unstack",
            f"Hierarchical Unstack Tool: combined {src_cols} -> '{output_col}' "
            f"(format: {date_format}"
            + (", DayName)" if day_name_col else ")"),
            "ok",
        )
        return df

    def combine_split_date(
        self,
        df: pd.DataFrame,
        year_col: str,
        month_col: str,
        day_col: str,
        output_col: str = "Date",
        day_name_col: str | None = None,
    ) -> pd.DataFrame:
        """Backward-compatible alias for apply_hierarchical_unstack_tool."""
        return self.apply_hierarchical_unstack_tool(
            df, year_col, month_col, day_col,
            day_name_col=day_name_col,
            output_col=output_col,
        )

    # ------------------------------------------------------------------
    # 7. Type coercion / validation
    # ------------------------------------------------------------------

    def coerce_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attempt to coerce columns to their expected types per TARGET_SCHEMA."""
        df = df.copy()
        for col, meta in TARGET_SCHEMA.items():
            if col not in df.columns:
                continue
            dtype = meta["dtype"]
            try:
                if dtype == "int":
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                elif dtype == "float":
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif dtype == "date":
                    # ── Robust date coercion ───────────────────────────────────────
                    # 1. Strip weekday suffixes produced by Tool 3
                    #    e.g. "07/01/2023, Saturday" → "07/01/2023"
                    raw = df[col].astype(str)
                    cleaned = raw.str.extract(
                        r"^(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}[/\-]\d{2}[/\-]\d{2})",
                        expand=False,
                    ).fillna(raw)  # fall back to original string if pattern not found

                    # 2. Try dayfirst=True (DD/MM/YYYY), then fallback
                    parsed = pd.to_datetime(cleaned, dayfirst=True, errors="coerce")

                    # 3. For any rows that failed, try iso format (YYYY-MM-DD)
                    failed_mask = parsed.isna() & df[col].notna()
                    if failed_mask.any():
                        parsed[failed_mask] = pd.to_datetime(
                            raw[failed_mask], errors="coerce"
                        )

                    # 4. Keep as formatted string for Excel readability
                    #    (avoids ugly epoch timestamps in export)
                    df[col] = parsed.dt.strftime("%d/%m/%Y").where(parsed.notna(), other=df[col].astype(str))
                # str: leave as-is
            except Exception as exc:
                self._log("coerce_types", f"Could not coerce '{col}' to {dtype}: {exc}", "warning")
        self._log("coerce_types", "Type coercion complete.", "ok")
        return df

    # ------------------------------------------------------------------
    # 8. Validation – check required columns present
    # ------------------------------------------------------------------

    def validate(self, df: pd.DataFrame) -> list[str]:
        """Return list of missing required columns."""
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            self._log("validate", f"Missing required columns: {missing}", "warning")
        else:
            self._log("validate", "All required columns present.", "ok")
        return missing

    # ------------------------------------------------------------------
    # 9. Export
    # ------------------------------------------------------------------

    def export_excel(
        self,
        df_processed: pd.DataFrame,
        output_path: str | Path | None = None,
    ) -> bytes:
        """
        Write processed data + logs to a multi-sheet Excel workbook.
        Returns raw bytes (for Streamlit download) and optionally saves to disk.
        """
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            df_processed.to_excel(writer, sheet_name="Results", index=False)
            self.logs_as_dataframe().to_excel(writer, sheet_name="Logs", index=False)

            # Auto-fit column widths (Results sheet)
            ws = writer.sheets["Results"]
            for i, col in enumerate(df_processed.columns):
                max_width = max(
                    len(str(col)),
                    df_processed[col].astype(str).str.len().max() if len(df_processed) else 0,
                )
                ws.set_column(i, i, min(max_width + 2, 50))

        excel_bytes = buffer.getvalue()

        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(excel_bytes)
            self._log("export", f"Saved output to '{output_path}'", "ok")
        else:
            self._log("export", "Excel bytes generated (in-memory).", "ok")

        return excel_bytes

    # ------------------------------------------------------------------
    # 10. Full pipeline (convenience method)
    # ------------------------------------------------------------------

    def run_pipeline(
        self,
        source: str | Path | io.BytesIO,
        filename: str,
        confirmed_mappings: dict[str, ColumnMapping] | None = None,
        do_melt: bool = False,
        melt_id_vars: list[str] | None = None,
        melt_value_vars: list[str] | None = None,
        melt_var_name: str = "Date",
        melt_value_name: str = "Spends",
        do_hierarchical_unstack: bool = False,
        hierarchical_info: dict[str, Any] | None = None,
        do_schema_wide_melt: bool = False,
        schema_wide_info: dict[str, Any] | None = None,
        do_metric_hierarchy: bool = False,
        metric_hierarchy_info: dict[str, Any] | None = None,
        output_path: str | Path | None = None,
    ) -> tuple[pd.DataFrame, bytes, dict[str, ColumnMapping]]:
        """
        Full Agentic AI pipeline:
          1. Ingest + analyse structure
          2. Detect ALL structural patterns:
             - Tool 2a: Date-wide format (date values as column headers)
             - Tool 2b: Schema-wide format (categorical field values as headers)
             - Tool 3a: Hierarchical date (day/month/year split columns → Date)
             - Tool 3b: Metric hierarchy (e.g. TV_Spends, Digital_Spends → Channel+Spends)
          3. Apply Tool 2a (date unpivot) if requested
          4. Apply Tool 2b (schema unpivot) if requested
          5. Apply Tool 3a (hierarchical date unstack) if detected
          6. Apply Tool 3b (metric hierarchy unstack) if requested
          7. Suggest + verify column mappings (heuristic + LLM)
          8. If confirmed_mappings provided → apply Tool 1 (Rename), coerce, validate
          9. Export to Excel (Results + Logs sheets)

        Returns
        -------
        (processed_df, excel_bytes, mappings_used)
          If confirmed_mappings is None, returns suggestions only (no export).
        """
        # Step 1: Ingest + structure analysis
        df = self.ingest(source, filename)
        self.analyse_structure(df)

        # Step 2: Detect ALL structural patterns
        wide_info      = self.detect_wide_format(df)
        h_info         = hierarchical_info or self.detect_hierarchical_date(df)
        sw_info        = schema_wide_info or self.detect_schema_wide_format(df)
        mh_info        = metric_hierarchy_info or self.detect_metric_hierarchy(df)

        # Step 3: Tool 2a — Date-column unpivot
        if do_melt and wide_info["is_wide"]:
            df = self.apply_unpivot_tool(
                df,
                id_vars=melt_id_vars or wide_info["id_vars"],
                value_vars=melt_value_vars or wide_info["value_vars"],
                var_name=melt_var_name,
                value_name=melt_value_name,
            )

        # Step 4: Tool 2b — Schema-column unpivot (categorical field values as headers)
        if do_schema_wide_melt and sw_info.get("is_schema_wide"):
            df = self.apply_unpivot_tool(
                df,
                id_vars=sw_info["id_vars"],
                value_vars=sw_info["value_vars"],
                var_name=sw_info["schema_field"],
                value_name=sw_info.get("suggested_value_col", "Spends"),
            )

        # Step 5: Tool 3a — Hierarchical date unstack (always auto-apply when detected)
        if (do_hierarchical_unstack or h_info.get("has_split_date")) and h_info.get("year_col"):
            df = self.apply_hierarchical_unstack_tool(
                df,
                year_col=h_info["year_col"],
                month_col=h_info["month_col"],
                day_num_col=h_info.get("day_num_col") or h_info.get("day_col", ""),
                day_name_col=h_info.get("day_name_col"),
            )

        # Step 6: Tool 3b — Metric hierarchy unstack (e.g. TV_Spends → Channel + Spends)
        if do_metric_hierarchy and mh_info.get("has_metric_hierarchy"):
            df = self.apply_metric_hierarchy_unstack(
                df,
                matched_columns=mh_info["matched_columns"],
                id_vars=mh_info["id_vars"],
                metric_col=mh_info["metric_col"],
                split_field=mh_info["split_field"],
            )

        # Step 7: Suggest mappings (heuristic + LLM + LLM verification)
        if confirmed_mappings is None:
            mappings = self.suggest_mappings(df)
            return df, b"", mappings  # Return suggestions; caller must confirm

        # Step 8: Tool 1 — Rename Tool (apply confirmed mappings)
        df, _rename_result = self.apply_rename_tool(df, confirmed_mappings)
        df = self.coerce_types(df)
        missing = self.validate(df)
        if missing:
            self._log("pipeline", f"Warning: required columns still missing: {missing}", "warning")

        # Step 9: Export
        excel_bytes = self.export_excel(df, output_path=output_path)
        return df, excel_bytes, confirmed_mappings
