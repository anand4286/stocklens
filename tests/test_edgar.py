import pytest

from stocklens.data.base import normalise_symbol
from stocklens.data.edgar import EdgarFundamentals, parse_company_facts


def fact(val, fy, end, start=None, form="10-K", fp="FY", filed=None):
    row = {"val": val, "fy": fy, "fp": fp, "form": form, "end": end, "filed": filed or end}
    if start:
        row["start"] = start
    return row


PAYLOAD = {
    "entityName": "Example Corp",
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                fact(900, 2024, "2024-12-31", "2024-01-01"),
                fact(1000, 2025, "2025-12-31", "2025-01-01"),
                fact(260, 2025, "2025-12-31", "2025-10-01"),  # a quarter inside a 10-K
                fact(270, 2025, "2025-09-30", "2025-07-01", form="10-Q", fp="Q3"),
            ]}},
            "NetIncomeLoss": {"units": {"USD": [fact(120, 2025, "2025-12-31", "2025-01-01")]}},
            "Assets": {"units": {"USD": [fact(5000, 2025, "2025-12-31")]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 50, "end": "2025-06-30"}, {"val": 48, "end": "2026-01-31"}]}}},
    },
}


def test_parses_annual_figures_and_ignores_quarters():
    f = parse_company_facts(PAYLOAD)
    assert f.company == "Example Corp"
    assert f.revenue == 1000 and f.revenue_prior == 900
    assert f.net_income == 120 and f.total_assets == 5000
    assert f.shares_outstanding == 48
    assert f.long_term_debt is None  # not reported is not zero


def test_empty_facts_mean_no_fundamentals():
    assert parse_company_facts({"facts": {}}) is None


def test_sec_requires_a_contact_in_the_user_agent():
    with pytest.raises(ValueError):
        EdgarFundamentals("no-contact")


@pytest.mark.parametrize("raw,expected", [("aapl", "AAPL"), (" brk.b ", "BRK.B"), ("BF-B", "BF-B")])
def test_symbol_normalisation(raw, expected):
    assert normalise_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "AAPL/../x", "A B", "123", "ABCDEFG", "AAPL?x=1"])
def test_symbol_rejection(raw):
    with pytest.raises(ValueError):
        normalise_symbol(raw)


def test_the_most_recent_of_equivalent_tags_wins():
    # Apple reported `Revenues` until 2018 and the ASC 606 tag after it.
    payload = {"entityName": "Switcher", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [fact(200, 2018, "2018-09-29", "2017-10-01")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            fact(380, 2024, "2024-09-28", "2023-10-01"),
            fact(400, 2025, "2025-09-27", "2024-09-29"),
        ]}},
    }}}
    f = parse_company_facts(payload)
    assert (f.revenue, f.revenue_prior, f.fiscal_year) == (400, 380, 2025)


def test_one_filing_restating_prior_years_yields_each_year_once():
    # A single 10-K carries three years, all with the filing's own fy.
    rows = [fact(v, 2025, f"{y}-12-31", f"{y}-01-01", filed="2026-02-01")
            for y, v in [(2023, 70), (2024, 80), (2025, 90)]]
    payload = {"entityName": "X", "facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}}
    f = parse_company_facts(payload)
    assert (f.revenue, f.revenue_prior) == (90, 80)
