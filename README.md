# Company Enrichment Benchmark Generator

This directory contains the raw provider response files and a reproducible generator for the benchmark CSV outputs.

Run from the repository root:

```bash
python3 benchmarks/generate_benchmark_results.py
```

By default, the script reads raw JSONL files from `benchmarks/data` and writes generated outputs to:

```text
benchmarks/data/generated
```

To overwrite the benchmark CSV files used by the article:

```bash
python3 benchmarks/generate_benchmark_results.py --output-dir benchmarks/data
```

The script reads only raw provider source files:

```text
apollo.jsonl
companyenrich.jsonl
contactout.jsonl
coresignal.jsonl
crustdata.jsonl
pdl.jsonl
```

It does not read existing generated benchmark CSVs as inputs.

Generated outputs:

```text
benchmark_provider_summary.csv
benchmark_depth_metrics.csv
benchmark_canonical_field_coverage.csv
benchmark_bucket_coverage.csv
benchmark_record_depth.csv
benchmark_config.json
*_fields.csv
```

Benchmark rules currently encoded:

- All public percentages use the submitted-domain denominator of `349`.
- ContactOut records are unwrapped from their dynamic top-level domain key.
- Placeholder values are treated as missing where applicable: numeric `0` for employee count, revenue, founded year, and follower count, plus `N/A`-style revenue strings.
- People Data Labs `profiles[]` counts as Crunchbase only when the individual URL contains `crunchbase.com`.
- Provider-specific market identifier fields are excluded from benchmark metrics.
- Domain and website are merged into `domain_website`; funding fields are merged into `funding_data`; parent/subsidiary/affiliate fields are merged into `corporate_relations`.
