# Company Enrichment API Benchmark

This repository contains the sampled domain list, raw provider response files,
and scripts used to generate benchmark CSV outputs for company enrichment APIs.

## 1. Domain Sampling

We sampled 500 candidate domains from the Majestic Million dataset.

The Majestic Million is a public list of highly ranked domains. The sampling
script downloads that file, filters it to simple `.com` domains, and randomly
selects a diverse 500-domain sample for API coverage testing.

| Item | Value |
| --- | --- |
| Source dataset | Majestic Million |
| Source URL | `https://downloads.majestic.com/majestic_million.csv` |
| Initial sample size | 500 domains |
| Sampling method | Random selection after `.com` filtering and diversity balancing |
| Script | `maj.py` |
| Output | `random_domains.csv` |

Run the sampling script from the repository root:

```bash
python3 maj.py
```

The script:

- downloads the Majestic Million CSV;
- keeps only root `.com` domains with alphabetic second-level names;
- excludes subdomains, private-style TLDs, digits, and hyphenated domains;
- shuffles the filtered list;
- limits over-representation by two-character prefix;
- balances the final sample across first letters where possible;
- writes the sorted output to `random_domains.csv`.

The current `random_domains.csv` contains 500 sampled domains plus a header row.
The script does not set a random seed, so rerunning it can produce a different
sample. Keep the checked-in CSV if the exact domain set needs to stay fixed.

## 2. Provider Inputs

Raw provider responses are stored in `data` as JSONL files:

```text
apollo.jsonl
companyenrich.jsonl
contactout.jsonl
coresignal.jsonl
crustdata.jsonl
pdl.jsonl
```

The benchmark generator reads only these raw provider source files. It does not
use existing generated benchmark CSV files as inputs.

## 3. Benchmark Generation

Run the generator from the repository root:

```bash
python3 generate_benchmark_results.py
```

By default, the script reads raw JSONL files from `data` and writes generated
outputs to:

```text
data/generated
```

To overwrite the benchmark CSV files currently stored in `data`:

```bash
python3 generate_benchmark_results.py --output-dir data
```

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

## 4. Benchmark Rules

The current benchmark CSVs use a submitted-domain denominator of `349`, as
encoded in `generate_benchmark_results.py`. This is separate from the 500-domain
candidate sample in `random_domains.csv`.

Rules currently encoded in the generator:

- All public percentages use the submitted-domain denominator of `349`.
- ContactOut records are unwrapped from their dynamic top-level domain key.
- Placeholder values are treated as missing where applicable: numeric `0` for employee count, revenue, founded year, and follower count, plus `N/A`-style revenue strings.
- People Data Labs `profiles[]` counts as Crunchbase only when the individual URL contains `crunchbase.com`.
- Provider-specific market identifier fields are excluded from benchmark metrics.
- Domain and website are merged into `domain_website`; funding fields are merged into `funding_data`; parent/subsidiary/affiliate fields are merged into `corporate_relations`.
