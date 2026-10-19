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


def test_prior_year_and_history_come_from_the_same_series():
    years = range(2019, 2026)
    rows = [fact(100 + 10 * i, y, f"{y}-12-31", f"{y}-01-01") for i, y in enumerate(years)]
    shares = [fact(1000 - i, y, f"{y}-12-31", f"{y}-01-01") for i, y in enumerate(years)]
    payload = {"entityName": "Hist", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": rows}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": shares}},
        "AccountsReceivableNetCurrent": {"units": {"USD": [
            fact(30, 2025, "2024-12-31"), fact(40, 2025, "2025-12-31")]}},
        "RetainedEarningsAccumulatedDeficit": {"units": {"USD": [fact(-5, 2025, "2025-12-31")]}},
    }}}
    f = parse_company_facts(payload)
    assert f.revenue == 160 and f.prior["revenue"] == 150
    assert f.shares_diluted == 994 and f.prior["shares_diluted"] == 995
    assert (f.receivables, f.prior["receivables"]) == (40, 30)
    assert f.retained_earnings == -5 and f.prior["retained_earnings"] is None
    assert [v for _, v in f.history["revenue"]] == [120, 130, 140, 150, 160]
    assert f.history["revenue"][-1][0] == "2025-12-31"
    assert len(f.history["shares_diluted"]) == 5
    assert "net_income" not in f.history  # not reported is absent, not empty zeros


def test_a_gap_in_the_filings_is_not_a_prior_year():
    rows = [fact(80, 2022, "2022-12-31", "2022-01-01"), fact(100, 2025, "2025-12-31", "2025-01-01")]
    payload = {"entityName": "Gap", "facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}}
    f = parse_company_facts(payload)
    assert f.prior["revenue"] is None
    assert f.revenue_prior == 80  # the existing field keeps its old behaviour


SUBMISSIONS = {"cik": "320193", "name": "Apple Inc.", "sicCode": "3571",
               "sicDescription": "Electronic Computers", "tickers": ["AAPL"]}


def test_sic_comes_from_the_submissions_profile():
    from stocklens.data.edgar import parse_sic

    assert parse_sic(SUBMISSIONS) == 3571
    assert parse_sic({"sicCode": ""}) is None
    assert parse_sic({}) is None
    assert parse_sic({"sicCode": "not a code"}) is None


def test_sic_is_attached_and_its_failure_never_costs_the_fundamentals(monkeypatch):
    from stocklens.data.base import DataUnavailable

    def serve(fail_submissions):
        def fake_get(self, url):
            if "company_tickers" in url:
                return {"0": {"ticker": "AAPL", "cik_str": 320193}}
            if "submissions" in url:
                if fail_submissions:
                    raise DataUnavailable("sec unreachable")
                return SUBMISSIONS
            return PAYLOAD
        return fake_get

    monkeypatch.setattr(EdgarFundamentals, "_get", serve(False))
    f = EdgarFundamentals("tests tests@example.com").fundamentals("AAPL")
    assert f.sic == 3571 and f.revenue == 1000

    monkeypatch.setattr(EdgarFundamentals, "_get", serve(True))
    f = EdgarFundamentals("tests tests@example.com").fundamentals("AAPL")
    assert f.sic is None and f.revenue == 1000


def test_fixture_payload_without_sic_is_backward_compatible():
    assert parse_company_facts(PAYLOAD).sic is None


def test_sic_rolls_up_to_spdr_sectors():
    from stocklens.data.sectors import (
        REPRESENTATIVE_SIC,
        SECTOR_ETFS,
        etf_for,
        sector_for_symbol,
        sector_from_sic,
    )

    assert sector_from_sic(3571) == "technology"  # electronic computers
    assert sector_from_sic(2834) == "health_care"  # pharma overrides group 28
    assert sector_from_sic(2810) == "materials"
    assert sector_from_sic(3711) == "industrials"
    assert sector_from_sic(3721) == "industrials"
    assert sector_from_sic(6798) == "real_estate"  # REITs moved out of financials
    assert sector_from_sic(6022) == "financials"
    assert sector_from_sic(1311) == "energy"
    assert sector_from_sic(4911) == "utilities"
    assert sector_from_sic(4813) == "communication_services"
    assert sector_from_sic(9999) is None and sector_from_sic(None) is None
    assert sector_from_sic(0) is None
    for sector, sic in REPRESENTATIVE_SIC.items():
        assert sector_from_sic(sic) == sector
    assert etf_for("energy") == "XLE" and etf_for(None) == "SPY" and etf_for("nope") == "SPY"
    assert len(set(SECTOR_ETFS.values())) == 11
    assert sector_for_symbol("GOOGL", sic=7370) == "communication_services"  # override wins
    assert sector_for_symbol("XLU") == "utilities"
    assert sector_for_symbol("ZZZZ", sic=2834) == "health_care"


def test_demo_sectors_and_sic_agree():
    from stocklens.data.demo import DemoFundamentals, sector_of
    from stocklens.data.sectors import SECTOR_ETFS, sector_from_sic

    assert sector_of("AAPL") == "technology" and sector_of("XLE") == "energy"
    assert sector_of("TSLA") == "consumer_discretionary"
    for symbol in ("AAPL", "ZZZZ", "ACME", "XLRE"):
        assert sector_of(symbol) in SECTOR_ETFS
        assert sector_from_sic(DemoFundamentals().fundamentals(symbol).sic) == sector_of(symbol)
