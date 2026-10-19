"""The forecast ledger: scoring rules, point-in-time discipline, write-once resolution."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from stocklens.data.base import Bar
from stocklens.data.demo import DemoPrices
from stocklens.db import Database
from stocklens.forecast import HORIZON, MODEL_VERSION, make_forecast
from stocklens.forecast.backfill import symbol_record, track_record
from stocklens.forecast.ledger import resolve_due
from stocklens.forecast.model import add_business_days, probability_up
from stocklens.forecast.scoring import brier, brier_skill, log_loss, score


def _rows(pairs, start=date(2025, 1, 6)):
    """(p_up, outcome_up) pairs, one per decision day."""
    return [{"p_up": p, "outcome_up": y, "made_on": (start + timedelta(days=i)).isoformat()}
            for i, (p, y) in enumerate(pairs)]


@pytest.fixture(scope="module")
def demo():
    prices = DemoPrices(today=date(2026, 9, 30))
    return prices, prices.daily_bars("SPY", 600)


# -- scoring rules -------------------------------------------------------------


def test_brier_perfect_and_coin():
    assert brier(_rows([(1.0, True), (0.0, False)])) == 0.0
    assert brier(_rows([(0.5, True), (0.5, False), (0.5, True)])) == pytest.approx(0.25)
    assert log_loss(_rows([(0.5, True), (0.5, False)])) == pytest.approx(0.6931, abs=1e-4)


def test_climatology_has_zero_skill():
    outcomes = [True] * 30 + [False] * 20
    base = 30 / 50
    rows = _rows([(base, y) for y in outcomes])
    assert brier_skill(rows) == pytest.approx(0.0, abs=1e-12)
    assert brier_skill(_rows([(0.9 if y else 0.1, y) for y in outcomes])) > 0.5


def test_score_shape_reliability_and_intervals():
    pairs = [(0.40 + 0.01 * (i % 30), i % 3 != 0) for i in range(120)]
    s = score(_rows(pairs))
    assert s["resolved"] == 120
    assert s["decision_days"] == 120
    assert sum(b["n"] for b in s["reliability"]) == 120
    for b in s["reliability"]:
        if b["n"]:
            assert b["wilson_95"]["method"] == "Wilson score"
    eff = s["hit_rate"]["effective"]
    assert eff["wilson_95"]["samples"] == 120
    assert eff["hoeffding_95"]["method"] == "Hoeffding"
    assert {sl["label"] for sl in s["slices"]["confidence"]} == {"low", "medium", "high"}
    assert s["verdict"]["label"] in {"skill", "no_skill", "worse_than_climatology"}


def test_effective_sample_counts_decision_days_not_rows():
    rows = [{"p_up": 0.6, "outcome_up": True, "made_on": "2025-03-03"} for _ in range(40)]
    s = score(rows)
    assert s["resolved"] == 40
    assert s["decision_days"] == 1
    assert s["hit_rate"]["effective"]["samples"] == 1
    assert s["verdict"]["label"] == "insufficient"


def test_small_slices_are_flagged_not_hidden():
    rows = _rows([(0.6, True)] * 5) + [
        {**r, "regime": "trending"} for r in _rows([(0.4, False)] * 40, date(2025, 6, 2))]
    s = score(rows)
    regimes = {sl["label"]: sl for sl in s["slices"]["regime"]}
    assert regimes["unknown"]["n"] == 5 and regimes["unknown"]["reliable"] is False
    assert regimes["trending"]["reliable"] is True


def test_unresolved_rows_are_ignored():
    rows = _rows([(0.6, True), (0.4, False)]) + [
        {"p_up": 0.6, "outcome_up": None, "made_on": "2025-02-01"}]
    assert score(rows)["resolved"] == 2


# -- the model -----------------------------------------------------------------


def test_probability_map_is_shrunk():
    assert probability_up(0.0) == 0.5
    assert 0.32 < probability_up(-1.0) < 0.35
    assert 0.65 < probability_up(1.0) < 0.68


def test_make_forecast_shape(demo):
    prices, bench = demo
    bars = prices.daily_bars("AAPL", 600)
    f = make_forecast(bars, bench)
    assert f is not None
    assert f["horizon"] == HORIZON and f["model_version"] == MODEL_VERSION
    assert f["made_on"] == bars[-1].day.isoformat()
    assert f["resolves_on_or_after"] == add_business_days(bars[-1].day, HORIZON).isoformat()
    assert f["direction"] == ("up" if f["p_up"] >= 0.5 else "down")
    assert f["range_low"] < f["price"] < f["range_high"]
    assert set(f["features"]) == {"technical", "momentum", "relative_strength"}
    assert isinstance(f["regime"], str)
    assert make_forecast([], bench) is None


def test_forecast_uses_only_bars_up_to_decision(demo):
    prices, bench = demo
    bars = prices.daily_bars("MSFT", 600)
    t = 400
    before = make_forecast(bars[: t + 1], bench[: t + 1])
    mutated = bars[: t + 1] + [replace(b, close=b.close * 3, high=b.high * 3)
                               for b in bars[t + 1:]]
    after = make_forecast(mutated[: t + 1], bench[: t + 1])
    assert before == after


def test_backfill_is_point_in_time(demo):
    prices, bench = demo
    bars = prices.daily_bars("NVDA", 600)
    # Shock every bar after the cut. Forecasts whose outcome lies before it are unchanged.
    cut = 500
    shocked = bars[:cut] + [replace(b, close=b.close * 0.5) for b in bars[cut:]]
    a = symbol_record(bars, bench, symbol="NVDA")["forecasts"]
    b = symbol_record(shocked, bench, symbol="NVDA")["forecasts"]
    last_safe = bars[cut - 1].day.isoformat()
    safe_a = [f for f in a if f["resolved_on"] <= last_safe]
    safe_b = [f for f in b if f["resolved_on"] <= last_safe]
    assert safe_a and safe_a == safe_b
    # Appending future bars only adds forecasts; it never edits earlier ones.
    shorter = symbol_record(bars[:550], bench[:550], symbol="NVDA")["forecasts"]
    assert a[: len(shorter)] == shorter


def test_demo_random_walk_shows_no_skill():
    # The evaluation harness's default basket. Demo prices are a random walk with
    # a per-symbol drift, so the honest answer is "no skill beyond the base rate".
    prices = DemoPrices(today=date(2026, 9, 30))
    symbols = ("AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "KO", "PFE", "CAT", "WMT")
    panel = {s: prices.daily_bars(s, 750) for s in symbols}
    record = track_record(panel, prices.daily_bars("SPY", 750))
    assert record["kind"] == "simulated"
    assert "bars[:t+1]" in record["method"]
    s = record["scoring"]
    assert s["resolved"] > 100
    assert abs(s["brier_skill"]) < 0.05
    w = s["hit_rate"]["effective"]["wilson_95"]
    assert w["low"] < 0.5 < w["high"]
    assert 0.55 < s["range_coverage"]["rate"] < 0.80
    assert record["period"]["start"] <= record["period"]["end"]


# -- the live ledger -----------------------------------------------------------


def _bars(n, start=date(2025, 1, 6)):
    out, day, price = [], start, 100.0
    while len(out) < n:
        if day.weekday() < 5:
            price *= 1.001
            out.append(Bar(day, price, price, price, price, 1e6))
        day += timedelta(days=1)
    return out


def _db():
    db = Database("sqlite:///:memory:")
    db.create_all()
    return db


def _forecast(bars, i):
    return {"symbol": "TEST", "made_on": bars[i].day.isoformat(), "horizon": HORIZON,
            "model_version": MODEL_VERSION, "p_up": 0.55, "direction": "up", "score": 0.3,
            "price": bars[i].close, "range_low": bars[i].close * 0.9,
            "range_high": bars[i].close * 1.1, "features": {"technical": 0.3},
            "regime": "random"}


def test_one_forecast_per_symbol_per_session():
    db, bars = _db(), _bars(30)
    assert db.record_forecast(_forecast(bars, 5)) is True
    assert db.record_forecast({**_forecast(bars, 5), "p_up": 0.9}) is False
    assert db.record_forecast({**_forecast(bars, 5), "model_version": "price-v2"}) is True
    rows = db.forecasts(symbol="TEST")
    assert len(rows) == 2
    assert {r["p_up"] for r in rows} == {0.55}


def test_resolution_waits_for_horizon_and_is_write_once():
    db, bars = _db(), _bars(60)
    db.record_forecast(_forecast(bars, 10))
    assert resolve_due(db, "TEST", bars[: 10 + HORIZON]) == 0  # one bar short
    assert db.forecasts(resolved=False)
    assert resolve_due(db, "TEST", bars) == 1
    row = db.forecasts(symbol="TEST", resolved=True)[0]
    assert row["outcome_price"] == pytest.approx(bars[10 + HORIZON].close)
    assert row["outcome_up"] is True and row["in_range"] is True
    assert db.resolve_forecast(row["id"], 1.0, False, False) is False
    again = db.forecasts(symbol="TEST")[0]
    assert again["outcome_price"] == row["outcome_price"] and again["outcome_up"] is True
    assert resolve_due(db, "TEST", bars) == 0
    assert db.due_forecasts("TEST") == []
