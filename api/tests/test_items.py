from app.repositories.item_repository import ItemRepository


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
    assert response.json() == {"error": "Item 999 not found"}


def test_duplicate_name_is_409_and_rolled_back(client):
    assert client.post("/api/v1/items", json={"name": "Same"}).status_code == 201
    assert client.post("/api/v1/items", json={"name": "Same"}).status_code == 409
    assert len(client.get("/api/v1/items").json()) == 1


def test_validation(client):
    # Bad input is a 400 with one plain sentence, as the current app (#604).
    refused = client.post("/api/v1/items", json={"name": ""})
    assert refused.status_code == 400 and refused.json()["error"].startswith("name:")
    assert client.post("/api/v1/items", json={"name": "x", "extra": 1}).status_code == 400
    assert client.get("/api/v1/items?limit=0").status_code == 400


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


def test_concurrent_duplicate_is_409_not_500(client, monkeypatch):
    # Another request inserted the name after this one's check: only the unique
    # constraint catches it.
    assert client.post("/api/v1/items", json={"name": "Race"}).status_code == 201
    monkeypatch.setattr(ItemRepository, "get_by_name", lambda self, name: None)
    response = client.post("/api/v1/items", json={"name": "Race"})
    assert response.status_code == 409
    assert response.json() == {"error": "Item named 'Race' already exists"}
    assert len(client.get("/api/v1/items").json()) == 1
