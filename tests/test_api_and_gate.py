from reliabilityml.gate.validate import EXAMPLES, validate


def test_health_and_metrics(client):
    assert client.get("/health").json()["status"] == "ok"
    text = client.get("/metrics").text
    assert "rml_slo_burn_rate" in text and "rml_http_requests_total" in text


def test_incidents_contract(client):
    rows = client.get("/incidents").json()
    assert rows and {"id", "predicted", "confidence", "actual"} <= set(rows[0])
    detail = client.get(f"/incidents/{rows[0]['id']}").json()
    assert {"evidence", "explanation", "series", "trace"} <= set(detail)
    assert client.get("/incidents/INC-999").status_code == 404


def test_predict_contract(client):
    body = client.post(
        "/predict", json={"features": {"db_saturation_ratio": 0.97, "orders_latency_p99_delta": 3.0}}
    ).json()
    assert {"prediction", "confidence", "probabilities", "rules_baseline"} <= set(body)
    assert client.post("/predict", json={"features": {"not_a_feature": 1}}).status_code == 422


def test_slo_contract(client):
    body = client.get("/slo/web-frontend").json()
    assert body["availability"]["target"] == 0.999
    assert set(body["availability"]["burn_rates"]) == {"5min", "30min", "1h", "6h", "3D"}
    assert client.get("/slo/postgres-db").status_code == 404


def test_ask_contract(client):
    body = client.post("/ask", json={"question": "Which Redis eviction policy should the session cache use?"}).json()
    assert body["refused"] is True
    assert client.post("/ask", json={"question": "hi"}).status_code == 422


def test_validate_deployment_contract(client):
    ok = client.post("/validate-deployment", json={}).json()
    bad = client.post("/validate-deployment", json={"latency_multiplier": 1.3}).json()
    assert ok["passed"] is True and bad["passed"] is False
    assert [c["name"] for c in bad["checks"] if not c["passed"]] == ["Canary: p95 latency"]


def test_gate_blocks_bad_candidates(artifacts):
    for name in ("latency_regression", "error_regression", "weaker_model", "budget_freeze"):
        assert not validate(EXAMPLES[name], artifacts).passed, name
    assert validate(EXAMPLES["clean_release"], artifacts).passed
    assert validate(EXAMPLES["urgent_fix_during_freeze"], artifacts).passed


def test_dashboard_reports(client):
    for name in ("detection", "classifier", "slo", "rag", "drift", "gate"):
        assert client.get(f"/api/report/{name}").status_code == 200
    assert client.get("/api/registry").json()["aliases"]["production"] >= 1
