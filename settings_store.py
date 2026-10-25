"""Pengaturan aplikasi yang dapat diubah administrator.

Disimpan sebagai JSON di ``instance/settings.json`` (dapat diubah melalui
``SETTINGS_PATH``). Berkas memuat hash kata sandi admin dan kunci API Gemini;
karena itu berkas ditulis dengan izin ``0600`` dan tidak boleh masuk repositori.

Nilai dari panel admin diutamakan, lalu variabel lingkungan, lalu nilai bawaan.
Variabel ``LLM_ENABLED=0`` selalu memaksa AI nonaktif.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = BASE_DIR / "instance" / "settings.json"
DEFAULT_MODEL = "gemini-2.5-flash"
MIN_PASSWORD_LENGTH = 8

DEFAULTS: dict[str, Any] = {
    "admin_password_hash": "",
    "gemini_api_key": "",
    "gemini_model": "",
    "llm_enabled": True,
    "updated_at": "",
}

_LOCK = threading.RLock()


def path() -> Path:
    return Path(os.environ.get("SETTINGS_PATH", str(DEFAULT_PATH))).expanduser()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        raw = path().read_text(encoding="utf-8")
    except OSError:
        return data
    try:
        stored = json.loads(raw)
    except (ValueError, TypeError):
        return data
    if not isinstance(stored, dict):
        return data
    for key, default in DEFAULTS.items():
        value = stored.get(key, default)
        if isinstance(default, bool):
            data[key] = bool(value)
        elif isinstance(value, str):
            data[key] = value
    return data


def save(data: dict[str, Any]) -> None:
    target = path()
    merged = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        value = data.get(key, default)
        if isinstance(default, bool):
            merged[key] = bool(value)
        elif isinstance(value, str):
            merged[key] = value
    merged["updated_at"] = _now()
    with _LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".settings-", dir=str(target.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(merged, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def update(**changes: Any) -> dict[str, Any]:
    with _LOCK:
        data = load()
        for key, value in changes.items():
            if key in DEFAULTS:
                data[key] = value
        save(data)
        return data


def is_admin_configured() -> bool:
    return bool(load()["admin_password_hash"] or os.environ.get("ADMIN_PASSWORD", "").strip())


def verify_admin_password(password: str) -> bool:
    password = password or ""
    stored = load()["admin_password_hash"]
    if stored:
        try:
            return check_password_hash(stored, password)
        except ValueError:
            return False
    env_password = os.environ.get("ADMIN_PASSWORD", "")
    return bool(env_password) and secrets.compare_digest(env_password, password)


def set_admin_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Kata sandi minimal {MIN_PASSWORD_LENGTH} karakter.")
    update(admin_password_hash=generate_password_hash(password))


def effective_api_key() -> str:
    return (
        load()["gemini_api_key"]
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("GOOGLE_API_KEY", "")
    ).strip()


def effective_model() -> str:
    return (load()["gemini_model"] or os.environ.get("GEMINI_MODEL", "") or DEFAULT_MODEL).strip()


def effective_llm_enabled() -> bool:
    if os.environ.get("LLM_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return load()["llm_enabled"]


def mask_secret(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{'•' * 8}{value[-4:]}"
