def test_public_can_list_and_view_centres(client, catalog):
    listing = client.get("/centres")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1 and body["limit"] == 20 and body["offset"] == 0
    assert body["items"][0]["city"] == "Bengaluru"

    detail = client.get(f"/centres/{catalog['centre_id']}").json()
    assert detail["name"] == "EVE Indiranagar"
    assert detail["tests"] == [
        {
            "test": {"id": catalog["test_id"], "code": "CBC", "name": "Complete Blood Count", "description": None},
            "price": "500.00",
            "is_active": True,
        }
    ]


def test_filter_by_city_and_test(client, catalog, admin_headers):
    client.post(
        "/centres",
        json={"name": "EVE Bandra", "address": "Hill Rd", "city": "Mumbai"},
        headers=admin_headers,
    )
    assert client.get("/centres", params={"city": "bengaluru"}).json()["total"] == 1
    assert client.get("/centres", params={"city": "Mumbai"}).json()["total"] == 1
    by_test = client.get("/centres", params={"test_id": catalog["test_id"]}).json()
    assert [c["name"] for c in by_test["items"]] == ["EVE Indiranagar"]


def test_pagination(client, admin_headers):
    for i in range(5):
        client.post(
            "/centres",
            json={"name": f"Centre {i}", "address": f"Street {i}", "city": "Pune"},
            headers=admin_headers,
        )
    page = client.get("/centres", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5
    assert [c["name"] for c in page["items"]] == ["Centre 2", "Centre 3"]
    assert client.get("/centres", params={"limit": 1000}).status_code == 422
    assert client.get("/centres", params={"offset": -1}).status_code == 422


def test_writes_require_admin(client, user_headers):
    body = {"name": "X", "address": "Y", "city": "Z"}
    assert client.post("/centres", json=body).status_code == 401
    resp = client.post("/centres", json=body, headers=user_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ADMIN_REQUIRED"
    assert client.post("/tests", json={"code": "A", "name": "B"}, headers=user_headers).status_code == 403


def test_centre_validation(client, admin_headers):
    resp = client.post(
        "/centres",
        json={"name": "  ", "address": "Y", "city": "Z", "pincode": "12ab"},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert {d["loc"][-1] for d in resp.json()["error"]["details"]} == {"name", "pincode"}


def test_duplicate_centre_and_offering_conflict(client, admin_headers, catalog):
    dup = client.post(
        "/centres",
        json={"name": "EVE Indiranagar", "address": "100 Feet Rd", "city": "Bengaluru"},
        headers=admin_headers,
    )
    assert dup.status_code == 409
    resp = client.post(
        f"/centres/{catalog['centre_id']}/tests",
        json={"test_id": catalog["test_id"], "price": "100"},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TEST_ALREADY_OFFERED"


def test_offering_validation(client, admin_headers, catalog):
    url = f"/centres/{catalog['centre_id']}/tests"
    test_id = catalog["unoffered_test_id"]
    for bad_price in ["0", "-10", "10.999", "abc"]:
        resp = client.post(url, json={"test_id": test_id, "price": bad_price}, headers=admin_headers)
        assert resp.status_code == 422, bad_price
    missing_test = client.post(url, json={"test_id": 9999, "price": "10"}, headers=admin_headers)
    assert missing_test.status_code == 404
    missing_centre = client.post("/centres/9999/tests", json={"test_id": test_id, "price": "10"}, headers=admin_headers)
    assert missing_centre.status_code == 404


def test_price_update_invalidates_cache(client, admin_headers, catalog):
    url = f"/centres/{catalog['centre_id']}"
    assert client.get(url).json()["tests"][0]["price"] == "500.00"  # now cached
    resp = client.patch(
        f"{url}/tests/{catalog['test_id']}", json={"price": "650.00"}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert client.get(url).json()["tests"][0]["price"] == "650.00"


def test_deactivated_centre_and_test_are_hidden(client, admin_headers, catalog):
    url = f"/centres/{catalog['centre_id']}"
    client.patch(f"{url}/tests/{catalog['test_id']}", json={"is_active": False}, headers=admin_headers)
    assert client.get(url).json()["tests"] == []

    client.patch(url, json={"is_active": False}, headers=admin_headers)
    assert client.get("/centres").json()["total"] == 0
    assert client.get(url).status_code == 404


def test_patch_rejects_null_for_required_fields_and_empty_body(client, admin_headers, catalog):
    url = f"/centres/{catalog['centre_id']}"
    assert client.patch(url, json={"name": None}, headers=admin_headers).status_code == 422
    assert client.patch(url, json={}, headers=admin_headers).status_code == 422
    assert client.patch(url, json={"pincode": None}, headers=admin_headers).status_code == 200
    assert client.patch("/centres/9999", json={"city": "X"}, headers=admin_headers).status_code == 404


def test_list_tests_with_search(client, catalog):
    assert client.get("/tests").json()["total"] == 2
    found = client.get("/tests", params={"q": "blood"}).json()
    assert [t["code"] for t in found["items"]] == ["CBC"]
