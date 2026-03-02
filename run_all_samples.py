"""Run all sample files through the pipeline and list outputs."""
import sys, os
sys.path.insert(0, ".")
from agent.agent import DataMappingAgent, ColumnMapping

def run(src, filename, out, do_melt=False):
    a = DataMappingAgent()
    df = a.ingest(src, filename=filename)
    suggestions = a.suggest_mappings(df)
    confirmed = {}
    for col, m in suggestions.items():
        confirmed[col] = ColumnMapping(
            source_column=col, target_column=m.target_column,
            confidence=m.confidence, reasoning=m.reasoning,
        )
    a2 = DataMappingAgent()
    _, xl, _ = a2.run_pipeline(src, filename, confirmed_mappings=confirmed,
                               do_melt=do_melt, output_path=out)
    print(f"[OK] {out}  ({len(xl):,} bytes)")

run("data/sample_alias_names.xlsx", "sample_alias_names.xlsx", "output/alias_names_output.xlsx")
run("data/sample_split_date.csv",   "sample_split_date.csv",   "output/split_date_output.xlsx")
run("data/sample_wide_format.xlsx", "sample_wide_format.xlsx", "output/wide_format_output.xlsx", do_melt=True)
run("data/sample_extra_cols.xlsx",  "sample_extra_cols.xlsx",  "output/extra_cols_output.xlsx")
run("data/sample_standard.csv",     "sample_standard.csv",     "output/standard_output.xlsx")

print("\nOutput files:")
for f in sorted(os.listdir("output")):
    size = os.path.getsize(os.path.join("output", f))
    print(f"  {f:<45}  {size:>8,} bytes")
