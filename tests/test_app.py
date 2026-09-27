def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    assert len(client.get("/health").headers["X-Request-ID"]) == 32


def test_openapi_documents_all_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ["/auth/signup", "/auth/login", "/centres", "/bookings", "/payments/", "/payments/webhook/"]:
        assert path in paths
    webhook = paths["/payments/webhook/"]["post"]
    assert "requestBody" in webhook


def test_unknown_route_uses_error_format(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
