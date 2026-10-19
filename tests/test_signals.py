from datetime import UTC, date, datetime, timedelta

import pytest

from stocklens.analytics import signals
from stocklens.analytics.composite import CAUTIOUS_AT, CONSTRUCTIVE_AT, blend
from stocklens.analytics.signals import Signal
from stocklens.data.base import Bar, Fundamentals, NewsItem
from stocklens.data.demo import DemoFundamentals, DemoPrices


def bars_from(closes):
    start = date(2025, 1, 1)
    return [Bar(start + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e6)
            for i, c in enumerate(closes)]


def test_every_family_abstains_rather_than_inventing_a_score():
    short = bars_from([10.0] * 10)
    assert signals.technical(short).score is None
    assert signals.momentum(short).score is None
    assert signals.volume(short).score is None
    assert signals.fundamental(None)[0].score is None
    assert signals.valuation(None, 10).score is None
    assert signals.sentiment([]).score is None
    assert signals.news_flow([]).score is None


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "KO", "ZZZ", "BRK.B"])
def test_every_score_is_inside_the_contract(symbol):
    bars = DemoPrices().daily_bars(symbol, 400)
    bench = DemoPrices().daily_bars("SPY", 400)
    f = DemoFundamentals().fundamentals(symbol)
    for sig in (signals.technical(bars), signals.momentum(bars),
                signals.relative_strength(bars, bench), signals.volume(bars),
                signals.fundamental(f)[0], signals.valuation(f, bars[-1].close)):
        assert sig.score is None or -1 <= sig.score <= 1, sig


def test_clamp_turns_non_numbers_into_no_opinion():
    assert signals.clamp(float("nan")) == 0.0
    assert signals.clamp(float("inf")) == 0.0
    assert signals.clamp(5) == 1.0


def test_a_missing_family_is_not_scored_as_neutral():
    strong = Signal("momentum", "directional", 0.8, "")
    missing = Signal("fundamental", "directional", None, "")
    with_missing = blend([strong, missing])
    neutral = blend([strong, Signal("fundamental", "directional", 0.0, "")])
    # Missing leaves the score alone and costs confidence...
    assert with_missing.score == pytest.approx(0.8)
    assert with_missing.coverage < 1
    # ...whereas a real neutral reading dilutes the score.
    assert neutral.score < with_missing.score


def test_contributions_sum_to_the_composite():
    sigs = [Signal(n, "directional", s, "") for n, s in
            [("technical", 0.4), ("momentum", -0.2), ("fundamental", 0.9), ("sentiment", None)]]
    sigs.append(Signal("volume", "conviction", 1.0, ""))
    comp = blend(sigs)
    total = sum(c["contribution"] for c in comp.contributions)
    assert total == pytest.approx(comp.score, abs=1e-3)
    assert next(c for c in comp.contributions if c["name"] == "volume")["contribution"] == 0


def test_stance_follows_the_published_thresholds():
    def stance(score):
        return blend([Signal(n, "directional", score, "") for n in
                      ("technical", "momentum", "relative_strength", "fundamental",
                       "valuation", "sentiment")]).stance

    assert stance(CONSTRUCTIVE_AT) == "Constructive"
    assert stance(CONSTRUCTIVE_AT - 0.01) == "Neutral"
    assert stance(CAUTIOUS_AT) == "Cautious"


def test_thin_evidence_yields_no_stance():
    comp = blend([Signal("technical", "directional", 1.0, "")])
    assert comp.stance == "Insufficient evidence"


def test_scorecard_marks_unreported_items_unknown_not_failed():
    f = Fundamentals(company="X", fiscal_year=2025, net_income=10.0, revenue=100.0)
    checks = {c.name: c for c in signals.scorecard(f)}
    assert checks["profitable"].passed is True
    assert checks["current_ratio"].passed is None
    assert signals.fundamental(f)[0].score is None  # fewer than 4 answered


def test_sentiment_weights_recent_headlines_more():
    now = datetime(2026, 1, 10, tzinfo=UTC)
    old_bad = [NewsItem(now - timedelta(days=20), "Company misses and cuts outlook", "x")] * 3
    new_good = [NewsItem(now, "Company beats estimates and raises guidance", "x")] * 3
    assert signals.sentiment(old_bad + new_good, now).score > 0.9


def test_headline_tone_ignores_neutral_text():
    assert signals.headline_tone("Company schedules earnings call") is None
    assert signals.headline_tone("Shares plunge after recall") == -1
