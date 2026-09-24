import math
import re
import statistics

from app.schemas import CompListing, CompsSummary

# Company name, optionally followed by a bounded allowlist of condition words
# ("PSA GEM MINT 10", "BGS Gem Mint 9.5"), then the grade. Arbitrary words must
# NOT bridge the gap — "Vintage PSA card lot of 10" is not a graded listing.
_GRADED_RE = re.compile(
    r"\b(PSA|BGS|SGC|CGC|TAG)"
    r"(?:\s+(?:GEM|MINT|MT|GM|PRISTINE|AUTHENTIC))*"
    r"\s*\.?\s*(10|[1-9](?:\.5)?)\b",
    re.I,
)

# Titles that indicate the listing is not a single authentic copy of the card:
# reprints/customs, multi-card lots, box breaks, pick-your-card menus, damaged
# copies, stickers. Matched case-insensitively with word boundaries so player
# and team names ("Lottery Pick", "Charlotte") never trip the filter.
# Only unambiguous nouns are pluralized; `lot`, `custom`, `sticker`, `break`
# stay singular because their plurals have legitimate uses ("customs card",
# "breaks records") — so "customs" passing through is accepted collateral.
# Contributions welcome — keep additions conservative: a false positive here
# silently drops a real comp and skews the value range.
_JUNK_PATTERNS = (
    r"\breprints?\b",
    r"\brp\b",         # dropping the odd "RP" relief-pitcher title is the
                       # deliberate fail-safe direction (junk in raw is worse)
    r"\bcustom\b",
    r"\bnovelt(?:y|ies)\b",
    r"\bprox(?:y|ies)\b",
    r"\breplicas?\b",
    r"\bfacsimiles?\b",
    r"\bart\s+card\b",
    r"\baceo\b",
    r"\bdigital\b",
    r"\blot\b",        # covers "lot of 10", "(lot)"; \b keeps "Lottery" safe
    r"\bbreak\b",
    r"\byou\s+pick\b",
    r"\bu\s+pick\b",
    r"\bchoose\b",
    r"\bpick\s+your\b",
    r"\bdamaged\b",
    r"\bsticker\b",
    r"\bdecals?\b",
)
_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS), re.I)


def is_graded(title: str) -> bool:
    return bool(_GRADED_RE.search(title))


def is_junk(title: str, query: str = "") -> bool:
    """True when the title marks a listing that must not price this card.

    A junk token that already appears in `query` (the vision search string) is
    part of the card's NAME, not a junk signal — "Greninja BREAK", "Digital
    Bug" — so that match is exempt. The exemption is checked with the same
    word-boundary, case-insensitive matching as _JUNK_RE, and is per-token:
    tokens absent from the query still junk ("case break" on ordinary scans).
    """
    for m in _JUNK_RE.finditer(title):
        if not query or not re.search(rf"\b{re.escape(m.group(0))}\b", query, re.I):
            return True
    return False


def _robust_low(prices: list[float]) -> float | None:
    """Trimmed floor for a sorted price list — the 'low' a buyer can trust.

    A single mispriced or junk listing must never set the value floor, so:
      - 8+ prices: 10th percentile by nearest-rank, clamped to at least the
        second-lowest so one outlier can never be the floor;
      - 4-7 prices: second-lowest;
      - 1-3 prices: minimum (too few points to trim anything).
    """
    if not prices:
        return None
    n = len(prices)
    if n >= 8:
        rank = max(2, math.ceil(0.10 * n))
        return prices[rank - 1]
    if n >= 4:
        return prices[1]
    return prices[0]


def _floor(bucket: list[float]) -> float | None:
    """Value floor for a sorted price bucket, resistant to cheap-junk floods.

    The percentile trim in _robust_low is defeated when >= ~10% of the bucket
    is clean-titled cheap junk (fresh $0.99 auctions are exactly this), so the
    floor is computed from prices at or above 30% of the bucket median. If
    that leaves fewer than 2 candidates (only possible for tiny buckets), fall
    back to the whole bucket.
    """
    if not bucket:
        return None
    candidates = [p for p in bucket if p >= 0.3 * statistics.median(bucket)]
    if len(candidates) < 2:
        candidates = bucket
    return _robust_low(candidates)


def _clamp_to_median(low: float | None, median: float | None) -> float | None:
    """A robust low above its own median is a floor artifact, not a floor."""
    if low is None or median is None:
        return low
    return min(low, median)


def _bucket_stats(prices: list[float]) -> tuple[float | None, float | None]:
    """(robust low, median) for a sorted price bucket.

    The low is a trimmed floor (_floor), not an absolute minimum. In bimodal
    buckets (half junk-cheap, half real) the 30%-of-median flood guard can
    push the floor above a median that straddles both modes, so the low is
    clamped to the median — a range must never read $50–$26.
    """
    if not prices:
        return None, None
    median = statistics.median(prices)
    return _clamp_to_median(_floor(prices), median), median


def summarize(listings: list[CompListing], source: str,
              query: str = "") -> CompsSummary:
    # Junk is dropped before bucketing so counts, lows, and medians all
    # reflect only plausible comps (a graded lot is junk too).
    kept = [l for l in listings if not is_junk(l.title, query=query)]
    raw = sorted(l.price for l in kept if not l.graded)
    graded = sorted(l.price for l in kept if l.graded)
    raw_low, raw_median = _bucket_stats(raw)
    graded_low, graded_median = _bucket_stats(graded)
    return CompsSummary(
        source=source,
        raw_count=len(raw),
        raw_low=raw_low,
        raw_median=raw_median,
        graded_count=len(graded),
        graded_low=graded_low,
        graded_median=graded_median,
    )


# Minimum same-company/same-grade comps before the slab path trusts them as a
# dedicated bucket (below this, mixed-grade or not_enough_data fallbacks apply).
MIN_MATCHING_COMPS = 3


def _norm_grade(grade: str) -> str:
    """Normalize a grade string for equality: '9.0' == '9', but '9' != '9.5'."""
    g = grade.strip().upper()
    return g[:-2] if g.endswith(".0") else g


def matching_grade_summary(listings: list[CompListing], company: str, grade: str,
                           source: str, query: str = "") -> CompsSummary | None:
    """Summary of comps slabbed by the SAME company at the SAME grade.

    A PSA 9 is priced by other PSA 9s — a PSA 10 or BGS 9.5 of the same card
    trades in a different market. The matching listings land in the graded
    bucket (raw fields empty: this summary prices the slab only), reusing the
    same trimmed-floor machinery as summarize(). Returns None when fewer than
    MIN_MATCHING_COMPS listings match, so callers can fall back.
    """
    want_company = company.strip().upper()
    want_grade = _norm_grade(grade)
    prices = []
    for l in listings:
        if is_junk(l.title, query=query):
            continue
        m = _GRADED_RE.search(l.title)
        if (m and m.group(1).upper() == want_company
                and _norm_grade(m.group(2)) == want_grade):
            prices.append(l.price)
    if len(prices) < MIN_MATCHING_COMPS:
        return None
    prices.sort()
    graded_low, graded_median = _bucket_stats(prices)
    return CompsSummary(
        source=source,
        raw_count=0,
        raw_low=None,
        raw_median=None,
        graded_count=len(prices),
        graded_low=graded_low,
        graded_median=graded_median,
    )
