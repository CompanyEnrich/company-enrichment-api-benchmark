#!/usr/bin/env python3
"""
Mask provider JSONL files for public publication.

The redactor preserves JSON shape, keys, null/empty values, booleans, and array
lengths. It partially masks strings instead of replacing every value with a
single placeholder, and it buckets sensitive numeric values such as revenue,
funding amounts, employee counts, and follower counts.

Default usage writes masked copies to data/redacted:
  python3 redact_provider_outputs.py

To replace the checked-in provider JSONL files with masked publication copies:
  python3 redact_provider_outputs.py --in-place

To mask per-record identifier columns after benchmark CSVs have been generated:
  python3 redact_provider_outputs.py --skip-jsonl --record-depth-csv data/benchmark_record_depth.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_INPUT_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR / "redacted"
DEFAULT_FILES = [
    "apollo.jsonl",
    "companyenrich.jsonl",
    "contactout.jsonl",
    "coresignal.jsonl",
    "crustdata.jsonl",
    "pdl.jsonl",
]

MISSING_STRINGS = {"", "n/a", "na", "none", "null", "unknown"}

PLATFORM_HOSTS = {
    "crunchbase.com",
    "www.crunchbase.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "linkedin.com",
    "www.linkedin.com",
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
    "youtube.com",
    "www.youtube.com",
}

STRUCTURAL_URL_PARTS = {
    "company",
    "companies",
    "in",
    "organization",
    "people",
    "pub",
    "school",
    "showcase",
}

TEXT_KEY_TERMS = {
    "about",
    "bio",
    "content",
    "description",
    "headline",
    "metadata_description",
    "metadata_title",
    "post",
    "summary",
    "title",
}

TEXT_VALUE_KEY_TERMS = {
    "category",
    "categories",
    "industry",
    "industries",
    "keyword",
    "keywords",
    "language",
    "languages",
    "specialities",
    "specialties",
    "tag",
    "tags",
    "technology",
    "technologies",
    "technology_names",
}

NAME_KEY_TERMS = {
    "display_name",
    "name",
    "profile_name",
}

ADDRESS_KEY_TERMS = {
    "address",
    "city",
    "geo",
    "headquarter",
    "location",
    "locality",
    "postal_code",
    "region",
    "state",
    "street",
}

ID_KEY_TERMS = {
    "company_id",
    "crustdata_company_id",
    "id",
    "linkedin_id",
    "linkedin_source_id",
    "linkedin_uid",
    "professional_network_id",
    "slug",
    "uid",
    "urn",
}

DATE_KEY_TERMS = {
    "created_at",
    "date",
    "first_verified_at",
    "last_updated",
    "last_verified_at",
    "updated_at",
}

COUNT_KEY_TERMS = {
    "employee",
    "employees",
    "follower",
    "followers",
    "head_count",
}

MONEY_KEY_TERMS = {
    "amount",
    "annual_revenue",
    "funding",
    "money",
    "raised",
    "revenue",
    "total_funding",
}

ROUND_COUNT_KEY_TERMS = {
    "number_funding_rounds",
    "number_of_funding_rounds",
    "rounds_count",
    "total_rounds_count",
}

DOMAIN_RE = re.compile(r"(?i)^(?:[a-z0-9-]+\.)+[a-z]{2,}$")
DOMAIN_WITH_PATH_RE = re.compile(r"(?i)^(?:[a-z0-9-]+\.)+[a-z]{2,}/")
EMAIL_RE = re.compile(r"(?i)^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_CHARS_RE = re.compile(r"^[+\d\s().-]{7,}$")
DATE_RE = re.compile(r"^(\d{4})(-\d{2})?(-\d{2})?$")
BUCKET_RE = re.compile(
    r"^(?:"
    r"\d{4}s|"
    r"\d+-\d+|"
    r"\d+\+|"
    r"<\d+[KMB]|"
    r"\d+[KMB]-\d+[KMB]|"
    r"\d+[KMB]\+"
    r")$"
)
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[^A-Za-z0-9]+")

KEY_ALIASES: dict[tuple[str, str], dict[str, str]] = {}


def normalize_key(value: str) -> str:
    return value.lower().replace("-", "_").replace(".", "_")


def path_text(path: str, key: str) -> str:
    return normalize_key(f"{path}.{key}" if path else key)


def has_term(path: str, key: str, terms: set[str]) -> bool:
    text = path_text(path, key)
    return any(term in text for term in terms)


def mask_alpha(value: str) -> str:
    length = len(value)
    if length <= 1:
        return "*"
    if length == 2:
        return value[0] + "*"
    if length == 3:
        return value[0] + "*" + value[-1]
    if length == 4:
        return value[0] + ("*" * 2) + value[-1]
    keep = 2 if length >= 6 else 1
    middle = "*" * (length - (keep * 2))
    return value[:keep] + middle + value[-keep:]


def mask_digits(value: str, keep_last: int = 2) -> str:
    if len(value) <= keep_last:
        return "*" * len(value)
    return ("*" * (len(value) - keep_last)) + value[-keep_last:]


def mask_token(value: str) -> str:
    if value.isdigit():
        return mask_digits(value)
    return mask_alpha(value)


def mask_phrase(value: str) -> str:
    return "".join(mask_token(part) if part.isalnum() else part for part in TOKEN_RE.findall(value))


def mask_identifier(value: str) -> str:
    stripped = value.strip()
    if stripped == "":
        return stripped
    parts = TOKEN_RE.findall(stripped)
    return "".join(mask_token(part) if part.isalnum() else part for part in parts)


def is_platform_host(host: str) -> bool:
    lower = host.lower().strip(".")
    return lower in PLATFORM_HOSTS


def mask_host(host: str) -> str:
    if not host:
        return host
    if is_platform_host(host):
        return host

    port = ""
    host_part = host
    if ":" in host and host.rsplit(":", 1)[1].isdigit():
        host_part, port = host.rsplit(":", 1)
        port = f":{port}"

    labels = host_part.split(".")
    if len(labels) < 2:
        return mask_phrase(host)

    masked: list[str] = []
    for index, label in enumerate(labels):
        if index == len(labels) - 1 or label.lower() == "www":
            masked.append(label)
        else:
            masked.append(mask_alpha(label))
    return ".".join(masked) + port


def mask_domain(value: str) -> str:
    return mask_host(value.strip())


def mask_url_path(path: str) -> str:
    if not path:
        return ""
    parts = path.split("/")
    masked: list[str] = []
    for part in parts:
        if part == "" or part.lower() in STRUCTURAL_URL_PARTS:
            masked.append(part)
        else:
            masked.append(mask_identifier(part))
    return "/".join(masked)


def mask_url(value: str) -> str:
    stripped = value.strip()
    had_scheme = "://" in stripped
    parse_target = stripped if had_scheme else f"https://{stripped}"
    parsed = urlsplit(parse_target)

    if not parsed.netloc:
        return mask_phrase(value)

    masked_host = mask_host(parsed.netloc)
    masked_path = mask_url_path(parsed.path)
    masked = urlunsplit((parsed.scheme, masked_host, masked_path, "", ""))
    if not had_scheme:
        return masked.removeprefix("https://")
    return masked


def mask_email(value: str) -> str:
    local, domain = value.split("@", 1)
    return f"{mask_identifier(local)}@{mask_domain(domain)}"


def mask_phone(value: str) -> str:
    digits = [char for char in value if char.isdigit()]
    if len(digits) < 7:
        return mask_phrase(value)

    keep_first_digits = 1 if value.strip().startswith("+") and len(digits) > 10 else 0
    keep_last_digits = 4
    digit_index = 0
    result: list[str] = []
    for char in value:
        if not char.isdigit():
            result.append(char)
            continue
        keep = digit_index < keep_first_digits or digit_index >= len(digits) - keep_last_digits
        result.append(char if keep else "*")
        digit_index += 1
    return "".join(result)


def bucket_count(value: float) -> str | int:
    if value == 0:
        return 0
    if value <= 10:
        return "1-10"
    if value <= 50:
        return "11-50"
    if value <= 200:
        return "51-200"
    if value <= 500:
        return "201-500"
    if value <= 1000:
        return "501-1000"
    if value <= 5000:
        return "1001-5000"
    if value <= 10000:
        return "5001-10000"
    return "10001+"


def bucket_money(value: float) -> str | int:
    if value == 0:
        return 0
    if value < 1_000:
        return "<1K"
    if value < 10_000:
        return "1K-10K"
    if value < 100_000:
        return "10K-100K"
    if value < 1_000_000:
        return "100K-1M"
    if value < 10_000_000:
        return "1M-10M"
    if value < 100_000_000:
        return "10M-100M"
    if value < 1_000_000_000:
        return "100M-1B"
    return "1B+"


def decade_from_year(year: int) -> str | int:
    if year == 0:
        return 0
    if year < 1000 or year > 2100:
        return mask_identifier(str(year))
    return f"{(year // 10) * 10}s"


def redact_number(value: int | float, path: str, key: str) -> Any:
    if has_term(path, key, {"founded", "founded_year"}):
        return decade_from_year(int(value))
    if has_term(path, key, ROUND_COUNT_KEY_TERMS):
        return value
    if has_term(path, key, MONEY_KEY_TERMS):
        return bucket_money(float(value))
    if has_term(path, key, COUNT_KEY_TERMS):
        return bucket_count(float(value))
    if has_term(path, key, ID_KEY_TERMS):
        return mask_identifier(str(value))
    return value


def redact_date_string(value: str) -> str | None:
    match = DATE_RE.match(value.strip())
    if not match:
        return None
    year = int(match.group(1))
    bucket = decade_from_year(year)
    return str(bucket)


def redact_string(value: str, path: str, key: str) -> str:
    stripped = value.strip()
    if stripped.lower() in MISSING_STRINGS:
        return value
    if "*" in value:
        return value
    if BUCKET_RE.match(stripped) and has_term(
        path,
        key,
        DATE_KEY_TERMS | MONEY_KEY_TERMS | COUNT_KEY_TERMS | {"founded", "founded_year"},
    ):
        return value

    date_value = redact_date_string(stripped)
    if date_value is not None and has_term(path, key, DATE_KEY_TERMS | {"founded", "founded_year"}):
        return date_value

    if EMAIL_RE.match(stripped):
        return mask_email(stripped)

    if stripped.startswith(("http://", "https://")) or DOMAIN_WITH_PATH_RE.match(stripped):
        return mask_url(stripped)

    if DOMAIN_RE.match(stripped):
        return mask_domain(stripped)

    if has_term(path, key, {"phone", "sanitized_phone"}) and PHONE_CHARS_RE.match(stripped):
        return mask_phone(stripped)

    if has_term(path, key, MONEY_KEY_TERMS):
        return mask_phrase(stripped)

    if has_term(path, key, ID_KEY_TERMS):
        return mask_identifier(stripped)

    if has_term(path, key, TEXT_KEY_TERMS):
        return mask_phrase(value)

    if has_term(path, key, TEXT_VALUE_KEY_TERMS | NAME_KEY_TERMS | ADDRESS_KEY_TERMS):
        return mask_phrase(value)

    if len(stripped) > 80:
        return mask_phrase(value)

    return mask_phrase(value)


def redact_key(key: str, path: str) -> str:
    if DOMAIN_RE.match(key):
        return mask_domain(key)
    if EMAIL_RE.match(key):
        return mask_email(key)
    if DOMAIN_WITH_PATH_RE.match(key):
        return mask_url(key)
    if normalize_key(path).endswith("_hash") or normalize_key(path).endswith("hash"):
        return mask_phrase(key)
    return key


def unique_redacted_key(key: str, path: str) -> str:
    base_key = redact_key(key, path)
    if base_key == key:
        return base_key

    alias_group = KEY_ALIASES.setdefault((path, base_key), {})
    if key not in alias_group:
        suffix = "" if not alias_group else f"__{len(alias_group) + 1}"
        alias_group[key] = f"{base_key}{suffix}"
    return alias_group[key]


def redact_value(value: Any, path: str = "", key: str = "") -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for child_key, child_value in value.items():
            key_text = str(child_key)
            masked_key = unique_redacted_key(key_text, path)
            child_path = f"{path}.{masked_key}" if path else masked_key
            redacted[masked_key] = redact_value(child_value, child_path, key_text)
        return redacted
    if isinstance(value, list):
        return [redact_value(item, path, key) for item in value]
    if isinstance(value, str):
        return redact_string(value, path, key)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return redact_number(value, path, key)
    if isinstance(value, float):
        return redact_number(value, path, key)
    return value


def redact_jsonl_file(input_path: Path, output_path: Path) -> tuple[int, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = 0
    invalid = 0

    with input_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            redacted = redact_value(parsed)
            target.write(json.dumps(redacted, ensure_ascii=True, separators=(",", ":")) + "\n")
            records += 1

    return records, invalid


def redact_in_place(path: Path) -> tuple[int, int]:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as handle:
        temp_path = Path(handle.name)

    try:
        records, invalid = redact_jsonl_file(path, temp_path)
        temp_path.replace(path)
        return records, invalid
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def redact_record_depth_csv(path: Path) -> int:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="", dir=path.parent) as handle:
        temp_path = Path(handle.name)

    rows = 0
    try:
        with path.open("r", encoding="utf-8", newline="") as source, temp_path.open(
            "w", encoding="utf-8", newline=""
        ) as target:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                raise ValueError(f"{path} has no CSV header")
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                if "name" in row:
                    row["name"] = redact_string(row["name"], "benchmark_record_depth", "name")
                if "domain_or_website" in row:
                    row["domain_or_website"] = redact_string(
                        row["domain_or_website"], "benchmark_record_depth", "domain_or_website"
                    )
                writer.writerow(row)
                rows += 1
        temp_path.replace(path)
        return rows
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mask provider JSONL files for public publication.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing provider JSONL files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write masked copies when not using --in-place.",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=DEFAULT_FILES,
        help="Provider JSONL filenames to redact.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite input files with masked publication copies.",
    )
    parser.add_argument(
        "--skip-jsonl",
        action="store_true",
        help="Skip provider JSONL files and only process other requested artifacts.",
    )
    parser.add_argument(
        "--record-depth-csv",
        type=Path,
        help="Optional benchmark_record_depth.csv path whose name/domain columns should be masked in place.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.skip_jsonl:
        for file_name in args.files:
            input_path = args.input_dir / file_name
            if args.in_place:
                records, invalid = redact_in_place(input_path)
                output_path = input_path
            else:
                output_path = args.output_dir / file_name
                records, invalid = redact_jsonl_file(input_path, output_path)

            invalid_note = f", skipped {invalid} invalid lines" if invalid else ""
            print(f"Redacted {records} records from {input_path} -> {output_path}{invalid_note}")

    if args.record_depth_csv:
        rows = redact_record_depth_csv(args.record_depth_csv)
        print(f"Redacted {rows} record-depth rows in {args.record_depth_csv}")


if __name__ == "__main__":
    main()
