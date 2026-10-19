"""The deep-analysis layer end to end: report sections, the portfolio lab, the agent's view."""

from __future__ import annotations

import json

from stocklens.agent.analyst import rules_report
from stocklens.agent.tools import Toolbox
from stocklens.analytics.composite import DEFAULT_WEIGHTS
from stocklens.data.demo import DemoPrices


def test_report_carries_quant_structure_and_research(client):
    data = client.get("/api/v1/symbols/MSFT/report").json()
    json.dumps(data, allow_nan=False)  # the page and the agent both need strict JSON
    assert data["quant"]["regime"]["trend"] in {"trending", "mean_reverting", "random"}
    assert data["quant"]["volatility"]["garch"]["persistence"] < 1
    assert 0 <= data["quant"]["sharpe"]["psr_vs_zero"] <= 1
    assert data["structure"]["volume_profile"]["poc"] > 0
    assert data["structure"]["analogs"]["matches"]
    assert data["research"]["piotroski"]["max_known"] <= 9


def test_regime_weights_preserve_the_total_and_feed_the_composite(client):
    comp = client.get("/api/v1/symbols/NVDA/report").json()["composite"]
    assert abs(sum(comp["regime_weights"].values()) - sum(DEFAULT_WEIGHTS.values())) < 1e-9
    assert comp["base_weights"] == DEFAULT_WEIGHTS
    used = [c for c in comp["contributions"] if c["kind"] == "directional"]
    assert all(c["weight"] == comp["regime_weights"][c["name"]] for c in used)


def test_portfolio_lab_finds_the_common_factor(client):
    r = client.get("/api/v1/portfolio?symbols=AAPL,MSFT,NVDA,JPM,XOM,KO,PFE,CAT")
    assert r.status_code == 200
    data = r.json()
    assert data["spectrum"]["signal_factors"] >= 1
    for method in data["methods"].values():
        assert abs(sum(method["weights"].values()) - 1) < 1e-3  # rounded to 4 places
        assert min(method["weights"].values()) >= -1e-9
    mv, eq = data["methods"]["min_variance"], data["methods"]["equal"]
    assert mv["volatility_annual"] <= eq["volatility_annual"]


def test_portfolio_lab_validates_before_any_upstream_call(client):
    assert client.get("/api/v1/portfolio?symbols=AAPL,x$y").status_code == 422
    assert client.get("/api/v1/portfolio?symbols=AAPL").status_code == 422
    many = ",".join(f"A{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(26))
    assert client.get(f"/api/v1/portfolio?symbols={many}").status_code == 422
    assert client.get("/portfolio").status_code == 200


def test_demo_series_are_one_path_whatever_the_request_length():
    prices = DemoPrices()
    assert prices.daily_bars("KO", 300) == prices.daily_bars("KO", 756)[-300:]


def test_deep_tools_return_citable_metrics(client):
    data = client.get("/api/v1/symbols/MSFT/report").json()
    box = Toolbox(data)
    for tool in ("get_quant", "get_market_structure", "get_research"):
        json.loads(box.call(tool, {}))
    assert "quant.garch.persistence" in box.seen
    assert "profile.poc" in box.seen
    assert "research.piotroski.score" in box.seen


def test_rules_brief_stays_fully_grounded_with_the_new_evidence(client):
    for symbol in ("MSFT", "AAPL", "BRK.B"):
        data = client.get(f"/api/v1/symbols/{symbol}/report").json()
        run = rules_report(data)
        assert run.grounding["score"] == 1.0


def test_viewing_a_report_commits_one_forecast_per_session(client):
    for _ in range(3):
        client.get("/api/v1/symbols/KO/report")
    live = client.get("/api/v1/symbols/KO/forecasts").json()["live"]["forecasts"]
    assert len(live) == 1
    assert 0.3 < live[0]["p_up"] < 0.7
    assert live[0]["outcome_up"] is None  # not knowable yet


def test_track_record_says_no_skill_on_demo_noise(client):
    data = client.get("/api/v1/track-record").json()
    assert data["simulated"]["kind"] == "simulated"
    assert data["simulated"]["scoring"]["verdict"]["label"] != "skill"
    assert client.get("/track-record").status_code == 200
    assert client.get("/why").status_code == 200


def test_evidence_lab_finds_nothing_in_demo_noise(client):
    data = client.get("/api/v1/symbols/MSFT/evidence").json()
    assert data["families"]
    assert not any(f["works"] for f in data["families"])


def test_drivers_decompose_and_stress_the_stock(client):
    d = client.get("/api/v1/symbols/NVDA/report").json()["drivers"]
    shares = d["attribution"]["variance_share"]
    assert abs(sum(v for v in shares.values() if v is not None) - 1) < 1e-3
    moves = [s["expected"] for s in d["shocks"]]
    assert moves == sorted(moves)  # positive beta: a bigger market move, a bigger stock move
    assert d["simulation"]["fan"]


def test_analog_false_positive_rate_is_nominal_on_noise():
    """The Why page claims 5%; this keeps the claim true."""
    from stocklens.quant import similarity

    prices = DemoPrices()
    found = [similarity.analogs(prices.daily_bars(f"Z{chr(65 + i // 26)}{chr(65 + i % 26)}", 756))
             for i in range(120)]
    rate = sum(r["significant"] for r in found if r) / sum(1 for r in found if r)
    assert rate <= 0.10
