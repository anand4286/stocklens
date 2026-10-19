import math

import pytest

from stocklens.data.base import Fundamentals
from stocklens.data.demo import DemoFundamentals, DemoPrices
from stocklens.research import forensics, report

PRIOR = {
    "revenue": 900.0, "gross_profit": 360.0, "operating_income": 120.0, "net_income": 70.0,
    "operating_cash_flow": 90.0, "total_assets": 1000.0, "total_liabilities": 500.0,
    "current_assets": 300.0, "current_liabilities": 200.0, "long_term_debt": 250.0,
    "shares_diluted": 100.0, "receivables": 90.0, "inventory": 80.0, "ppe_net": 400.0,
    "depreciation": 40.0, "sga": 180.0,
}


def strong(**over) -> Fundamentals:
    """Better than last year on all nine of Piotroski's measures."""
    base = dict(
        company="Strong Co", fiscal_year=2025, revenue=1000.0, revenue_prior=900.0,
        gross_profit=420.0, operating_income=150.0, net_income=100.0,
        operating_cash_flow=130.0, capex=30.0, total_assets=1050.0, total_liabilities=480.0,
        current_assets=360.0, current_liabilities=200.0, long_term_debt=200.0, cash=100.0,
        equity=570.0, shares_outstanding=98.0, shares_diluted=98.0, receivables=95.0,
        inventory=85.0, ppe_net=420.0, depreciation=44.0, sga=190.0, interest_expense=10.0,
        income_tax=25.0, pretax_income=125.0, stock_compensation=10.0,
        retained_earnings=300.0, prior=dict(PRIOR),
    )
    base.update(over)
    return Fundamentals(**base)


def test_piotroski_scores_a_uniformly_improving_company_nine():
    out = forensics.piotroski(strong())
    assert out["score"] == 9 and out["max_known"] == 9
    assert all(t["passed"] is True for t in out["tests"])
    assert all({"name", "rule", "measured", "passed"} <= t.keys() for t in out["tests"])


def test_piotroski_unknown_is_not_fail_and_withholds_below_seven():
    out = forensics.piotroski(strong(prior={}))
    # Without a prior year only the cash-flow-positive test can run.
    assert out["max_known"] == 1 and out["score"] is None
    assert "withheld" in out["reading"]
    assert sum(t["passed"] is None for t in out["tests"]) == 8


def test_piotroski_counts_failures():
    out = forensics.piotroski(strong(net_income=-10.0, shares_diluted=110.0))
    by = {t["name"]: t["passed"] for t in out["tests"]}
    assert by["ROA positive"] is False and by["No new shares issued"] is False
    assert out["score"] == out["max_known"] - 3  # ROA, ROA change, new shares


def test_altman_z_double_prime_matches_the_published_formula():
    f = strong()
    out = forensics.altman(f, price=None)
    wc, ta = 360 - 200, 1050
    expected = 6.56 * wc / ta + 3.26 * 300 / ta + 6.72 * 150 / ta + 1.05 * 570 / 480
    assert out["variant"] == "Z''" and out["z"] == round(expected, 2)
    assert out["zone"] == "safe" and out["thresholds"] == forensics.ALTMAN_Z2_ZONES
    assert out["original"]["z"] is None  # no price, so no market equity


def test_altman_reports_the_original_z_when_a_price_is_given():
    out = forensics.altman(strong(), price=10.0)
    ta = 1050
    z = 1.2 * 160 / ta + 1.4 * 300 / ta + 3.3 * 150 / ta + 0.6 * 980 / 480 + 1000 / ta
    assert out["original"]["z"] == round(z, 2)


def test_altman_withholds_without_retained_earnings():
    out = forensics.altman(strong(retained_earnings=None), price=None)
    assert out["z"] is None and out["zone"] is None and "retained earnings" in out["reading"]


def test_beneish_eight_variable_and_fallbacks():
    full = forensics.beneish(strong())
    assert full["model"] == "8-variable" and full["threshold"] == -1.78
    c = full["components"]
    m = (-4.84 + 0.920 * c["DSRI"] + 0.528 * c["GMI"] + 0.404 * c["AQI"] + 0.892 * c["SGI"]
         + 0.115 * c["DEPI"] - 0.172 * c["SGAI"] + 4.679 * c["TATA"] - 0.327 * c["LVGI"])
    assert full["m"] == pytest.approx(m, abs=0.01)
    assert full["flagged"] is (full["m"] > -1.78)

    five = forensics.beneish(strong(sga=None))
    assert five["model"] == "5-variable" and five["threshold"] == -2.22
    assert "SGAI" in five["reading"]

    none = forensics.beneish(strong(receivables=None))
    assert none["m"] is None and none["model"] is None and none["flagged"] is None


def test_sloan_accruals_ratio_uses_average_assets():
    out = forensics.accruals(strong())
    assert out["sloan_ratio"] == round((100 - 130) / ((1050 + 1000) / 2), 4)
    assert out["raised"] is False


def test_severity_grows_with_the_breach_and_saturates():
    assert forensics.severity_of(0) == 0.25
    assert forensics.severity_of(0.5) < forensics.severity_of(0.9) < forensics.severity_of(1)
    assert forensics.severity_of(5) == 1.0
    assert forensics.severity_of(None) == 0.25


def test_red_flags_distinguish_raised_passed_and_unknown():
    flags = {f["name"]: f for f in forensics.red_flags(strong(
        operating_cash_flow=40.0, retained_earnings=-5.0, income_tax=None))}
    conv = flags["Cash conversion"]
    assert conv["raised"] is True and conv["measured"] == 0.4 and conv["threshold"] == 0.8
    assert conv["severity"] == forensics.severity_of(0.5)
    assert flags["Accumulated deficit"]["raised"] is True
    assert flags["Tax rate anomaly"]["raised"] is None
    assert flags["Tax rate anomaly"]["severity"] is None
    assert flags["Dilution"]["raised"] is False and flags["Dilution"]["severity"] == 0.0


def test_receivables_outpacing_revenue_is_raised():
    flags = {f["name"]: f for f in forensics.red_flags(strong(receivables=150.0))}
    rec = flags["Receivables outpacing revenue"]
    assert rec["raised"] is True and rec["measured"] == round((150 / 90) / (1000 / 900), 4)


def _walk(obj):
    if isinstance(obj, float):
        assert math.isfinite(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk(v)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _walk(v)


def test_report_contract_and_derived_debate():
    out = report.build(strong(), price=12.0)
    for key in ("company", "fiscal_year", "piotroski", "altman", "beneish", "accruals",
                "red_flags", "valuation", "debate", "checklist"):
        assert key in out
    assert out["piotroski"]["score"] == 9
    assert any("Piotroski" in line for line in out["debate"]["bull"])
    assert all(row["answer"] in ("yes", "no", "unknown") for row in out["checklist"])
    _walk(out)


def test_report_of_nothing_is_nothing():
    assert report.build(None, 10.0) is None


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "KO", "ZZZ", "BRK.B", "TSLA", "GME"])
def test_demo_reports_are_finite_and_complete(symbol):
    f = DemoFundamentals().fundamentals(symbol)
    price = DemoPrices().daily_bars(symbol, 300)[-1].close
    out = report.build(f, price)
    _walk(out)
    assert out["piotroski"]["max_known"] == 9  # demo files every input


def test_demo_tickers_do_not_all_score_alike():
    outs = [report.build(DemoFundamentals().fundamentals(s), 100.0)
            for s in ("AAPL", "MSFT", "KO", "ZZZ", "BRK.B", "TSLA", "NVDA", "F", "XOM", "GME")]
    assert len({o["altman"]["zone"] for o in outs}) == 3
    assert len({o["piotroski"]["score"] for o in outs}) >= 3
    assert {o["beneish"]["flagged"] for o in outs} == {True, False}
