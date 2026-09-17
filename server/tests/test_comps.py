from app.comps import matching_grade_summary, summarize, is_graded, is_junk
from app.schemas import CompListing

def L(title, price):
    return CompListing(title=title, price=price, graded=is_graded(title))

def test_is_graded_detects_companies():
    assert is_graded("2018 Prizm Luka Doncic PSA 10")
    assert is_graded("Luka RC BGS 9.5 GEM")
    assert is_graded("Luka Doncic Prizm TAG 9")
    assert not is_graded("2018 Prizm Luka Doncic RC #280")

def test_is_graded_allows_condition_words_between_company_and_grade():
    assert is_graded("PSA GEM MINT 10")
    assert is_graded("SGC Mint 9")
    assert is_graded("BGS Gem Mint 9.5")

def test_is_graded_rejects_arbitrary_words_between_company_and_number():
    assert not is_graded("Vintage card lot of 10")
    assert not is_graded("Vintage PSA card lot of 10")

def test_is_graded_hopeful_titles_go_to_graded_bucket():
    # a "PSA GEM MINT 10?" hopeful raw listing landing in the graded bucket
    # is the benign direction (keeps slab-ish prices out of raw_median)
    assert is_graded("PSA GEM MINT 10?")

def test_summarize_buckets_and_medians():
    listings = [L("Luka raw", 40), L("Luka raw RC", 60), L("Luka RC nice", 80),
                L("Luka PSA 10", 400), L("Luka PSA 9", 150)]
    s = summarize(listings, source="active_listings")
    assert (s.raw_count, s.raw_low, s.raw_median) == (3, 40, 60)
    assert (s.graded_count, s.graded_low) == (2, 150)

def test_summarize_empty():
    s = summarize([], source="active_listings")
    assert s.raw_count == 0 and s.raw_low is None

# --- junk filter -------------------------------------------------------------

def test_is_junk_matches_obvious_junk():
    assert is_junk("Luka Doncic Prizm RC REPRINT")
    assert is_junk("Luka Doncic rookie RP mint")
    assert is_junk("Luka Doncic custom art card")
    assert is_junk("Luka Doncic novelty card")
    assert is_junk("Luka Doncic proxy")
    assert is_junk("Luka Doncic replica rookie")
    assert is_junk("Luka Doncic facsimile auto")
    assert is_junk("Luka Doncic ACEO rookie")
    assert is_junk("Luka Doncic digital card")
    assert is_junk("Luka Doncic lot of 10")
    assert is_junk("Luka Doncic rookie (lot)")
    assert is_junk("2018 Prizm basketball box BREAK Luka")
    assert is_junk("Luka Doncic you pick")
    assert is_junk("Luka Doncic u pick your card")
    assert is_junk("Luka Doncic choose your card")
    assert is_junk("Luka Doncic pick your player")
    assert is_junk("Luka Doncic rookie damaged corner")
    assert is_junk("Luka Doncic sticker card")
    assert is_junk("Luka Doncic decal")

def test_is_junk_word_boundary_safety():
    # "Lottery" must not trip \blot\b, "Sharp"-style substrings must not
    # trip \brp\b, and clean titles stay clean.
    assert not is_junk("Zach LaVine Lottery Pick insert")
    assert not is_junk("2018 Prizm Luka Doncic RC #280")
    assert not is_junk("Luka Doncic Prizm Rookie Sharp Corners")
    assert not is_junk("Luka Doncic Charlotte Hornets")   # "rlot" is not \blot\b

def test_is_junk_plural_forms():
    assert is_junk("Luka Doncic REPRINTS x3")
    assert is_junk("Luka Doncic replicas rookie")
    assert is_junk("Luka Doncic proxies")
    assert is_junk("Luka Doncic facsimiles auto")
    assert is_junk("Luka Doncic decals set")
    assert is_junk("Luka Doncic novelties")
    # "customs card" does NOT match \bcustom\b — accepted collateral of
    # keeping `custom` singular (see comment on _JUNK_PATTERNS).
    assert not is_junk("Luka Doncic customs card")

def test_summarize_drops_junk_from_both_buckets():
    listings = [L("Luka raw", 40), L("Luka raw RC", 60), L("Luka RC nice", 80),
                L("Luka Doncic REPRINT", 2.99),           # junk raw
                L("Luka Doncic lot of 10 PSA 9", 25),     # junk graded
                L("Luka PSA 10", 400), L("Luka PSA 9", 150)]
    s = summarize(listings, source="active_listings")
    assert (s.raw_count, s.raw_low, s.raw_median) == (3, 40, 60)
    assert (s.graded_count, s.graded_low) == (2, 150)

def test_is_junk_exempts_tokens_present_in_query():
    # A junk token the vision model put in the search string is part of the
    # card's NAME — "Greninja BREAK", "Digital Bug" — not a junk signal.
    assert not is_junk("Greninja BREAK Holo #41",
                       query="XY BREAKpoint Greninja BREAK #41")
    assert not is_junk("Digital Bug Cocoon LED3",
                       query="Yu-Gi-Oh Digital Bug Cocoon LED3-EN020")
    # A token absent from the query still junks — ordinary scans keep filtering.
    assert is_junk("Luka Doncic case break", query="2018 Prizm Luka Doncic")
    # Exemption is per-token: an exempt name token never launders other junk.
    assert is_junk("Greninja BREAK lot of 10",
                   query="XY BREAKpoint Greninja BREAK #41")

def test_summarize_passes_query_to_junk_filter():
    listings = [L("Greninja BREAK Holo #41", 20), L("Greninja BREAK NM", 30),
                L("Greninja BREAK XY", 40)]
    assert summarize(listings, source="active_listings").raw_count == 0
    s = summarize(listings, source="active_listings",
                  query="XY BREAKpoint Greninja BREAK #41")
    assert s.raw_count == 3 and s.raw_low == 20

def test_matching_grade_summary_passes_query_to_junk_filter():
    listings = [L(f"Greninja BREAK PSA 10 #{i}", 100 + i) for i in range(3)]
    assert matching_grade_summary(listings, "PSA", "10",
                                  source="active_listings") is None
    s = matching_grade_summary(listings, "PSA", "10", source="active_listings",
                               query="Greninja BREAK #41 PSA 10")
    assert s is not None and s.graded_count == 3

def test_summarize_keeps_lottery_title_raw():
    listings = [L("Zach LaVine Lottery Pick insert", 20),
                L("Zach LaVine base", 30), L("Zach LaVine RC", 40)]
    s = summarize(listings, source="active_listings")
    assert s.raw_count == 3 and s.raw_low == 20

# --- robust (trimmed) floor --------------------------------------------------

def test_raw_low_trims_outlier_with_ten_listings():
    # A $20 lowball (above the 30%-of-median flood guard, so the trim alone
    # must handle it) does not set the floor with 10 listings.
    prices = [20, 40, 42, 44, 46, 48, 50, 52, 54, 56]
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.raw_count == 10
    assert s.raw_low == 40  # 10th-percentile (nearest-rank) value, not 20

def test_raw_low_extreme_outlier_hits_guard_then_trim():
    # A $0.99 listing is first dropped by the 30%-of-median guard, THEN the
    # percentile trim applies to the 9 survivors — floor is 42, never 0.99.
    prices = [0.99, 40, 42, 44, 46, 48, 50, 52, 54, 56]
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.raw_low == 42

def test_raw_low_second_lowest_with_five_listings():
    prices = [20, 40, 45, 50, 55]   # 20 >= 0.3 * median(45): guard inactive
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.raw_low == 40

def test_raw_low_min_with_three_listings():
    prices = [30, 40, 50]
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.raw_low == 30

def test_raw_low_resists_cheap_junk_flood():
    # Five clean-titled $0.99 auctions (fresh listings) outnumber the trim:
    # the guard computes the floor from the >= 30%-of-median population (58),
    # but with half the bucket junk the median (27.995) straddles both modes,
    # so the inversion clamp caps the low at the median. Never $0.99 either way.
    prices = [0.99, 0.99, 0.99, 0.99, 0.99, 55, 58, 60, 62, 65]
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.raw_count == 10
    assert s.raw_low == s.raw_median  # guard floor clamped to the median
    assert s.raw_low > 0.99

def test_graded_low_resists_cheap_junk_flood():
    prices = [9.99, 9.99, 9.99, 9.99, 9.99, 150, 160, 170, 180, 190]
    s = summarize([L(f"Luka PSA 10 #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.graded_low == s.graded_median  # guard floor clamped to the median
    assert s.graded_low > 9.99

def test_raw_low_single_listing_falls_back_to_min():
    s = summarize([L("Luka raw", 45)], source="active_listings")
    assert s.raw_low == 45  # < 2 floor candidates -> whole bucket

def test_graded_low_uses_same_trimmed_floor():
    prices = [50, 150, 160, 170, 180]   # 50 >= 0.3 * median(160): guard inactive
    s = summarize([L(f"Luka PSA 10 #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.graded_count == 5
    assert s.graded_low == 150  # second-lowest for 4-7 listings

# --- matching-grade summary (slab mode) ---------------------------------------

def test_matching_grade_summary_filters_to_same_company_and_grade():
    listings = [L("Luka PSA 9 rookie", 100), L("Luka Doncic PSA 9", 110),
                L("2018 Prizm Luka PSA 9 #280", 120),
                L("Luka PSA 10 gem", 400),          # same company, other grade
                L("Luka BGS 9 rookie", 90),         # other company, same grade
                L("Luka raw RC #280", 50)]          # raw
    s = matching_grade_summary(listings, "PSA", "9", source="active_listings")
    assert s is not None
    assert s.graded_count == 3
    assert s.graded_median == 110
    # Raw side is intentionally empty: this summary prices the slab only.
    assert (s.raw_count, s.raw_low, s.raw_median) == (0, None, None)

def test_matching_grade_summary_is_case_insensitive_on_company():
    listings = [L("Luka psa 9", 100), L("Luka Psa 9", 110), L("Luka PSA 9", 120)]
    s = matching_grade_summary(listings, "psa", "9", source="active_listings")
    assert s is not None and s.graded_count == 3

def test_matching_grade_summary_grade_9_does_not_match_9_5():
    listings = [L(f"Luka BGS 9.5 #{i}", 200 + i) for i in range(3)]
    assert matching_grade_summary(listings, "BGS", "9",
                                  source="active_listings") is None
    s = matching_grade_summary(listings, "BGS", "9.5", source="active_listings")
    assert s is not None and s.graded_count == 3

def test_matching_grade_summary_under_three_matches_is_none():
    listings = [L("Luka PSA 9", 100), L("Luka PSA 9 rookie", 110),
                L("Luka PSA 10", 400)]
    assert matching_grade_summary(listings, "PSA", "9",
                                  source="active_listings") is None

def test_matching_grade_summary_drops_junk():
    listings = [L("Luka PSA 9", 100), L("Luka PSA 9 rookie", 110),
                L("Luka PSA 9 lot of 5", 30)]      # junk never prices a slab
    assert matching_grade_summary(listings, "PSA", "9",
                                  source="active_listings") is None

def test_matching_grade_summary_uses_trimmed_floor():
    # Same robust-low machinery as summarize: second-lowest for 4-7 listings.
    prices = [50, 150, 160, 170, 180]   # 50 >= 0.3 * median(160): guard inactive
    listings = [L(f"Luka PSA 10 #{i}", p) for i, p in enumerate(prices)]
    s = matching_grade_summary(listings, "PSA", "10", source="active_listings")
    assert s.graded_count == 5
    assert s.graded_low == 150 and s.graded_median == 160

def test_matching_grade_summary_normalizes_grade_strings():
    # A vision read of "9.0" must match "PSA 9" titles.
    listings = [L(f"Luka PSA 9 #{i}", 100 + i) for i in range(3)]
    s = matching_grade_summary(listings, "PSA", "9.0", source="active_listings")
    assert s is not None and s.graded_count == 3

# --- bimodal buckets must not invert the range --------------------------------

def test_bimodal_bucket_never_inverts_raw_range():
    # Half junk-cheap, half real: the 30%-of-median flood guard pushes the
    # floor into the expensive mode (50) while the median straddles both
    # (25.5). The low must be clamped to the median, never above it.
    prices = [1, 1, 1, 50, 60, 70]
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.raw_median == 25.5
    assert s.raw_low is not None and s.raw_low <= s.raw_median

def test_bimodal_bucket_never_inverts_graded_range():
    prices = [1, 1, 1, 50, 60, 70]
    s = summarize([L(f"Luka PSA 10 #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    assert s.graded_low is not None and s.graded_low <= s.graded_median

def test_bimodal_bucket_prices_sanely_through_decide():
    from app.schemas import Condition
    from app.verdict import decide

    prices = [1, 1, 1, 50, 60, 70]
    s = summarize([L(f"Luka raw #{i}", p) for i, p in enumerate(prices)],
                  source="active_listings")
    v = decide(s, Condition(observations=[], grade_low=6, grade_high=8), None, 0.9)
    assert v.value_low is not None and v.value_high is not None
    assert v.value_low <= v.value_high
    assert v.verdict == "no_ask"  # a sane label, not a contradictory call
