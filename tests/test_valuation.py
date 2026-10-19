import pytest

from stocklens.data.base import Fundamentals
from stocklens.research import valuation


def company(**over) -> Fundamentals:
    base = dict(
        company="Cash Co", fiscal_year=2025, revenue=1000.0, revenue_prior=900.0,
        operating_income=200.0, net_income=150.0, operating_cash_flow=180.0, capex=40.0,
        long_term_debt=300.0, cash=100.0, equity=800.0, shares_outstanding=100.0,
        history={"revenue": [("2021-12-31", 800.0), ("2023-12-31", 900.0),
                             ("2025-12-31", 1000.0)]},
    )
    base.update(over)
    return Fundamentals(**base)


def test_multiples_are_exact_and_forward_pe_is_withheld():
    mult, withheld = valuation.multiples(company(), price=30.0)
    cap, ev = 3000.0, 3000.0 + 300 - 100
    assert mult["market_cap"] == cap and mult["enterprise_value"] == ev
    assert mult["pe"] == 20.0 and mult["ps"] == 3.0 and mult["pb"] == 3.75
    assert mult["ev_ebit"] == round(ev / 200, 2) and mult["ev_sales"] == round(ev / 1000, 2)
    assert mult["fcf_yield"] == round(140 / cap, 4)
    assert mult["earnings_yield"] == 0.05
    assert mult["forward_pe"] is None and "consensus" in withheld["forward_pe"]


def test_loss_makers_get_a_reason_not_a_negative_multiple():
    mult, withheld = valuation.multiples(company(net_income=-5.0), price=30.0)
    assert mult["pe"] is None and "positive net income" in withheld["pe"]
    mult, withheld = valuation.multiples(company(), price=None)
    assert mult["pe"] is None and "price" in withheld["pe"]


def test_cagr_uses_calendar_time():
    assert valuation.cagr([("2021-12-31", 100.0), ("2025-12-31", 200.0)]) == pytest.approx(
        2 ** (1 / 4) - 1, abs=1e-3)
    assert valuation.cagr([("2025-12-31", 1.0)]) is None
    assert valuation.cagr([("2021-12-31", -1.0), ("2025-12-31", 2.0)]) is None


def test_cases_are_ordered_and_anchored_to_history():
    cases = valuation.cases(company())
    g = {c["name"]: c["growth"] for c in cases}
    assert g["bear"] < g["base"] < g["bull"]
    assert "CAGR" in cases[0]["reasoning"]
    shrinking = valuation.cases(company(history={"revenue": [("2021-12-31", 1000.0),
                                                             ("2025-12-31", 800.0)]}))
    s = {c["name"]: c["growth"] for c in shrinking}
    assert s["bear"] < s["base"] < s["bull"] == 0.0


def test_dcf_grid_moves_the_right_way():
    dcf, why = valuation.dcf(company(), price=30.0, growth=0.05)
    assert why is None
    assert dcf["base_fcf_per_share"] == 1.4
    assert len(dcf["grid"]) == len(dcf["discount_rates"]) == 6
    assert all(len(row) == len(dcf["terminal_growth"]) == 6 for row in dcf["grid"])
    column = [row[0] for row in dcf["grid"]]
    assert column == sorted(column, reverse=True)  # higher discount, lower value
    assert dcf["grid"][0] == sorted(dcf["grid"][0])  # higher terminal, higher value
    assert 0.5 < dcf["terminal_share"] < 0.95


def test_dcf_is_withheld_on_negative_free_cash_flow():
    dcf, why = valuation.dcf(company(capex=500.0), price=30.0, growth=0.05)
    assert dcf is None and "not positive" in why
    out = valuation.build(company(capex=500.0), 30.0)
    assert out["dcf"] is None and out["reverse_dcf"] is None and "dcf" in out["withheld"]


def test_reverse_dcf_recovers_the_growth_that_produced_the_price():
    per_share = 1.4
    price = valuation._value_at_constant_growth(per_share, 0.07)
    assert valuation.implied_growth(per_share, price) == pytest.approx(0.07, abs=1e-5)


def test_reverse_dcf_reports_history_beside_the_implied_rate():
    rev = valuation.reverse_dcf(company(), price=60.0)
    assert rev["discount"] == 0.09 and rev["terminal"] == 0.025
    assert rev["historical_growth"] == pytest.approx(0.0574, abs=1e-3)
    assert rev["implied_growth"] > rev["historical_growth"]
    assert "more than the company has delivered" in rev["reading"]


def test_reverse_dcf_needs_a_price():
    rev = valuation.reverse_dcf(company(), price=None)
    assert rev["implied_growth"] is None and "price" in rev["reading"]


def test_build_contract():
    out = valuation.build(company(), 30.0)
    assert set(out) >= {"multiples", "withheld", "cases", "dcf", "reverse_dcf"}
    assert all({"name", "growth", "discount", "terminal"} <= c.keys() for c in out["cases"])
    assert out["dcf"]["price"] == 30.0
