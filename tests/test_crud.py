def test_create_read_update_delete(client):
    r = client.post("/api/transcriptions", json={
        "text": "  Kal ki meeting 10 baje hai, please don't be late.  ",
        "engine": "gemini", "language": "hi,en", "duration_seconds": 4.2,
    })
    assert r.status_code == 201
    row = r.json()
    assert row["text"] == "Kal ki meeting 10 baje hai, please don't be late."
    assert row["title"] == "Kal ki meeting 10 baje hai, please don't…"
    assert row["word_count"] == 10

    assert client.get(f"/api/transcriptions/{row['id']}").json()["engine"] == "gemini"

    r = client.patch(f"/api/transcriptions/{row['id']}", json={"title": "Meeting reminder", "text": "मीटिंग 10 बजे है"})
    assert r.status_code == 200
    assert r.json()["title"] == "Meeting reminder"
    assert r.json()["text"] == "मीटिंग 10 बजे है"
    assert r.json()["language"] == "hi,en"  # untouched

    assert client.delete(f"/api/transcriptions/{row['id']}").status_code == 204
    assert client.get(f"/api/transcriptions/{row['id']}").status_code == 404
    assert client.delete(f"/api/transcriptions/{row['id']}").status_code == 404


def test_validation(client):
    assert client.post("/api/transcriptions", json={"text": "   "}).status_code == 422
    assert client.post("/api/transcriptions", json={}).status_code == 422
    r = client.post("/api/transcriptions", json={"text": "hello"})
    assert client.patch(f"/api/transcriptions/{r.json()['id']}", json={"text": " "}).status_code == 422


def test_list_search_filter_paginate_stats(client):
    for i in range(25):
        client.post("/api/transcriptions", json={
            "text": f"note number {i} " + ("namaste duniya" if i % 5 == 0 else "hello world"),
            "engine": "whisper" if i % 2 else "gemini",
            "duration_seconds": 2,
        })
    page = client.get("/api/transcriptions", params={"page_size": 10, "page": 3}).json()
    assert page["total"] == 25 and len(page["items"]) == 5
    newest = client.get("/api/transcriptions").json()["items"][0]
    assert newest["text"].startswith("note number 24")

    assert client.get("/api/transcriptions", params={"q": "NAMASTE"}).json()["total"] == 5
    assert client.get("/api/transcriptions", params={"engine": "whisper"}).json()["total"] == 12

    stats = client.get("/api/transcriptions/stats").json()
    assert stats["total"] == 25
    assert stats["total_audio_seconds"] == 50
    assert stats["by_engine"] == {"gemini": 13, "whisper": 12}
    assert stats["total_words"] == 25 * 5


def test_config_and_index(client):
    cfg = client.get("/api/config").json()
    assert cfg["engines"]["gemini"]["server_key"] is False
    assert "hi-en" in cfg["languages"]
    assert "Speech to Text" in client.get("/").text
    assert client.get("/healthz").json() == {"status": "ok"}
