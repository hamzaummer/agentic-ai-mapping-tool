# Kantar Media — AI Data Mapping Agent

An **Agentic AI** solution that automatically ingests, maps, transforms, and exports media advertising datasets to a standard **Kantar schema**.  Powered by **Google Gemini 2.5 Flash** for intelligent column mapping and decision-making, with a full **Streamlit** human-in-the-loop UI.

---

## Architecture Overview

```
CSV / Excel  →  Ingest  →  Analyse Structure
                               ↓
                 ┌─────────────────────────────────┐
                 │      DETECTION LAYER             │
                 │  Tool 2a: Date-Wide Format       │
                 │  Tool 2b: Schema-Wide Format     │
                 │  Tool 3a: Hierarchical Date      │
                 │  Tool 3b: Metric Hierarchy       │
                 └─────────────────────────────────┘
                               ↓
              Heuristic + LLM Column Mapping  (Tool 1)
                               ↓
                    Human-in-the-Loop Review
                               ↓
              Type Validation (Pydantic)  +  Export Excel
```

---

## Three Core Tools

| Tool | Name | What it Does |
|------|------|-------------|
| **Tool 1** | Rename Tool | Detects aliased column headers (e.g. `Distributor → Publisher`, `Expenditure → Spends`) and renames them to the standard Kantar schema. Fully Pydantic-validated. |
| **Tool 2** | Un-pivot Tool | Detects wide-format datasets and melts them back to long format. Supports (a) date values used as column headers (`Jan-2024`, `07/01/2023`) AND (b) schema field values used as headers (`TV`, `Digital`, `Radio` → `Channel`). |
| **Tool 3** | Hierarchical Unstack | Combines split date columns (`Day`, `Month`, `Year`) into a single `Date` column AND detects metric-split columns (`TV_Spends`, `Digital_Spends`) into a categorical + numeric pair. |

---

## Target Schema

All seven columns are required in the output:

| Column | Type | Description |
|--------|------|-------------|
| `Date` | date | Calendar date — `DD/MM/YYYY` |
| `Category` | str | High-level category (Haircare, Beverages…) |
| `Sub_Category` | str | Detailed sub-category (Shampoo, Cola…) |
| `Channel` | str | Advertising channel (TV, Digital, Radio…) |
| `Publisher` | str | Media house / network (Sony, Google, Star…) |
| `Spends` | float | Ad spend in currency units |
| `Impressions` | int | Number of times the ad was served |

---

## Prerequisites

- **Python 3.10+** (tested on 3.11 and 3.13)
- A free **Google Gemini API key** from [aistudio.google.com](https://aistudio.google.com/app/apikey) *(optional — the agent falls back to heuristic-only mode without it)*

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/hamzaummer/agentic-ai-mapping-tool.git
cd agentic-ai-task
```

### 2. Create and activate a virtual environment

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set your Gemini API key *(optional)*

**Option A — `.env` file (recommended)**
```bash
# Create a .env file in the project root
GEMINI_API_KEY=your_api_key_here
```

**Option B — Environment variable**
```powershell
# Windows PowerShell
$env:GEMINI_API_KEY = "your_api_key_here"
```
```bash
# macOS / Linux
export GEMINI_API_KEY="your_api_key_here"
```

> **Without an API key** the agent runs in *heuristic-only mode* — all built-in alias mappings still work; only LLM-assisted disambiguation is skipped.

### 5. Generate sample datasets

```bash
python generate_test_data.py
```

This creates four datasets in `data/`:
- `media_standard.csv` — clean 80-row media dataset
- `media_alias_names.xlsx` — Tool 1 demo (aliased column headers)
- `media_wide_format.xlsx` — Tool 2 demo (dates as column headers)
- `media_hierarchical_date.xlsx` — Tool 3 demo (day/month/year split columns)

---

## Running the Streamlit App

```bash
streamlit run agent/app.py
```

Then open **http://localhost:8501** in your browser.

**Optional flags:**
```bash
streamlit run agent/app.py --server.port 8502 --server.headless true
```

---

## Running the Jupyter Notebook

```bash
jupyter notebook Agentic_AI_Agent.ipynb
```
Or open it directly in VS Code with the Jupyter extension.

The notebook walks through every section end-to-end:
- Section 1–3: Install dependencies, schema definition, Gemini setup
- Section 4–6: Ingestion, schema recognition, LLM mapping
- Section 7: Human-in-the-loop confirmation
- Section 8–9: Transformations (melt, date combining)
- Section 10–11: Pydantic validation, final mapping
- Section 12–13: Logging, Excel export
- Section 14–16: Generate test data, run unit tests, launch Streamlit

---

## Running Unit Tests

```bash
pytest tests/ -v
```

Expected output: **29 tests passing** across:
- Ingestion (CSV, Excel, BytesIO, bad-file)
- Heuristic mapping
- Transformations (date-wide, schema-wide, split-date, metric-hierarchy)
- Apply mapping & type coercion
- Validation & export
- Full pipeline (standard + split-date)

---

## Project Structure

```
.
├── agent/
│   ├── __init__.py         # Package init
│   ├── agent.py            # Core DataMappingAgent with all three tools
│   ├── app.py              # Streamlit UI
│   └── schema.py           # Kantar target schema definition
├── data/
│   ├── media_standard.csv
│   ├── media_alias_names.xlsx
│   ├── media_wide_format.xlsx
│   └── media_hierarchical_date.xlsx
├── tests/
│   └── test_agent.py       # 29 pytest unit tests
├── PRD Files/
│   └── Agentic AI PRD.md
├── Agentic_AI_Agent.ipynb  # End-to-end walkthrough notebook
├── generate_test_data.py   # Script to generate sample datasets
├── requirements.txt
├── .env                    # (create locally — NOT committed)
└── README.md
```

---

## Common Errors & Solutions

### `ModuleNotFoundError: No module named 'google.genai'`
```
pip install google-genai
```

### `ModuleNotFoundError: No module named 'agent'`
Make sure you run commands from the **project root directory** (where `agent/` folder is), not from inside `agent/`.

```bash
# Correct
cd agentic-ai-task
streamlit run agent/app.py

# Wrong
cd agentic-ai-task/agent
streamlit run app.py
```

### `GEMINI_API_KEY not set` / LLM returns empty
The agent works in heuristic-only mode without a key. To enable LLM:
1. Get a free key at https://aistudio.google.com/app/apikey
2. Add it to `.env` as `GEMINI_API_KEY=<your_key>`
3. Or enter it in the Streamlit sidebar at runtime

### `pip install` fails on `google-genai`
Ensure pip is up-to-date:
```bash
python -m pip install --upgrade pip
pip install google-genai
```

### Streamlit app shows blank page
Clear the Streamlit cache:
```bash
streamlit cache clear
streamlit run agent/app.py
```

### `openpyxl` or `xlsxwriter` missing when reading/writing Excel
```bash
pip install openpyxl xlsxwriter
```

### Pytest fails with `ImportError` on Windows
Run pytest from the project root with the `-p no:cacheprovider` flag if cache issues occur:
```bash
pytest tests/ -v -p no:cacheprovider
```

### Excel export contains no data / empty Results sheet
This happens when no confirmed mappings are passed. In the notebook, ensure you run the `auto_confirm` cell (Section 7) before running the export cell (Section 13).

### Wide-format detection misses columns (Tool 2)
The detector looks for **2 or more** columns matching a pattern. If your dataset has only one date column header or one schema value as a header, detection will not trigger. Add a second column, or use the LLM fallback by providing a Gemini API key.

### Metric-hierarchy detection not triggering (Tool 3b)
Column names must match the case-insensitive pattern `{value}_{metric}` or `{metric}_{value}`, e.g. `TV_Spends`, `digital_impressions`. Verify your column names match this convention.

### `ValidationError` from Pydantic on date columns
Date values must be parseable. The agent supports `DD/MM/YYYY`, `YYYY-MM-DD`, and timestamp strings. If your dates use a different format (e.g. `MM-DD-YYYY`), pre-convert them before uploading.

---

## Tech Stack

| Component | Library / Service |
|-----------|------------------|
| LLM | Google Gemini 2.5 Flash (`google-genai`) |
| Data manipulation | pandas |
| Type validation | Pydantic v2 |
| UI | Streamlit |
| Excel I/O | openpyxl, xlsxwriter |
| Testing | pytest |
| Env management | python-dotenv |

---

## License

This project is for educational / internal demonstration purposes.
