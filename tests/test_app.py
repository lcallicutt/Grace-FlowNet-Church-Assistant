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


# ---------------------------------------------------------------------------
# Railway pilot: health endpoint, DATA_DIR persistence, missing config,
# and atomic/locked writes.
# ---------------------------------------------------------------------------
import os  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_health_endpoint_returns_200_json(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.content_type.startswith("application/json")
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["model"]


def test_health_is_never_gated(client, monkeypatch):
    monkeypatch.setattr(main, "ACCESS_CODE", "test-code-not-a-secret")
    assert client.get("/health").status_code == 200


def _run_in_subprocess(code, env_overrides, cwd):
    env = dict(os.environ)
    for var in ("DATA_DIR", "GRACE_PROFILE_PATH", "GRACE_USAGE_PATH"):
        env.pop(var, None)
    env.update(env_overrides)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def test_data_dir_controls_storage_paths(tmp_path):
    code = (
        "import main, json;"
        "main.save_profile({'church_name': 'Volume Test'});"
        "print(main.PROFILE_PATH);"
        "print(main.USAGE_PATH)"
    )
    result = _run_in_subprocess(code, {"DATA_DIR": str(tmp_path)}, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    profile_path, usage_path = result.stdout.strip().splitlines()
    assert profile_path == str(tmp_path / "church_profile.json")
    assert usage_path == str(tmp_path / "grace_usage.json")
    saved = json.loads((tmp_path / "church_profile.json").read_text())
    assert saved["church_name"] == "Volume Test"


def test_without_data_dir_files_stay_local(tmp_path):
    workdir = tmp_path / "local-project"
    workdir.mkdir()
    code = (
        "import main;"
        "main.save_profile({'church_name': 'Local Test'});"
        "print(main.PROFILE_PATH)"
    )
    result = _run_in_subprocess(code, {}, cwd=workdir)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "church_profile.json"
    assert (workdir / "church_profile.json").exists()


def test_data_dir_is_created_if_missing(tmp_path):
    target = tmp_path / "nested" / "data"
    result = _run_in_subprocess(
        "import main; print(main.DATA_DIR)", {"DATA_DIR": str(target)}, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    assert target.is_dir()


def test_missing_api_key_returns_clean_json_error(client, monkeypatch):
    def no_credentials(**kwargs):
        raise TypeError(
            "Could not resolve authentication method. Expected one of api_key, "
            "auth_token, or credentials to be set."
        )

    monkeypatch.setattr(
        main, "client", SimpleNamespace(messages=SimpleNamespace(create=no_credentials))
    )
    res = client.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 500
    body = res.get_json()
    assert body["success"] is False
    assert "API key" in body["error"]


def test_concurrent_usage_writes_stay_consistent(client):
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    threads = [
        threading.Thread(target=main.record_usage, args=("chat", usage))
        for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # File must be valid JSON with every write accounted for.
    with open(main.USAGE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    totals = client.get("/api/usage").get_json()["totals"]
    assert totals["requests"] == 20
    assert totals["input_tokens"] == 200
    assert data["days"]


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out.json"
    main.atomic_write_json(str(target), {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
