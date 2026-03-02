"""
app.py — Kantar Media Data Mapping Agent — Streamlit UI

Demonstrates all three Agentic AI tools:
  Tool 1 — Rename Tool       : Normalise aliased column headers to the standard schema
  Tool 2 — Un-pivot Tool     : Melt wide-format (date-as-columns) datasets
  Tool 3 — Hierarchical Unstack: Combine Day_Name/Day_Num/Month/Year into one Date column

Run:
    streamlit run agent/app.py
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
# Remove the script directory (agent/) from sys.path to prevent import confusion.
# When Streamlit runs `streamlit run agent/app.py`, it prepends agent/ to sys.path.
# That causes Python to find agent.py (a module) instead of the agent/ package
# when executing `from agent.agent import ...`.  Removing it first fixes this.
_this_dir = str(Path(__file__).resolve().parent)
sys.path = [p for p in sys.path if p != _this_dir]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.agent import (
    ColumnMapping,
    DataMappingAgent,
    HumanFlag,
    MappingGroup,
    RenameResult,
)
from agent.schema import ALL_COLUMNS, REQUIRED_COLUMNS, TARGET_SCHEMA

# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Kantar Media — AI Data Mapping Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ─── Global typography fixes (works in both light AND dark mode) ──── */
.main-header{
    font-size:2.2rem;font-weight:700;
    background:linear-gradient(90deg,#1f4e79,#2e86c1);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
    background-clip:text;
}
.sub-header{
    font-size:1.05rem;color:#a0b4c8;
    margin-top:-8px;margin-bottom:20px;letter-spacing:.3px;
}

/* ─── Colour-coded column boxes — explicit bg + text so always readable ─ */
.mapped-col{
    background:#1a3d2b;border-left:4px solid #4caf50;
    padding:8px 14px;margin:4px 0;border-radius:6px;
    color:#c8f7c5;font-size:.92rem;line-height:1.5;
}
.mapped-col b{color:#89e08a;}

.notmapped-col{
    background:#3d1a1a;border-left:4px solid #ef5350;
    padding:8px 14px;margin:4px 0;border-radius:6px;
    color:#f9c0c0;font-size:.92rem;line-height:1.5;
}
.notmapped-col b{color:#ff8a80;}

.flagged-col{
    background:#3d2e00;border-left:4px solid #ffa726;
    padding:8px 14px;margin:4px 0;border-radius:6px;
    color:#ffe082;font-size:.92rem;line-height:1.5;
}
.flagged-col b{color:#ffd54f;}
.flagged-col small{color:#ffe0a0;}

/* ─── Tool container card ─────────────────────────────────────────── */
.tool-box{
    border:1px solid #2e6da4;border-radius:10px;
    padding:18px 20px;margin-bottom:14px;
    background:#0d1f35;
}
.tool-title{
    font-size:1.08rem;font-weight:700;
    color:#63b3ed;margin-bottom:8px;
}

/* ─── Confidence badges ───────────────────────────────────────────── */
.conf-high{
    display:inline-block;background:#1b5e20;color:#b9f6ca;
    font-weight:700;font-size:.82rem;padding:1px 7px;
    border-radius:10px;border:1px solid #4caf50;
}
.conf-med{
    display:inline-block;background:#e65100;color:#ffe0b2;
    font-weight:700;font-size:.82rem;padding:1px 7px;
    border-radius:10px;border:1px solid #ffa726;
}
.conf-low{
    display:inline-block;background:#b71c1c;color:#ffcdd2;
    font-weight:700;font-size:.82rem;padding:1px 7px;
    border-radius:10px;border:1px solid #ef5350;
}

/* ─── Schema pills ────────────────────────────────────────────────── */
.schema-pill{
    display:inline-block;background:#1565c0;color:#e3f2fd;
    padding:3px 10px;border-radius:12px;font-size:.76rem;
    margin:3px 2px;border:1px solid #1e88e5;font-weight:600;
}

/* ─── Step progress labels ─────────────────────────────────────────── */
div[data-testid="stMetricLabel"] p{font-size:.82rem;}
</style>
""", unsafe_allow_html=True)


def _init_state():
    defaults = {
        "agent": None,
        "df_raw": None,
        "filename": "",
        "structure_info": None,
        "wide_info": None,
        "hier_info": None,
        "df_transformed": None,
        "tool2_applied": False,
        "tool3_applied": False,
        "suggestions": None,
        "mapping_group": None,
        "human_flags": None,
        "confirmed_mappings": None,
        "df_processed": None,
        "rename_result": None,
        "excel_bytes": None,
        "step": 1,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8a/Google_Gemini_logo.svg/200px-Google_Gemini_logo.svg.png",
        width=110,
    )
    st.markdown("## ⚙️ Settings")
    api_key_input = st.text_input(
        "Google Gemini API Key",
        type="password",
        value=os.environ.get("GEMINI_API_KEY", ""),
        help="Free key at https://aistudio.google.com/app/apikey",
    )
    model_choice = st.selectbox(
        "Gemini Model",
        ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    )
    conf_threshold = st.slider(
        "Confidence threshold",
        0.5, 1.0, 0.70, 0.05,
        help="Mappings below this score are flagged for human review.",
    )

    st.markdown("---")
    st.markdown("### 🎯 Standard Schema")
    st.caption("All 7 columns are **required** in the output.")
    for col, meta in TARGET_SCHEMA.items():
        st.markdown(
            f"<span class='schema-pill'>{col}</span> "
            f"<small>({meta['dtype']})</small>",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("### 📂 Sample Datasets")
    st.caption(
        "Generate demo files with:\n"
        "`python generate_test_data.py`\n\n"
        "Then upload from `data/`:\n"
        "- `media_standard.csv` — raw dataset\n"
        "- `media_alias_names.xlsx` — Tool 1 demo\n"
        "- `media_wide_format.xlsx` — Tool 2 demo\n"
        "- `media_hierarchical_date.xlsx` — Tool 3 demo"
    )

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown('<h1 class="main-header">📊 Kantar Media — AI Data Mapping Agent</h1>',
            unsafe_allow_html=True)
st.markdown(
    '<p class="sub-header">Powered by Google Gemini &mdash; '
    'Upload any media advertising dataset &rarr; Auto-detect &rarr; Map &rarr; Transform &rarr; Export</p>',
    unsafe_allow_html=True,
)

# Progress bar
steps      = ["📁 Upload & Analyse", "🔧 Tool 2 & 3", "🗂️ Tool 1 — Mapping", "📊 Results"]
step_cols  = st.columns(4)
for i, (col, label) in enumerate(zip(step_cols, steps)):
    cur = st.session_state.step
    if cur > i + 1:
        col.success(f"✅ {label}")
    elif cur == i + 1:
        col.warning(f"▶ {label}")
    else:
        col.info(label)

st.divider()


# ===========================================================================
# Helper: confidence badge
# ===========================================================================

def _conf_badge(c: float) -> str:
    pct = f"{c:.0%}"
    if c >= conf_threshold:
        return f"<span class='conf-high'>{pct}</span>"
    if c >= 0.5:
        return f"<span class='conf-med'>{pct}</span>"
    return f"<span class='conf-low'>{pct}</span>"


# ===========================================================================
# STEP 1 — Upload & Analyse
# ===========================================================================

st.markdown("### 📁 Step 1 — Upload Dataset")

uploaded = st.file_uploader(
    "Upload a CSV or Excel file (any Kantar media format)",
    type=["csv", "xlsx", "xls"],
)

if uploaded:
    if uploaded.name != st.session_state.filename:
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        _init_state()

    st.session_state.filename = uploaded.name

    if st.session_state.agent is None:
        st.session_state.agent = DataMappingAgent(
            api_key=api_key_input or None,
            model=model_choice,
        )

    agent: DataMappingAgent = st.session_state.agent
    file_bytes = uploaded.read()

    if st.session_state.df_raw is None:
        with st.spinner("Loading and analysing file…"):
            try:
                df_raw = agent.ingest(io.BytesIO(file_bytes), filename=uploaded.name)
                structure_info = agent.analyse_structure(df_raw)
                wide_info      = agent.detect_wide_format(df_raw)
                hier_info      = agent.detect_hierarchical_date(df_raw)

                st.session_state.df_raw        = df_raw
                st.session_state.df_transformed = df_raw.copy()
                st.session_state.structure_info = structure_info
                st.session_state.wide_info      = wide_info
                st.session_state.hier_info      = hier_info
            except Exception as exc:
                st.error(f"Failed to load file: {exc}")
                st.stop()

    df_raw:    pd.DataFrame  = st.session_state.df_raw
    s_info:    dict          = st.session_state.structure_info
    wide_info: dict          = st.session_state.wide_info
    hier_info: dict          = st.session_state.hier_info

    st.success(
        f"✅ **{uploaded.name}** loaded — "
        f"**{s_info['rows']:,} rows × {s_info['columns']} columns**"
    )

    # --- Structure summary ---
    with st.expander("🔍 Dataset Structure Analysis", expanded=True):
        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Rows",    f"{s_info['rows']:,}")
        tc2.metric("Columns", s_info["columns"])
        tc3.metric("Missing  values",
                   sum(s_info["null_counts"].values()))

        info_df = pd.DataFrame({
            "Column":        s_info["column_names"],
            "Dtype":         [s_info["dtypes"][c]      for c in s_info["column_names"]],
            "Null count":    [s_info["null_counts"][c]  for c in s_info["column_names"]],
            "Sample values": [", ".join(str(v) for v in s_info["sample"][c])
                              for c in s_info["column_names"]],
        })
        st.dataframe(info_df, use_container_width=True)

    with st.expander("👀 Raw data preview (first 10 rows)"):
        st.dataframe(df_raw.head(10), use_container_width=True)

    if st.button("▶ Continue to Tool Detection", type="primary",
                 use_container_width=True):
        st.session_state.step = 2
        st.rerun()


# ===========================================================================
# STEP 2 — Tool 2 & Tool 3 Detection & Application
# ===========================================================================

if st.session_state.step >= 2 and st.session_state.df_raw is not None:
    st.divider()
    st.markdown("### 🔧 Step 2 — Structural Transformations (Tools 2 & 3)")
    st.caption(
        "The agent automatically detects structural issues in the dataset. "
        "Review and apply the relevant tools before column mapping."
    )

    agent:     DataMappingAgent = st.session_state.agent
    wide_info: dict             = st.session_state.wide_info
    hier_info: dict             = st.session_state.hier_info
    df_work: pd.DataFrame       = st.session_state.df_transformed.copy()

    # ---- TOOL 2: Un-pivot ----
    with st.container():
        st.markdown(
            '<div class="tool-box">'
            '<div class="tool-title">⑵ Tool 2 — Un-pivot Tool (Wide → Long Format)</div>',
            unsafe_allow_html=True,
        )
        if wide_info["is_wide"] and not st.session_state.tool2_applied:
            wt = wide_info.get("wide_type", "period_name")
            vv = wide_info["value_vars"]
            # Normalise display of column names (Timestamps → DD/MM/YYYY strings)
            def _fmt_col(c) -> str:
                if hasattr(c, "strftime"):
                    return c.strftime("%d/%m/%Y")
                return str(c)
            vv_display = [_fmt_col(c) for c in vv]
            st.warning(
                f"⚠️ **Wide (pivoted) format detected!** "
                f"Found **{len(vv)} {'date' if wt=='date_string' else 'period'}-columns** "
                f"that should be a single stacked `Date` column: "
                f"`{', '.join(vv_display[:4])}{'…' if len(vv)>4 else ''}`"
            )
            c1, c2 = st.columns(2)
            mv_name  = c1.text_input("New variable column name (Date column)", value="Date")
            val_name = c2.text_input("New value column name", value="Spends")
            if st.button("Apply Tool 2 — Un-pivot Dataset",
                         type="primary", use_container_width=True):
                with st.spinner("Un-pivoting…"):
                    df_work = agent.apply_unpivot_tool(
                        df_work,
                        id_vars=wide_info["id_vars"],
                        value_vars=vv,
                        var_name=mv_name,
                        value_name=val_name,
                    )
                    st.session_state.df_transformed = df_work
                    st.session_state.tool2_applied  = True
                    # Re-detect after unpivot
                    st.session_state.hier_info = agent.detect_hierarchical_date(df_work)
                    hier_info = st.session_state.hier_info
                st.rerun()
        elif st.session_state.tool2_applied:
            df_t = st.session_state.df_transformed
            st.success(
                f"✅ Tool 2 applied — dataset un-pivoted to long format. "
                f"**{df_t.shape[0]:,} rows × {df_t.shape[1]} columns**"
            )
            st.info(f"Preview (first 20 of {df_t.shape[0]:,} rows)")
            st.dataframe(df_t.head(20), use_container_width=True)
        else:
            st.info("✅ No wide-format detected. Tool 2 not needed for this dataset.")
        st.markdown("</div>", unsafe_allow_html=True)

    # ---- TOOL 3: Hierarchical Unstack ----
    with st.container():
        st.markdown(
            '<div class="tool-box">'
            '<div class="tool-title">⑶ Tool 3 — Hierarchical Unstack (Split Date → Single Date Column)</div>',
            unsafe_allow_html=True,
        )
        df_work = st.session_state.df_transformed
        hi = st.session_state.hier_info

        if hi.get("has_split_date") and not st.session_state.tool3_applied:
            st.warning(
                f"⚠️ **Hierarchical date detected!** "
                f"Date is split across columns: "
                + ", ".join(f"`{hi[k]}`" for k in
                            ["day_name_col","day_num_col","month_col","year_col"]
                            if hi.get(k))
                + ". These will be combined into a single `Date` column."
            )
            # Show preview of combined date
            try:
                sample_row = df_work.iloc[0]
                day_n  = str(sample_row[hi["day_name_col"]]) if hi.get("day_name_col") else ""
                day_d  = int(sample_row[hi["day_num_col"]])
                month  = int(sample_row[hi["month_col"]])   if hi.get("month_col") else 1
                year   = int(sample_row[hi["year_col"]])
                preview = f"{day_d:02d}/{month:02d}/{year}" + (f", {day_n}" if day_n else "")
                st.info(f"📅 First row preview → **{preview}**")
            except Exception:
                pass

            if st.button("Apply Tool 3 — Hierarchical Unstack",
                         type="primary", use_container_width=True):
                with st.spinner("Combining date columns…"):
                    df_work = agent.apply_hierarchical_unstack_tool(
                        df_work,
                        year_col=hi["year_col"],
                        month_col=hi["month_col"],
                        day_num_col=hi.get("day_num_col") or hi.get("day_col",""),
                        day_name_col=hi.get("day_name_col"),
                    )
                    st.session_state.df_transformed = df_work
                    st.session_state.tool3_applied  = True
                st.rerun()
        elif st.session_state.tool3_applied:
            st.success("✅ Tool 3 applied — hierarchical date columns combined into `Date`.")
            st.dataframe(st.session_state.df_transformed.head(5), use_container_width=True)
        else:
            st.info("✅ No hierarchical date detected. Tool 3 not needed for this dataset.")
        st.markdown("</div>", unsafe_allow_html=True)

    if st.button("▶ Continue to Column Mapping (Tool 1)",
                 type="primary", use_container_width=True):
        st.session_state.step = 3
        st.session_state.suggestions = None   # reset so it re-runs
        st.rerun()


# ===========================================================================
# STEP 3 — Tool 1: Column Mapping & Rename
# ===========================================================================

if st.session_state.step >= 3 and st.session_state.df_transformed is not None:
    st.divider()
    st.markdown("### 🗂️ Step 3 — Tool 1: Column Mapping & Rename")
    st.caption(
        "The agent maps every column to the Kantar standard schema using heuristics "
        "and the Gemini LLM. High-confidence → auto-accepted. Low-confidence → flagged "
        "for your review. Columns with no standard match are dropped from the output."
    )

    agent: DataMappingAgent = st.session_state.agent
    df_work: pd.DataFrame   = st.session_state.df_transformed

    # Build suggestions if not yet done
    if st.session_state.suggestions is None:
        with st.spinner("Analysing columns with Gemini LLM…"):
            suggestions = agent.suggest_mappings(df_work)
            group       = agent.group_mappings(suggestions, threshold=conf_threshold)
            flags       = agent.get_human_flags(suggestions, df_work, threshold=conf_threshold)
            st.session_state.suggestions    = suggestions
            st.session_state.mapping_group  = group
            st.session_state.human_flags    = flags

    suggestions:    dict[str, ColumnMapping] = st.session_state.suggestions
    group:          MappingGroup             = st.session_state.mapping_group
    flags:          list[HumanFlag]          = st.session_state.human_flags

    # ---------- Grouped summary ----------
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("MAPPED columns",       len(group.mapped_columns),
               help=f"Confidence >= {conf_threshold:.0%}")
    mc2.metric("NOT-MAPPED (to drop)", len(group.not_mapped_columns),
               help="No confident standard match — will be removed from output")
    mc3.metric("Flagged for review",   len(flags),
               help="Agent unsure — needs your decision")

    all_options = ["(Drop / Ignore)"] + ALL_COLUMNS

    # ---------- MAPPED columns ----------
    with st.expander(
        f"🟢 MAPPED Columns ({len(group.mapped_columns)}) — already auto-accepted",
        expanded=True,
    ):
        if group.mapped_columns:
            for src, m in group.mapped_columns.items():
                st.markdown(
                    f'<div class="mapped-col">'
                    f'<b>{src}</b> → <b>{m.target_column}</b> &nbsp;'
                    f'{_conf_badge(m.confidence)} &nbsp; <small>{m.reasoning}</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No columns mapped with high confidence.")

    # ---------- NOT-MAPPED columns ----------
    with st.expander(
        f"🔴 NOT-MAPPED Columns ({len(group.not_mapped_columns)}) — will be DROPPED",
        expanded=bool(group.not_mapped_columns),
    ):
        if group.not_mapped_columns:
            st.warning(
                "These columns do not match any standard schema column and will be "
                "**removed from the exported file**. You can override this below."
            )
            for src, m in group.not_mapped_columns.items():
                st.markdown(
                    f'<div class="notmapped-col">'
                    f'<b>{src}</b> — {m.reasoning}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.success("All columns have a confident standard mapping.")

    # ---------- FLAGGED columns (human review) ----------
    confirmed_overrides: dict[str, ColumnMapping] = {}
    if flags:
        st.markdown("#### ⚠️ Flagged Columns — Human Decision Required")
        st.warning(
            f"**{len(flags)} column(s)** could not be confidently mapped. "
            "The agent is asking for your input. Please select the correct mapping below."
        )
        for flag in flags:
            src         = flag.column
            existing    = suggestions.get(src)
            current_idx = 0
            if existing and existing.target_column in ALL_COLUMNS:
                current_idx = all_options.index(existing.target_column)

            with st.container():
                st.markdown(
                    f'<div class="flagged-col">'
                    f'<b>🚩 {src}</b> &nbsp; {_conf_badge(existing.confidence if existing else 0)}'
                    f'<br><small>{flag.question}</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                chosen = st.selectbox(
                    f"Map '{src}' to:",
                    options=all_options,
                    index=current_idx,
                    key=f"flag_{src}",
                )
                target = None if chosen == "(Drop / Ignore)" else chosen
                confirmed_overrides[src] = ColumnMapping(
                    source_column=src,
                    target_column=target,
                    confidence=1.0 if target else 0.0,
                    reasoning="Human decision",
                )

    # ---------- Process button ----------
    # Build final confirmed mappings: mapped + overrides
    st.markdown("---")
    process_col, reset_col = st.columns([4, 1])

    # Check required columns coverage
    final_targets = {m.target_column for m in group.mapped_columns.values()}
    final_targets |= {m.target_column for m in confirmed_overrides.values() if m.target_column}
    missing_req = [c for c in REQUIRED_COLUMNS if c not in final_targets]

    if missing_req:
        st.error(
            f"❌ Required columns not yet mapped: **{missing_req}**. "
            "Please resolve flagged columns above."
        )

    process_btn = process_col.button(
        "⚡ Apply Tool 1 (Rename) & Generate Excel",
        type="primary",
        use_container_width=True,
        disabled=bool(missing_req),
    )
    if reset_col.button("🔄 Reset", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        _init_state()
        st.rerun()

    if process_btn:
        with st.spinner("Applying rename tool and exporting…"):
            # Merge mapped + human overrides
            final_mappings: dict[str, ColumnMapping] = {
                **group.mapped_columns,
                **confirmed_overrides,
                **{
                    src: ColumnMapping(
                        source_column=src,
                        target_column=None,
                        confidence=0.0,
                        reasoning="Not mapped — dropped",
                    )
                    for src in group.not_mapped_columns
                    if src not in confirmed_overrides
                },
            }

            agent2 = DataMappingAgent(
                api_key=api_key_input or None,
                model=model_choice,
            )
            # Replay ingestion from stored raw bytes
            df2 = df_work.copy()

            # Tool 1 — Apply rename
            df2, rename_result = agent2.apply_rename_tool(df2, final_mappings)
            df2 = agent2.coerce_types(df2)
            agent2.validate(df2)
            excel_bytes = agent2.export_excel(df2)

            st.session_state.df_processed      = df2
            st.session_state.rename_result     = rename_result
            st.session_state.excel_bytes       = excel_bytes
            st.session_state.agent             = agent2
            st.session_state.confirmed_mappings = final_mappings
            st.session_state.step              = 4
        st.rerun()


# ===========================================================================
# STEP 4 — Results & Export
# ===========================================================================

if st.session_state.step == 4 and st.session_state.df_processed is not None:
    st.divider()
    st.markdown("### 📊 Step 4 — Results & Export")

    df_proc: pd.DataFrame        = st.session_state.df_processed
    rename_result: RenameResult  = st.session_state.rename_result
    agent_final: DataMappingAgent = st.session_state.agent

    st.success(
        f"✅ Processing complete! Output: "
        f"**{df_proc.shape[0]:,} rows × {df_proc.shape[1]} columns**"
    )

    # ---- Rename Tool summary ----
    with st.expander("🔑 Tool 1 (Rename) Summary", expanded=True):
        r1, r2, r3 = st.columns(3)
        r1.metric("Successfully Renamed", len(rename_result.mapped))
        r2.metric("Dropped (Not-Mapped)",  len(rename_result.columns_dropped))
        r3.metric("Flagged (Human input)", len(rename_result.flagged_for_review))
        if rename_result.mapped:
            rename_df = pd.DataFrame([
                {
                    "Source Column":  m.source_column,
                    "Standard Column": m.target_column,
                    "Confidence":     f"{m.confidence:.0%}",
                    "Reasoning":      m.reasoning,
                }
                for m in rename_result.mapped
            ])
            st.dataframe(rename_df, use_container_width=True)

    tab1, tab2, tab3 = st.tabs(["📋 Processed Data", "📝 Operation Logs", "📈 Summary Stats"])

    with tab1:
        st.dataframe(df_proc, use_container_width=True)

    with tab2:
        st.dataframe(agent_final.logs_as_dataframe(), use_container_width=True)

    with tab3:
        num_cols = df_proc.select_dtypes(include="number").columns.tolist()
        if num_cols:
            st.dataframe(df_proc[num_cols].describe(), use_container_width=True)
        else:
            st.info("No numeric columns to summarise.")

    st.markdown("#### 💾 Download Output")
    st.download_button(
        label="⬇️ Download Excel (Processed + Logs)",
        data=st.session_state.excel_bytes,
        file_name=f"kantar_mapped_{Path(st.session_state.filename).stem}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )

    if st.button("🔄 Start Over", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        _init_state()
        st.rerun()
