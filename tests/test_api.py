def test_report_has_every_section_the_page_renders(client):
    r = client.get("/api/v1/symbols/AAPL/report")
    assert r.status_code == 200
    data = r.json()
    for key in ("quote", "composite", "technicals", "risk", "fundamentals", "news", "chart"):
        assert key in data
    assert -1 <= data["composite"]["score"] <= 1
    assert len(data["chart"]["dates"]) == len(data["chart"]["close"])
    assert data["disclaimer"]


def test_demo_data_is_deterministic_per_symbol(client):
    a = client.get("/api/v1/symbols/KO/report").json()
    b = client.get("/api/v1/symbols/KO/report").json()
    assert a["quote"] == b["quote"]
    assert a["quote"]["price"] != client.get("/api/v1/symbols/PEP/report").json()["quote"]["price"]


def test_one_snapshot_per_symbol_per_session(client):
    for _ in range(3):
        client.get("/api/v1/symbols/NVDA/report")
    rows = client.get("/api/v1/symbols/NVDA/history").json()["snapshots"]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"


def test_pages_and_probes(client):
    assert client.get("/").status_code == 200
    assert client.get("/s/AAPL").status_code == 200
    assert client.get("/s/not$valid").status_code == 422
    assert client.get("/architecture").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/static/app.css").status_code == 200


def test_meta_reports_demo_mode_and_rules_agent(client):
    meta = client.get("/api/v1/meta").json()
    assert meta["demo"] is True
    assert meta["agent"]["mode"] == "rules"


def test_openapi_documents_the_public_api(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/symbols/{symbol}/report" in paths
    assert "/api/v1/symbols/{symbol}/analyst" in paths
