"""Backend tests for Grace Ministry Hub — run with: pytest tests/test_app.py"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setattr(main, "USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(main, "ACCESS_CODE", "")
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_chat_rejects_empty_message(client):
    res = client.post("/api/chat", json={"message": "  "})
    assert res.status_code == 400
    assert res.get_json()["success"] is False


def test_weekly_service_requires_announcements(client):
    res = client.post("/api/weekly-service", json={"church_name": "Greater Emmanuel"})
    assert res.status_code == 400
    assert "announcements" in res.get_json()["error"].lower()


def test_profile_round_trip(client):
    assert client.get("/api/profile").get_json()["profile"] == {}
    payload = {
        "church_name": "Greater Emmanuel",
        "service_times": "Saturdays 6pm",
        "office_contact": "(555) 123-4567",
        "order_of_service": "Welcome, Worship, Sermon",
        "standing_announcements": "- Bible study Wednesdays",
        "notes": "Rev. Nkemelu = en-keh-MEH-loo",
    }
    res = client.post("/api/profile", json=payload)
    assert res.status_code == 200
    assert res.get_json()["profile"] == payload
    assert client.get("/api/profile").get_json()["profile"] == payload


def test_usage_recording_and_cost_math(client):
    usage = SimpleNamespace(
        input_tokens=2500,
        output_tokens=4300,
        cache_read_input_tokens=3000,
        cache_creation_input_tokens=1800,
    )
    main.record_usage("weekly_service", usage)
    data = client.get("/api/usage").get_json()
    totals = data["totals"]
    assert totals["requests"] == 1
    assert totals["input_tokens"] == 2500
    expected = main._estimated_cost(
        {
            "input_tokens": 2500,
            "output_tokens": 4300,
            "cache_read_input_tokens": 3000,
            "cache_creation_input_tokens": 1800,
        }
    )
    assert totals["estimated_cost_usd"] == expected
    assert expected == pytest.approx(0.0797, abs=0.0002)


def test_reset_clears_conversation(client):
    main.conversations["abc"] = [{"role": "user", "content": "hi"}]
    res = client.post("/api/reset", json={"session_id": "abc"})
    assert res.status_code == 200
    assert "abc" not in main.conversations


class TestAccessGate:
    @pytest.fixture
    def gated(self, client, monkeypatch):
        monkeypatch.setattr(main, "ACCESS_CODE", "test-code-not-a-secret")
        return client

    def test_health_and_auth_exempt(self, gated):
        assert gated.get("/api/health").status_code == 200
        assert gated.get("/api/auth").get_json()["required"] is True

    def test_protected_routes_401_without_code(self, gated):
        assert gated.get("/api/profile").status_code == 401
        assert gated.get("/api/usage").status_code == 401
        assert gated.post("/api/chat", json={"message": "hi"}).status_code == 401

    def test_wrong_code_rejected(self, gated):
        res = gated.post("/api/auth", json={"code": "wrong"})
        assert res.status_code == 401
        assert res.get_json()["valid"] is False

    def test_right_code_passes_gate(self, gated):
        res = gated.post("/api/auth", json={"code": "test-code-not-a-secret"})
        assert res.status_code == 200
        assert res.get_json()["valid"] is True
        res = gated.get("/api/profile", headers={"X-Access-Code": "test-code-not-a-secret"})
        assert res.status_code == 200

    def test_gate_off_means_open(self, client, monkeypatch):
        monkeypatch.setattr(main, "ACCESS_CODE", "")
        assert client.get("/api/profile").status_code == 200
        assert client.get("/api/auth").get_json()["required"] is False


def test_index_serves_markdown_module(client):
    page = client.get("/").get_data(as_text=True)
    assert '<script src="markdown.js"></script>' in page
    js = client.get("/markdown.js")
    assert js.status_code == 200
    assert "safeRenderMarkdown" in js.get_data(as_text=True)
