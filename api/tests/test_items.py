def test_create_then_get_and_list(client):
    created = client.post("/api/v1/items", json={"name": "Widget", "description": "A widget"})
    assert created.status_code == 201
    item = created.json()
    assert item["name"] == "Widget" and item["id"] == 1 and item["created_at"]

    assert client.get(f"/api/v1/items/{item['id']}").json() == item
    assert client.get("/api/v1/items").json() == [item]


def test_unknown_item_is_404(client):
    response = client.get("/api/v1/items/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Item 999 not found"}


def test_duplicate_name_is_409_and_rolled_back(client):
    assert client.post("/api/v1/items", json={"name": "Same"}).status_code == 201
    assert client.post("/api/v1/items", json={"name": "Same"}).status_code == 409
    assert len(client.get("/api/v1/items").json()) == 1


def test_validation(client):
    assert client.post("/api/v1/items", json={"name": ""}).status_code == 422
    assert client.post("/api/v1/items", json={"name": "x", "extra": 1}).status_code == 422
    assert client.get("/api/v1/items?limit=0").status_code == 422


def test_delete(client):
    item_id = client.post("/api/v1/items", json={"name": "Gone"}).json()["id"]
    assert client.delete(f"/api/v1/items/{item_id}").status_code == 204
    assert client.get(f"/api/v1/items/{item_id}").status_code == 404
    assert client.delete(f"/api/v1/items/{item_id}").status_code == 404


def test_pagination(client):
    for n in range(3):
        client.post("/api/v1/items", json={"name": f"n{n}"})
    page = client.get("/api/v1/items?limit=2&offset=1").json()
    assert [i["name"] for i in page] == ["n1", "n2"]
