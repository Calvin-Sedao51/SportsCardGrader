"""Build the [comment, comment_options] op pair for a Binder card post.

The body is human-readable markdown so the community stays browsable on
Snapie/PeakD; the machine-readable record lives in json_metadata.card.
Payout is declined: a central account auto-posting user content and pocketing
rewards reads as reward farming and invites downvotes.
"""
import json

from app.hive.record import CardRecord, build_tags

APP_ID = "cardscanner/1.0"
METADATA_BUDGET = 8192


class MetadataTooLarge(Exception):
    pass


def _title(record: CardRecord) -> str:
    identity = record.identity
    parts = [identity.subject, "—", identity.year, identity.set_name]
    if identity.card_number:
        parts.append(f"#{identity.card_number}")
    if record.slab:
        parts.append(f"({record.slab.company} {record.slab.grade})")
    elif record.condition:
        parts.append(f"(Grade {record.condition.grade_low:g}–{record.condition.grade_high:g})")
    return " ".join(parts)[:255]


def _comps_line(record: CardRecord) -> str:
    if not record.comps:
        return "_No market comps available at scan time._"
    s = record.comps.summary
    source = "eBay active listings" if s.source == "active_listings" else "sold sales"
    bits = []
    if s.raw_count:
        median = f", median ${s.raw_median:g}" if s.raw_median is not None else ""
        bits.append(f"{s.raw_count} raw comps{median}")
    if s.graded_count:
        median = f", median ${s.graded_median:g}" if s.graded_median is not None else ""
        bits.append(f"{s.graded_count} graded{median}")
    detail = " · ".join(bits) or "no comparable listings"
    return f"{detail} — {source}, {record.comps.as_of[:10]}"


def _body(record: CardRecord) -> str:
    identity = record.identity
    lines = [f"![front]({record.images.front})", ""]
    if record.images.back:
        lines += [f"![back]({record.images.back})", ""]
    lines += ["| | |", "|---|---|",
              f"| Player | {identity.subject} |",
              f"| Year | {identity.year} |",
              f"| Set | {identity.set_name} |"]
    if identity.card_number:
        lines.append(f"| Card # | {identity.card_number} |")
    if identity.variant:
        lines.append(f"| Variant | {identity.variant} |")
    if record.slab:
        lines.append(f"| Slab | {record.slab.company} {record.slab.grade} |")
    elif record.condition:
        lines.append(f"| Est. grade | {record.condition.grade_low:g}–"
                     f"{record.condition.grade_high:g} |")
    if record.authenticity:
        lines.append(f"| Authenticity risk | {record.authenticity.risk} |")
    if record.verdict and record.verdict.value_low is not None:
        lines.append(f"| Value | ${record.verdict.value_low:g}–"
                     f"${record.verdict.value_high:g} |")
    if record.asking_price is not None:
        lines.append(f"| Asking | ${record.asking_price:g} |")
    lines += ["", _comps_line(record), "",
              "*Scanned with [Card Scanner](https://github.com/Calvin-Sedao51/SportsCardGrader)"
              " — full record data in this post's json_metadata.*"]
    return "\n".join(lines)


def build_post(record: CardRecord, *, community: str, account: str,
               permlink: str) -> list[tuple[str, dict]]:
    metadata = {
        "app": APP_ID,
        "format": "markdown",
        "tags": build_tags(record.identity, community),
        "image": [url for url in (record.images.front, record.images.back) if url],
        "description": _title(record),
        "card": json.loads(record.model_dump_json()),  # via JSON to drop non-serializable types
    }
    metadata_json = json.dumps(metadata, separators=(",", ":"))
    if len(metadata_json) >= METADATA_BUDGET:
        raise MetadataTooLarge(f"json_metadata is {len(metadata_json)} bytes")
    comment = {
        "parent_author": "",
        "parent_permlink": community,
        "author": account,
        "permlink": permlink,
        "title": _title(record),
        "body": _body(record),
        "json_metadata": metadata_json,
    }
    options = {
        "author": account,
        "permlink": permlink,
        "max_accepted_payout": "0.000 HBD",  # declined
        "percent_hbd": 10000,
        "allow_votes": True,
        "allow_curation_rewards": True,
        "extensions": [],
    }
    return [("comment", comment), ("comment_options", options)]
