#!/usr/bin/env python3
"""
Generate company enrichment benchmark outputs from provider JSONL files.

This script is intentionally self-contained so it can be published with the
benchmark repo. It does not read any previously generated benchmark CSV/JSON
files. Inputs are the provider response JSONL files only. In the public repo,
those JSONL files may be masked publication copies.

Default usage:
  python3 benchmarks/generate_benchmark_results.py

Useful options:
  python3 benchmarks/generate_benchmark_results.py \
    --input-dir benchmarks/data \
    --output-dir benchmarks/data/generated

To overwrite the article data files, pass:
  --output-dir benchmarks/data
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable


TOTAL_SUBMITTED_DOMAINS = 349

PROVIDER_ORDER = [
    "CompanyEnrich",
    "Apollo",
    "PDL",
    "Coresignal",
    "ContactOut",
    "Crustdata",
]

SOURCE_FILES = {
    "PDL": "pdl.jsonl",
    "Apollo": "apollo.jsonl",
    "CompanyEnrich": "companyenrich.jsonl",
    "ContactOut": "contactout.jsonl",
    "Coresignal": "coresignal.jsonl",
    "Crustdata": "crustdata.jsonl",
}

FIELD_STAT_FILES = {
    "PDL": "pdl_fields.csv",
    "Apollo": "apollo_fields.csv",
    "CompanyEnrich": "companyenrich_fields.csv",
    "ContactOut": "contactout_fields.csv",
    "Coresignal": "coresignal_fields.csv",
    "Crustdata": "crustdata_fields.csv",
}

BUCKET_WEIGHTS = {
    "core_identity": 25,
    "firmographics": 20,
    "location_contact": 15,
    "social_web": 15,
    "taxonomy": 10,
    "org_structure": 5,
    "technology": 5,
    "funding": 5,
}

CANONICAL_FIELDS = [
    ("core_identity", "company_id"),
    ("core_identity", "name"),
    ("core_identity", "domain_website"),
    ("core_identity", "description"),
    ("core_identity", "logo"),
    ("firmographics", "industry"),
    ("firmographics", "employee_count_or_range"),
    ("firmographics", "revenue"),
    ("firmographics", "founded_year"),
    ("firmographics", "company_type"),
    ("location_contact", "location"),
    ("location_contact", "phone"),
    ("location_contact", "email"),
    ("social_web", "linkedin"),
    ("social_web", "facebook"),
    ("social_web", "twitter_x"),
    ("social_web", "instagram"),
    ("social_web", "youtube"),
    ("social_web", "crunchbase"),
    ("social_web", "follower_count"),
    ("taxonomy", "categories"),
    ("taxonomy", "keywords_tags_specialties"),
    ("taxonomy", "naics"),
    ("taxonomy", "sic"),
    ("technology", "technologies"),
    ("funding", "funding_data"),
    ("org_structure", "corporate_relations"),
]

ZERO_PLACEHOLDER_FIELDS = {
    "employee_count_or_range",
    "revenue",
    "founded_year",
    "follower_count",
}

RAW_ZERO_PLACEHOLDER_NAMES = {
    "employee",
    "employees",
    "estimated_num_employees",
    "employee_count",
    "size_employees_count",
    "size_employees_count_inferred",
    "revenue",
    "annual_revenue",
    "organization_revenue",
    "founded",
    "founded_at",
    "founded_year",
    "followers",
    "follower_count",
    "linkedin_follower_count",
}

RAW_STOCK_PATH_BLOCKLIST = {
    "PDL": {"ticker", "mic_exchange"},
    "Apollo": {"publicly_traded_symbol", "publicly_traded_exchange", "stock_symbol", "stock_exchange"},
    "CompanyEnrich": {"financial.stock_symbol", "financial.stock_exchange"},
    "Coresignal": {"ticker", "exchange"},
    "ContactOut": set(),
    "Crustdata": set(),
}

PROVIDER_FIELD_MAPPING: dict[str, dict[str, list[str]]] = {
    "PDL": {
        "company_id": ["id"],
        "name": ["name", "display_name"],
        "domain_website": ["website"],
        "description": ["summary", "headline"],
        "logo": ["logo", "logo_url"],
        "industry": ["industry", "industry_v2"],
        "employee_count_or_range": ["employee_count", "size"],
        "revenue": ["inferred_revenue", "average_inferred_revenue"],
        "founded_year": ["founded"],
        "company_type": ["type"],
        "location": [
            "location.country",
            "location.region",
            "location.locality",
            "location.street_address",
            "location.name",
            "location.postal_code",
        ],
        "phone": ["phone", "phone_numbers[]"],
        "email": ["emails[]", "email"],
        "linkedin": ["linkedin_url", "linkedin_id"],
        "facebook": ["facebook_url"],
        "twitter_x": ["twitter_url"],
        "instagram": ["instagram_url"],
        "youtube": ["youtube_url"],
        "crunchbase": ["crunchbase_url", "profiles[]"],
        "follower_count": ["linkedin_follower_count"],
        "categories": [],
        "keywords_tags_specialties": ["tags[]"],
        "naics": ["naics[].naics_code"],
        "sic": ["sic[].sic_code"],
        "technologies": ["technologies[]"],
        "funding_data": [
            "number_funding_rounds",
            "funding_stages[]",
            "total_funding_raised",
            "latest_funding_stage",
        ],
        "corporate_relations": [
            "ultimate_parent",
            "immediate_parent",
            "direct_subsidiaries[]",
            "all_subsidiaries[]",
            "affiliated_entities[].affiliated_id",
            "affiliated_profiles[]",
        ],
    },
    "Apollo": {
        "company_id": ["id"],
        "name": ["name"],
        "domain_website": ["primary_domain", "website_url"],
        "description": ["short_description"],
        "logo": ["logo_url"],
        "industry": ["industry"],
        "employee_count_or_range": ["estimated_num_employees"],
        "revenue": [
            "annual_revenue",
            "organization_revenue",
            "annual_revenue_printed",
            "organization_revenue_printed",
        ],
        "founded_year": ["founded_year"],
        "company_type": [],
        "location": ["country", "state", "city", "raw_address", "street_address", "postal_code"],
        "phone": ["phone", "primary_phone.number", "primary_phone.sanitized_number", "sanitized_phone"],
        "email": ["emails[]"],
        "linkedin": ["linkedin_url", "linkedin_uid"],
        "facebook": ["facebook_url"],
        "twitter_x": ["twitter_url"],
        "instagram": [],
        "youtube": [],
        "crunchbase": ["crunchbase_url"],
        "follower_count": [],
        "categories": [],
        "keywords_tags_specialties": ["keywords[]"],
        "naics": ["naics_codes[]"],
        "sic": ["sic_codes[]"],
        "technologies": ["current_technologies[].name", "technology_names"],
        "funding_data": [
            "funding_events[].id",
            "funding_events[].type",
            "total_funding",
            "total_funding_printed",
            "latest_funding_stage",
        ],
        "corporate_relations": ["suborganizations[].id", "suborganizations[].name"],
    },
    "CompanyEnrich": {
        "company_id": ["id"],
        "name": ["name"],
        "domain_website": ["domain", "website"],
        "description": ["description"],
        "logo": ["logo_url"],
        "industry": ["industry"],
        "employee_count_or_range": ["employees"],
        "revenue": ["revenue"],
        "founded_year": ["founded_year"],
        "company_type": ["type"],
        "location": [
            "location.country.name",
            "location.country.code",
            "location.state.name",
            "location.state.code",
            "location.city.name",
            "location.address",
            "location.postal_code",
        ],
        "phone": ["location.phone"],
        "email": ["email", "emails[]"],
        "linkedin": ["socials.linkedin_url", "socials.linkedin_id"],
        "facebook": ["socials.facebook_url"],
        "twitter_x": ["socials.twitter_url"],
        "instagram": ["socials.instagram_url"],
        "youtube": ["socials.youtube_url"],
        "crunchbase": ["socials.crunchbase_url"],
        "follower_count": [],
        "categories": ["categories[]"],
        "keywords_tags_specialties": ["keywords[]"],
        "naics": ["naics_codes[]"],
        "sic": [],
        "technologies": ["technologies[]"],
        "funding_data": [
            "financial.funding[].type",
            "financial.funding[].date",
            "financial.total_funding",
            "financial.funding_stage",
        ],
        "corporate_relations": ["subsidiaries[]"],
    },
    "ContactOut": {
        "company_id": [],
        "name": ["name"],
        "domain_website": ["domain", "website"],
        "description": ["description"],
        "logo": ["logo_url"],
        "industry": ["industry"],
        "employee_count_or_range": ["employees", "size"],
        "revenue": ["revenue"],
        "founded_year": ["founded_at"],
        "company_type": ["type"],
        "location": ["country", "headquarter", "locations[]"],
        "phone": [],
        "email": [],
        "linkedin": ["li_vanity"],
        "facebook": [],
        "twitter_x": [],
        "instagram": [],
        "youtube": [],
        "crunchbase": [],
        "follower_count": ["followers"],
        "categories": [],
        "keywords_tags_specialties": ["specialties[]"],
        "naics": [],
        "sic": [],
        "technologies": [],
        "funding_data": [
            "funding.number_of_funding_rounds",
            "funding.rounds[].funding_type",
            "funding.rounds[].money_raised_usd",
        ],
        "corporate_relations": [],
    },
    "Coresignal": {
        "company_id": ["id"],
        "name": ["name"],
        "domain_website": ["websites_main", "websites_resolved", "websites_main_original"],
        "description": ["description", "enriched_summary"],
        "logo": ["logo"],
        "industry": ["industry", "enriched_category"],
        "employee_count_or_range": ["size_employees_count", "size_employees_count_inferred", "size_range"],
        "revenue": ["revenue"],
        "founded_year": ["founded"],
        "company_type": ["type"],
        "location": [
            "location_hq_country",
            "location_hq_country_iso_2",
            "location_hq_country_iso_3",
            "locations_full[].country",
            "location_hq_state",
            "locations_full[].state",
            "location_hq_city",
            "locations_full[].city",
            "location_hq_raw_address",
            "locations_full[].location_address",
        ],
        "phone": ["phone_numbers[]"],
        "email": ["emails[]"],
        "linkedin": ["websites_linkedin", "websites_linkedin_canonical", "linkedin_source_id"],
        "facebook": [],
        "twitter_x": [],
        "instagram": [],
        "youtube": [],
        "crunchbase": ["funding_rounds[].cb_url"],
        "follower_count": ["followers"],
        "categories": ["enriched_category"],
        "keywords_tags_specialties": ["enriched_keywords[]", "specialities[]"],
        "naics": [],
        "sic": [],
        "technologies": ["technologies[].technology"],
        "funding_data": [
            "funding_rounds[].last_round_type",
            "funding_rounds[].total_rounds_count",
            "funding_rounds[].last_round_money_raised",
        ],
        "corporate_relations": [],
    },
    "Crustdata": {
        "company_id": ["company_data.crustdata_company_id", "company_data.basic_info.crustdata_company_id"],
        "name": ["company_data.basic_info.name", "company_data.basic_info.profile_name"],
        "domain_website": [
            "company_data.basic_info.primary_domain",
            "company_data.basic_info.all_domains[]",
            "company_data.basic_info.website",
        ],
        "description": [],
        "logo": ["company_data.basic_info.logo_permalink"],
        "industry": ["company_data.basic_info.industries[]"],
        "employee_count_or_range": ["company_data.basic_info.employee_count_range"],
        "revenue": [],
        "founded_year": [],
        "company_type": [],
        "location": [],
        "phone": [],
        "email": [],
        "linkedin": [
            "company_data.basic_info.professional_network_url",
            "company_data.basic_info.professional_network_id",
        ],
        "facebook": [],
        "twitter_x": [],
        "instagram": [],
        "youtube": [],
        "crunchbase": [],
        "follower_count": [],
        "categories": [],
        "keywords_tags_specialties": [],
        "naics": [],
        "sic": [],
        "technologies": [],
        "funding_data": [],
        "corporate_relations": [],
    },
}

KEYWORD_PATHS = {
    "PDL": ["tags[]"],
    "Apollo": ["keywords[]"],
    "CompanyEnrich": ["keywords[]"],
    "ContactOut": ["specialties[]"],
    "Coresignal": ["enriched_keywords[]"],
    "Crustdata": [],
}

TECHNOLOGY_PATHS = {
    "PDL": ["technologies[]"],
    "Apollo": ["current_technologies[]"],
    "CompanyEnrich": ["technologies[]"],
    "ContactOut": [],
    "Coresignal": ["technologies[]"],
    "Crustdata": [],
}

FUNDING_ROUND_PATHS = {
    "PDL": ["number_funding_rounds", "funding_stages[]"],
    "Apollo": ["funding_events[]"],
    "CompanyEnrich": ["financial.funding[]"],
    "ContactOut": ["funding.number_of_funding_rounds", "funding.rounds[]"],
    "Coresignal": ["funding_rounds[]"],
    "Crustdata": [],
}

ORG_RELATION_PATHS = {
    "PDL": [
        "ultimate_parent",
        "immediate_parent",
        "direct_subsidiaries[]",
        "all_subsidiaries[]",
        "affiliated_entities[]",
        "affiliated_profiles[]",
    ],
    "Apollo": ["suborganizations[]"],
    "CompanyEnrich": ["subsidiaries[]"],
    "ContactOut": [],
    "Coresignal": [],
    "Crustdata": [],
}

MULTI_LOCATION_PATHS = {
    "ContactOut": ["locations[]"],
    "Coresignal": ["locations_full[]"],
}

DESCRIPTION_PATHS = {
    "PDL": ["summary", "headline"],
    "Apollo": ["short_description"],
    "CompanyEnrich": ["description"],
    "ContactOut": ["description"],
    "Coresignal": ["description", "enriched_summary"],
    "Crustdata": [],
}


@dataclass
class ProviderResult:
    provider: str
    records: list[dict[str, Any]]
    invalid_lines: int
    canonical_rows: list[dict[str, Any]]
    record_depth_rows: list[dict[str, Any]]
    bucket_rows: list[dict[str, Any]]
    field_stats_rows: list[dict[str, Any]]
    summary_row: dict[str, Any]
    depth_row: dict[str, Any]


def fmt_number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if math.isclose(value, round(value), abs_tol=1e-9):
        return str(int(round(value)))
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def unwrap_record(provider: str, record: dict[str, Any]) -> dict[str, Any]:
    if provider != "ContactOut":
        return record
    if len(record) == 1:
        value = next(iter(record.values()))
        return value if isinstance(value, dict) else {}
    return record


def load_jsonl(path: Path, provider: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    invalid = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(parsed, dict):
                records.append(unwrap_record(provider, parsed))
    return records, invalid


def values_at_path(obj: Any, path: str) -> list[Any]:
    if not path:
        return []
    current = [obj]
    for part in path.split("."):
        is_array = part.endswith("[]")
        key = part[:-2] if is_array else part
        next_values: list[Any] = []
        for item in current:
            if not isinstance(item, dict) or key not in item:
                continue
            value = item[key]
            if is_array:
                if isinstance(value, list):
                    next_values.extend(value)
                elif value is not None:
                    next_values.append(value)
            else:
                next_values.append(value)
        current = next_values
    return current


def is_zero_like_string(value: str) -> bool:
    stripped = value.strip().lower()
    if stripped in {"", "0", "0.0", "$0", "$0.0", "$0.00"}:
        return True
    cleaned = stripped.replace("$", "").replace(",", "").replace("+", "")
    try:
        return float(cleaned) == 0
    except ValueError:
        return False


def is_present(value: Any, canonical_field: str | None = None, raw_path: str | None = None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if canonical_field in ZERO_PLACEHOLDER_FIELDS:
            return value != 0
        if raw_path and raw_path.split(".")[-1].replace("[]", "") in RAW_ZERO_PLACEHOLDER_NAMES:
            return value != 0
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return False
        lowered = stripped.lower()
        if lowered in {"n/a", "na", "none", "null", "unknown"}:
            return False
        if canonical_field in ZERO_PLACEHOLDER_FIELDS and is_zero_like_string(stripped):
            return False
        if raw_path and raw_path.split(".")[-1].replace("[]", "") in RAW_ZERO_PLACEHOLDER_NAMES:
            return not is_zero_like_string(stripped)
        return True
    if isinstance(value, list):
        return any(is_present(item, canonical_field, raw_path) for item in value)
    if isinstance(value, dict):
        return any(is_present(item, canonical_field, raw_path) for item in value.values())
    return bool(value)


def canonical_value_present(provider: str, record: dict[str, Any], field: str, paths: list[str]) -> bool:
    if not paths:
        return False
    for path in paths:
        values = values_at_path(record, path)
        if field == "crunchbase":
            if any(is_present(v, field) and "crunchbase.com" in str(v).lower() for v in values):
                return True
            continue
        if any(is_present(v, field) for v in values):
            return True
    return False


def count_path_values(record: dict[str, Any], paths: list[str]) -> int:
    total = 0
    for path in paths:
        values = values_at_path(record, path)
        if not values:
            continue
        if len(values) == 1 and isinstance(values[0], (int, float)) and not isinstance(values[0], bool):
            total += int(values[0]) if values[0] > 0 else 0
        else:
            total += sum(1 for value in values if is_present(value, raw_path=path))
    return total


def capped_description_chars(provider: str, record: dict[str, Any]) -> int:
    lengths = [
        min(len(str(value)), 100)
        for path in DESCRIPTION_PATHS.get(provider, [])
        for value in values_at_path(record, path)
        if is_present(value, raw_path=path)
    ]
    return max(lengths) if lengths else 0


def first_present_text(record: dict[str, Any], paths: list[str]) -> str:
    for path in paths:
        for value in values_at_path(record, path):
            if is_present(value, raw_path=path):
                return str(value)
    return ""


class FieldAccumulator:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.records_with_field: dict[str, set[int]] = defaultdict(set)
        self.records_with_value: dict[str, set[int]] = defaultdict(set)
        self.occurrences: Counter[str] = Counter()
        self.types: dict[str, set[str]] = defaultdict(set)
        self.leaf_value_paths_by_record: dict[int, set[str]] = defaultdict(set)

    def blocked(self, path: str) -> bool:
        return path in RAW_STOCK_PATH_BLOCKLIST.get(self.provider, set())

    def add_field(self, path: str, value: Any, record_index: int, value_path: str | None = None) -> None:
        if not path or self.blocked(path):
            return
        self.records_with_field[path].add(record_index)
        self.occurrences[path] += 1
        self.types[path].add(type_name(value))
        if is_present(value, raw_path=value_path or path):
            self.records_with_value[path].add(record_index)

    def add_leaf_value(self, path: str, value: Any, record_index: int) -> None:
        if self.blocked(path):
            return
        if is_present(value, raw_path=path):
            self.leaf_value_paths_by_record[record_index].add(path)


def collect_field_stats(provider: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    acc = FieldAccumulator(provider)

    def walk(value: Any, path: str, record_index: int) -> None:
        if isinstance(value, dict):
            if path:
                acc.add_field(path, value, record_index)
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                walk(child, child_path, record_index)
            return
        if isinstance(value, list):
            if path:
                acc.add_field(path, value, record_index)
            element_path = f"{path}[]"
            scalar_seen = False
            for item in value:
                if isinstance(item, dict):
                    for key, child in item.items():
                        walk(child, f"{element_path}.{key}", record_index)
                elif isinstance(item, list):
                    walk(item, element_path, record_index)
                else:
                    scalar_seen = True
                    acc.add_field(element_path, item, record_index, value_path=element_path)
                    acc.add_leaf_value(element_path, item, record_index)
            if scalar_seen:
                acc.records_with_field[element_path].add(record_index)
            return
        if path:
            acc.add_field(path, value, record_index)
            acc.add_leaf_value(path, value, record_index)

    for index, record in enumerate(records, start=1):
        walk(record, "", index)

    rows: list[dict[str, Any]] = []
    for field in sorted(acc.records_with_field):
        type_list = sorted(t for t in acc.types[field] if t != "null")
        rows.append(
            {
                "field": field,
                "types": "|".join(type_list),
                "records_with_field": len(acc.records_with_field[field]),
                "field_coverage_pct": fmt_number(pct(len(acc.records_with_field[field]), TOTAL_SUBMITTED_DOMAINS)),
                "matched_field_coverage_pct": fmt_number(pct(len(acc.records_with_field[field]), len(records))),
                "records_with_value": len(acc.records_with_value[field]),
                "value_coverage_pct": fmt_number(pct(len(acc.records_with_value[field]), TOTAL_SUBMITTED_DOMAINS)),
                "matched_value_coverage_pct": fmt_number(pct(len(acc.records_with_value[field]), len(records))),
                "returned_record_count": len(records),
                "submitted_domain_count": TOTAL_SUBMITTED_DOMAINS,
                "occurrences": acc.occurrences[field],
            }
        )

    raw_non_empty_leaf_counts = [
        len(acc.leaf_value_paths_by_record.get(i, set())) for i in range(1, len(records) + 1)
    ]
    return rows, raw_non_empty_leaf_counts


def build_provider_result(provider: str, input_dir: Path) -> ProviderResult:
    records, invalid = load_jsonl(input_dir / SOURCE_FILES[provider], provider)
    mapping = PROVIDER_FIELD_MAPPING[provider]
    canonical_presence: dict[str, list[bool]] = {}

    for _, field in CANONICAL_FIELDS:
        canonical_presence[field] = [
            canonical_value_present(provider, record, field, mapping.get(field, [])) for record in records
        ]

    canonical_rows: list[dict[str, Any]] = []
    for bucket, field in CANONICAL_FIELDS:
        count = sum(canonical_presence[field])
        paths = mapping.get(field, [])
        canonical_rows.append(
            {
                "provider": provider,
                "bucket": bucket,
                "canonical_field": field,
                "mapped_paths": "|".join(paths),
                "records_with_value": count,
                "submitted_domain_count": TOTAL_SUBMITTED_DOMAINS,
                "returned_record_count": len(records),
                "coverage_pct": fmt_number(pct(count, TOTAL_SUBMITTED_DOMAINS)),
                "matched_record_coverage_pct": fmt_number(pct(count, len(records))),
            }
        )

    field_stats_rows, raw_leaf_counts = collect_field_stats(provider, records)

    record_depth_rows: list[dict[str, Any]] = []
    canonical_totals: list[int] = []
    keyword_counts: list[int] = []
    technology_counts: list[int] = []
    social_counts: list[int] = []
    location_contact_counts: list[int] = []
    funding_round_counts: list[int] = []
    org_relation_counts: list[int] = []
    multi_location_counts: list[int] = []
    description_chars: list[int] = []

    name_paths = mapping.get("name", [])
    domain_paths = mapping.get("domain_website", [])
    social_fields = ["linkedin", "facebook", "twitter_x", "instagram", "youtube", "crunchbase", "follower_count"]
    location_fields = ["location", "phone", "email"]

    for index, record in enumerate(records, start=1):
        present_count = sum(canonical_presence[field][index - 1] for _, field in CANONICAL_FIELDS)
        canonical_totals.append(present_count)
        keyword_count = count_path_values(record, KEYWORD_PATHS.get(provider, []))
        technology_count = count_path_values(record, TECHNOLOGY_PATHS.get(provider, []))
        funding_round_count = count_path_values(record, FUNDING_ROUND_PATHS.get(provider, []))
        org_relation_count = count_path_values(record, ORG_RELATION_PATHS.get(provider, []))
        multi_location_count = count_path_values(record, MULTI_LOCATION_PATHS.get(provider, []))
        social_count = sum(canonical_presence[field][index - 1] for field in social_fields)
        location_contact_count = sum(canonical_presence[field][index - 1] for field in location_fields)
        desc_chars = capped_description_chars(provider, record)

        keyword_counts.append(keyword_count)
        technology_counts.append(technology_count)
        social_counts.append(social_count)
        location_contact_counts.append(location_contact_count)
        funding_round_counts.append(funding_round_count)
        org_relation_counts.append(org_relation_count)
        multi_location_counts.append(multi_location_count)
        description_chars.append(desc_chars)

        record_depth_rows.append(
            {
                "provider": provider,
                "record_index": index,
                "name": first_present_text(record, name_paths),
                "domain_or_website": first_present_text(record, domain_paths),
                "canonical_fields_present": present_count,
                "canonical_fields_possible": len(CANONICAL_FIELDS),
                "canonical_depth_pct": fmt_number(pct(present_count, len(CANONICAL_FIELDS))),
                "raw_non_empty_leaf_fields": raw_leaf_counts[index - 1] if index - 1 < len(raw_leaf_counts) else 0,
                "keyword_count": keyword_count,
                "technology_count": technology_count,
                "social_profile_count": social_count,
                "location_contact_field_count": location_contact_count,
                "funding_round_count": funding_round_count,
                "org_relation_count": org_relation_count,
                "multi_location_count": multi_location_count,
                "description_chars": desc_chars,
            }
        )

    bucket_rows: list[dict[str, Any]] = []
    fields_by_bucket: dict[str, list[str]] = defaultdict(list)
    for bucket, field in CANONICAL_FIELDS:
        fields_by_bucket[bucket].append(field)

    for bucket in BUCKET_WEIGHTS:
        fields = fields_by_bucket[bucket]
        field_count = len(fields)
        field_sum = sum(sum(canonical_presence[field]) for field in fields)
        any_count = 0
        all_count = 0
        for i in range(len(records)):
            values = [canonical_presence[field][i] for field in fields]
            if any(values):
                any_count += 1
            if fields and all(values):
                all_count += 1
        bucket_rows.append(
            {
                "provider": provider,
                "bucket": bucket,
                "bucket_weight": BUCKET_WEIGHTS[bucket],
                "canonical_fields_in_bucket": field_count,
                "submitted_domain_count": TOTAL_SUBMITTED_DOMAINS,
                "returned_record_count": len(records),
                "avg_field_coverage_pct": fmt_number(pct(field_sum, TOTAL_SUBMITTED_DOMAINS * field_count)),
                "matched_avg_field_coverage_pct": fmt_number(pct(field_sum, len(records) * field_count)),
                "records_with_any_bucket_value": any_count,
                "any_bucket_coverage_pct": fmt_number(pct(any_count, TOTAL_SUBMITTED_DOMAINS)),
                "matched_any_bucket_coverage_pct": fmt_number(pct(any_count, len(records))),
                "records_with_all_bucket_values": all_count,
                "complete_bucket_coverage_pct": fmt_number(pct(all_count, TOTAL_SUBMITTED_DOMAINS)),
                "matched_complete_bucket_coverage_pct": fmt_number(pct(all_count, len(records))),
            }
        )

    def avg(values: list[int]) -> float:
        return sum(values) / len(values) if values else 0.0

    summary_row = {
        "provider": provider,
        "domains_submitted": TOTAL_SUBMITTED_DOMAINS,
        "record_count": len(records),
        "not_enriched": TOTAL_SUBMITTED_DOMAINS - len(records),
        "find_rate_pct": fmt_number(pct(len(records), TOTAL_SUBMITTED_DOMAINS)),
        "invalid_jsonl_lines": invalid,
        "raw_field_count": sum(
            1
            for row in field_stats_rows
            if "object" not in str(row["types"]).split("|") and "array" not in str(row["types"]).split("|")
        ),
        "avg_canonical_fields_present": fmt_number(round(avg(canonical_totals), 2)),
        "median_canonical_fields_present": fmt_number(float(median(canonical_totals)) if canonical_totals else 0),
        "avg_raw_non_empty_leaf_fields": fmt_number(round(avg(raw_leaf_counts), 2)),
        "median_raw_non_empty_leaf_fields": fmt_number(float(median(raw_leaf_counts)) if raw_leaf_counts else 0),
        "avg_keyword_count": fmt_number(round(avg(keyword_counts), 2)),
        "avg_technology_count": fmt_number(round(avg(technology_counts), 2)),
        "avg_social_profile_count": fmt_number(round(avg(social_counts), 2)),
        "avg_location_contact_field_count": fmt_number(round(avg(location_contact_counts), 2)),
        "avg_description_chars": fmt_number(round(avg(description_chars), 2)),
        "rank": PROVIDER_ORDER.index(provider) + 1,
    }

    depth_row = {
        "provider": provider,
        "domains_submitted": TOTAL_SUBMITTED_DOMAINS,
        "record_count": len(records),
        "not_enriched": TOTAL_SUBMITTED_DOMAINS - len(records),
        "find_rate_pct": fmt_number(pct(len(records), TOTAL_SUBMITTED_DOMAINS)),
        "raw_field_count": summary_row["raw_field_count"],
        "avg_raw_non_empty_leaf_fields": summary_row["avg_raw_non_empty_leaf_fields"],
        "median_raw_non_empty_leaf_fields": summary_row["median_raw_non_empty_leaf_fields"],
        "avg_canonical_fields_present": summary_row["avg_canonical_fields_present"],
        "median_canonical_fields_present": summary_row["median_canonical_fields_present"],
        "avg_canonical_depth_pct": fmt_number(pct(avg(canonical_totals), len(CANONICAL_FIELDS))),
        "all_request_avg_canonical_depth_pct": fmt_number(
            pct(sum(canonical_totals), TOTAL_SUBMITTED_DOMAINS * len(CANONICAL_FIELDS))
        ),
        "avg_keyword_count": summary_row["avg_keyword_count"],
        "avg_technology_count": summary_row["avg_technology_count"],
        "avg_social_profile_count": summary_row["avg_social_profile_count"],
        "avg_location_contact_field_count": summary_row["avg_location_contact_field_count"],
        "avg_funding_round_count": fmt_number(round(avg(funding_round_counts), 2)),
        "avg_org_relation_count": fmt_number(round(avg(org_relation_counts), 2)),
        "avg_multi_location_count": fmt_number(round(avg(multi_location_counts), 2)),
        "avg_description_chars": summary_row["avg_description_chars"],
    }

    return ProviderResult(
        provider=provider,
        records=records,
        invalid_lines=invalid,
        canonical_rows=canonical_rows,
        record_depth_rows=record_depth_rows,
        bucket_rows=bucket_rows,
        field_stats_rows=field_stats_rows,
        summary_row=summary_row,
        depth_row=depth_row,
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_config(path: Path) -> None:
    config = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_files": SOURCE_FILES,
        "notes": [
            "Generated from provider JSONL files only. Public JSONL inputs may be masked publication copies.",
            "Provider-specific market identifier fields are excluded from benchmark metrics.",
            "Category averages are calculated from populated canonical field counts divided by the submitted-domain denominator and the number of fields in each category.",
            "Returned-profile depth uses the average and median number of populated canonical fields on enriched records.",
            "ContactOut records are unwrapped from their dynamic top-level domain key before counting.",
            "Country/state/city/street-or-full-address/postal-code are combined into one canonical location field.",
            "Placeholder values are treated as missing where applicable: numeric 0 for employee count, revenue, founded year, and follower count; and N/A-style strings for revenue.",
            "People Data Labs profiles[] is counted as Crunchbase only when the individual profile URL contains crunchbase.com.",
            "The canonical domain and website fields are merged into domain_website to avoid double-counting the same signal.",
            "Parent, subsidiary, and affiliate fields are merged into corporate_relations.",
            "Funding rounds, total funding, and funding stage are merged into funding_data.",
        ],
        "bucket_weights": BUCKET_WEIGHTS,
        "canonical_fields": [{"bucket": bucket, "field": field} for bucket, field in CANONICAL_FIELDS],
        "provider_field_mapping": PROVIDER_FIELD_MAPPING,
        "total_submitted_domains": TOTAL_SUBMITTED_DOMAINS,
    }
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def generate(input_dir: Path, output_dir: Path) -> None:
    results = [build_provider_result(provider, input_dir) for provider in PROVIDER_ORDER]

    summary_fields = [
        "provider",
        "domains_submitted",
        "record_count",
        "not_enriched",
        "find_rate_pct",
        "invalid_jsonl_lines",
        "raw_field_count",
        "avg_canonical_fields_present",
        "median_canonical_fields_present",
        "avg_raw_non_empty_leaf_fields",
        "median_raw_non_empty_leaf_fields",
        "avg_keyword_count",
        "avg_technology_count",
        "avg_social_profile_count",
        "avg_location_contact_field_count",
        "avg_description_chars",
        "rank",
    ]
    depth_fields = [
        "provider",
        "domains_submitted",
        "record_count",
        "not_enriched",
        "find_rate_pct",
        "raw_field_count",
        "avg_raw_non_empty_leaf_fields",
        "median_raw_non_empty_leaf_fields",
        "avg_canonical_fields_present",
        "median_canonical_fields_present",
        "avg_canonical_depth_pct",
        "all_request_avg_canonical_depth_pct",
        "avg_keyword_count",
        "avg_technology_count",
        "avg_social_profile_count",
        "avg_location_contact_field_count",
        "avg_funding_round_count",
        "avg_org_relation_count",
        "avg_multi_location_count",
        "avg_description_chars",
    ]
    canonical_fields = [
        "provider",
        "bucket",
        "canonical_field",
        "mapped_paths",
        "records_with_value",
        "submitted_domain_count",
        "returned_record_count",
        "coverage_pct",
        "matched_record_coverage_pct",
    ]
    bucket_fields = [
        "provider",
        "bucket",
        "bucket_weight",
        "canonical_fields_in_bucket",
        "submitted_domain_count",
        "returned_record_count",
        "avg_field_coverage_pct",
        "matched_avg_field_coverage_pct",
        "records_with_any_bucket_value",
        "any_bucket_coverage_pct",
        "matched_any_bucket_coverage_pct",
        "records_with_all_bucket_values",
        "complete_bucket_coverage_pct",
        "matched_complete_bucket_coverage_pct",
    ]
    record_depth_fields = [
        "provider",
        "record_index",
        "name",
        "domain_or_website",
        "canonical_fields_present",
        "canonical_fields_possible",
        "canonical_depth_pct",
        "raw_non_empty_leaf_fields",
        "keyword_count",
        "technology_count",
        "social_profile_count",
        "location_contact_field_count",
        "funding_round_count",
        "org_relation_count",
        "multi_location_count",
        "description_chars",
    ]
    field_stat_fields = [
        "field",
        "types",
        "records_with_field",
        "field_coverage_pct",
        "matched_field_coverage_pct",
        "records_with_value",
        "value_coverage_pct",
        "matched_value_coverage_pct",
        "returned_record_count",
        "submitted_domain_count",
        "occurrences",
    ]

    by_provider = {result.provider: result for result in results}
    write_csv(output_dir / "benchmark_provider_summary.csv", [r.summary_row for r in results], summary_fields)
    write_csv(output_dir / "benchmark_depth_metrics.csv", [r.depth_row for r in results], depth_fields)
    write_csv(
        output_dir / "benchmark_canonical_field_coverage.csv",
        [row for result in results for row in result.canonical_rows],
        canonical_fields,
    )
    write_csv(
        output_dir / "benchmark_bucket_coverage.csv",
        [row for result in results for row in result.bucket_rows],
        bucket_fields,
    )
    write_csv(
        output_dir / "benchmark_record_depth.csv",
        [row for result in results for row in result.record_depth_rows],
        record_depth_fields,
    )
    for provider, file_name in FIELD_STAT_FILES.items():
        write_csv(output_dir / file_name, by_provider[provider].field_stats_rows, field_stat_fields)
    write_config(output_dir / "benchmark_config.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate benchmark CSVs from provider JSONL files.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
        help="Directory containing provider JSONL files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "generated",
        help="Directory to write generated CSV/JSON outputs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate(args.input_dir, args.output_dir)
    print(f"Generated benchmark outputs in {args.output_dir}")


if __name__ == "__main__":
    main()
