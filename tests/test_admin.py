from __future__ import annotations

import re
from pathlib import Path

import pytest

import settings_store


def _csrf(client, path: str = "/admin/login") -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, "Token CSRF tidak ditemukan pada formulir admin."
    return match.group(1)


def _setup_admin(client, password: str = "rahasia123") -> None:
    token = _csrf(client)
    response = client.post(
        "/admin/login",
        data={"csrf_token": token, "password": password, "confirm": password},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")


@pytest.fixture()
def admin_client(monkeypatch, tmp_path):
    from app import app

    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("SETTINGS_PATH", str(settings_file))
    for variable in ("ADMIN_PASSWORD", "LLM_ENABLED", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    app.config.update(TESTING=True)
    with app.test_client() as client:
        client.settings_file = settings_file
        yield client


def test_admin_panel_requires_login(admin_client):
    response = admin_client.get("/admin")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


def test_first_run_creates_admin_password(admin_client):
    page = admin_client.get("/admin/login").get_data(as_text=True)
    assert "Buat kata sandi admin" in page

    _setup_admin(admin_client)

    panel = admin_client.get("/admin")
    assert panel.status_code == 200
    assert "Pengaturan aplikasi" in panel.get_data(as_text=True)

    stored = admin_client.settings_file.read_text(encoding="utf-8")
    assert "rahasia123" not in stored
    assert "admin_password_hash" in stored


def test_admin_password_is_required_for_login(admin_client):
    settings_store.set_admin_password("rahasia123")

    session_clear_token = _csrf(admin_client)
    bad = admin_client.post(
        "/admin/login",
        data={"csrf_token": session_clear_token, "password": "salah-sekali"},
    )
    assert bad.status_code == 200
    assert "Kata sandi admin salah." in bad.get_data(as_text=True)
    assert admin_client.get("/admin").status_code == 302

    good_token = _csrf(admin_client)
    good = admin_client.post(
        "/admin/login",
        data={"csrf_token": good_token, "password": "rahasia123"},
    )
    assert good.status_code == 302
    assert admin_client.get("/admin").status_code == 200


def test_admin_can_save_api_key_and_disable_ai(admin_client):
    _setup_admin(admin_client)
    token = _csrf(admin_client, "/admin")

    response = admin_client.post(
        "/admin/settings",
        data={
            "csrf_token": token,
            "gemini_api_key": "kunci-uji-12345",
            "gemini_model": "gemini-uji",
        },
    )
    assert response.status_code == 302

    data = settings_store.load()
    assert data["gemini_api_key"] == "kunci-uji-12345"
    assert data["gemini_model"] == "gemini-uji"
    assert data["llm_enabled"] is False

    from llm_analyzer import status

    assert status()["available"] is False
    assert "dinonaktifkan" in status()["reason"]

    panel = admin_client.get("/admin").get_data(as_text=True)
    assert "kunci-uji-12345" not in panel
    assert "2345" in panel


def test_admin_can_enable_ai_with_stored_model(admin_client):
    _setup_admin(admin_client)
    settings_store.update(gemini_api_key="kunci-uji", gemini_model="gemini-uji", llm_enabled=True)
    token = _csrf(admin_client, "/admin")

    response = admin_client.post(
        "/admin/settings",
        data={
            "csrf_token": token,
            "gemini_api_key": "",
            "gemini_model": "gemini-uji",
            "llm_enabled": "on",
        },
    )
    assert response.status_code == 302

    from llm_analyzer import status

    result = status()
    assert result["available"] is True
    assert result["model"] == "gemini-uji"


def test_admin_settings_post_requires_csrf(admin_client):
    _setup_admin(admin_client)
    response = admin_client.post("/admin/settings", data={"gemini_model": "x"})
    assert response.status_code == 400


def test_admin_can_change_password(admin_client):
    _setup_admin(admin_client, "rahasia123")
    token = _csrf(admin_client, "/admin")
    response = admin_client.post(
        "/admin/password",
        data={
            "csrf_token": token,
            "current_password": "rahasia123",
            "new_password": "sandi-baru-123",
            "confirm_password": "sandi-baru-123",
        },
    )
    assert response.status_code == 302

    logout_token = _csrf(admin_client, "/admin")
    admin_client.post("/admin/logout", data={"csrf_token": logout_token})
    assert admin_client.get("/admin").status_code == 302

    login_token = _csrf(admin_client)
    admin_client.post(
        "/admin/login",
        data={"csrf_token": login_token, "password": "rahasia123"},
    )
    assert admin_client.get("/admin").status_code == 302

    new_token = _csrf(admin_client)
    admin_client.post(
        "/admin/login",
        data={"csrf_token": new_token, "password": "sandi-baru-123"},
    )
    assert admin_client.get("/admin").status_code == 200


def test_admin_can_clear_jobs(admin_client):
    from app import store

    _setup_admin(admin_client)
    store.create({"workdir": ""})
    assert store.stats()["jobs"] >= 1

    token = _csrf(admin_client, "/admin")
    response = admin_client.post("/admin/jobs/clear", data={"csrf_token": token})
    assert response.status_code == 302
    assert store.stats()["jobs"] == 0


def test_settings_path_is_private(admin_client):
    _setup_admin(admin_client)
    assert Path(admin_client.settings_file).stat().st_mode & 0o777 == 0o600
