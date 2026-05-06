"""
Generate 500 diverse random .com domains using Majestic Million.

Why Majestic instead of Common Crawl:
- Common Crawl endpoints are currently unreliable (503/504 errors, timeouts)
- Common Crawl shards are alphabetically narrow, making diversity hard
- Majestic Million is free, reliable, and gives you real active domains
- Perfect for API coverage testing
"""

import csv
import gzip
import io
import random
from collections import defaultdict

import requests

# ============ CONFIG ============
MAJESTIC_URL = "https://downloads.majestic.com/majestic_million.csv"
TARGET_SAMPLE = 500
MAX_PER_TWO_CHAR = 3
OUTPUT_FILE = "random_domains.csv"
# ================================


def is_valid_com(domain):
    # Must be exactly xxx.com (no subdomains, no private TLDs like cn.com)
    parts = domain.split(".")
    if len(parts) != 2:
        return False
    if parts[1] != "com":
        return False
    sld = parts[0]
    if not sld or len(sld) < 2:
        return False
    if any(c.isdigit() for c in sld):
        return False
    if not all(c.isalpha() for c in sld):
        return False
    return True


def download_majestic():
    print("Downloading Majestic Million...")
    resp = requests.get(MAJESTIC_URL, timeout=300)
    resp.raise_for_status()

    raw = resp.content
    # Majestic serves gzipped CSV
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    content = raw.decode("utf-8", errors="ignore")
    print(f"  downloaded and decompressed {len(content) / 1024 / 1024:.1f} MB")

    domains = []
    reader = csv.DictReader(io.StringIO(content))
    # Majestic columns: GlobalRank, TldRank, Domain, TLD, ...
    for row in reader:
        d = row.get("Domain", "").lower().strip()
        if d:
            domains.append(d)

    print(f"  loaded {len(domains)} domains")
    return domains


def diverse_sample(domains, target_count, max_per_two_char):
    shuffled = list(domains)
    random.shuffle(shuffled)

    # Layer 1: cap per 2-char prefix
    two_char_count = defaultdict(int)
    diverse_pool = []
    for d in shuffled:
        sld = d.split(".")[0]
        if len(sld) < 2:
            continue
        two = sld[:2]
        if two_char_count[two] < max_per_two_char:
            diverse_pool.append(d)
            two_char_count[two] += 1

    print(f"\nAfter 2-char cap (max {max_per_two_char}): {len(diverse_pool)} domains")
    print(f"Unique 2-char prefixes covered: {len(two_char_count)}")

    # Layer 2: proportional per letter
    by_letter = defaultdict(list)
    for d in diverse_pool:
        by_letter[d[0]].append(d)

    letters = sorted(by_letter.keys())
    per_letter = target_count // len(letters)

    final = []
    for letter in letters:
        batch = by_letter[letter]
        take = min(per_letter, len(batch))
        final.extend(random.sample(batch, take))

    if len(final) < target_count:
        taken = set(final)
        leftover = [d for d in diverse_pool if d not in taken]
        needed = target_count - len(final)
        if leftover:
            final.extend(random.sample(leftover, min(needed, len(leftover))))

    return final


def main():
    raw_domains = download_majestic()

    print("\nFiltering to .com only, no digits, no hyphens...")
    filtered = [d for d in raw_domains if is_valid_com(d)]
    filtered = list(set(filtered))
    print(f"  after filtering: {len(filtered)} domains")

    sample = diverse_sample(filtered, TARGET_SAMPLE, MAX_PER_TWO_CHAR)

    post_dist = defaultdict(int)
    for d in sample:
        post_dist[d[0]] += 1

    print(f"\nFinal distribution ({len(sample)} domains):")
    for letter in sorted(post_dist.keys()):
        bar = "#" * post_dist[letter]
        print(f"  {letter}: {post_dist[letter]:3d} {bar}")

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["domain"])
        for d in sorted(sample):
            writer.writerow([d])

    print(f"\nWrote {len(sample)} domains to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
