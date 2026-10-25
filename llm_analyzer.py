"""Integrasi Google Gemini untuk analisis dan ekstraksi Renja.

Modul ini opsional. Jika ``GEMINI_API_KEY`` (atau ``GOOGLE_API_KEY``) tidak
tersedia, semua fungsi mengembalikan status nonaktif dan aplikasi tetap
berjalan dengan mesin rule-based. Prompt disimpan di folder ``prompts/`` agar
mudah ditinjau dan disesuaikan tanpa mengubah kode.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from extract_renja import norm_space, parse_pagu
from master_nomenklatur import MasterNomenklatur, code_key, normalize_code
from review_fields import normalize_finding

LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent

CODE_RE = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d{1,2}){1,4})(?!\d)")

FALLBACK_ANALYSIS_SYSTEM = (
    "Anda adalah perencana ahli Bappeda. Periksa Renja OPD terhadap Permendagri 86/2017, "
    "Permendagri 90/2019 beserta pemutakhirannya, serta RPJMD/Renstra/RKPD. Jangan mengarang kode "
    "atau nomenklatur. Kembalikan satu objek JSON valid dengan kunci ringkasan, kesimpulan, "
    "rekomendasi_umum, penyelarasan, dan temuan."
)
FALLBACK_ANALYSIS_USER = "Analisis data berikut dan kembalikan JSON valid:\n\n{{PAYLOAD}}"
FALLBACK_EXTRACTION_SYSTEM = (
    "Ekstrak teks Renja menjadi JSON hierarki Program-Kegiatan-Subkegiatan. Jangan mengarang data. "
    'Kembalikan {"opd":"","tahun":"","judul":"","program":[]} jika tidak ada.'
)
FALLBACK_EXTRACTION_USER = "Nama file: {{FILENAME}}\nMaster: {{MASTER_RINGKAS}}\n\nTEKS:\n{{TEKS}}"


def _load_prompt(filename: str, fallback: str) -> str:
    path = BASE_DIR / "prompts" / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return fallback


PROMPT_ANALISIS_SISTEM = _load_prompt("analisis_system.md", FALLBACK_ANALYSIS_SYSTEM)
PROMPT_ANALISIS_USER = _load_prompt("analisis_user.md", FALLBACK_ANALYSIS_USER)
PROMPT_EKSTRAKSI_SISTEM = _load_prompt("ekstraksi_system.md", FALLBACK_EXTRACTION_SYSTEM)
PROMPT_EKSTRAKSI_USER = _load_prompt("ekstraksi_user.md", FALLBACK_EXTRACTION_USER)


def _api_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def _disabled_by_flag() -> bool:
    return os.environ.get("LLM_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}


def _sdk_available() -> bool:
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True


def is_enabled() -> bool:
    return not _disabled_by_flag() and bool(_api_key()) and _sdk_available()


def status() -> dict[str, Any]:
    if _disabled_by_flag():
        reason = "Analisis AI dinonaktifkan melalui LLM_ENABLED."
    elif not _api_key():
        reason = "GEMINI_API_KEY belum diset. Analisis rule-based tetap berjalan."
    elif not _sdk_available():
        reason = "Paket google-genai belum terpasang. Jalankan pip install -r requirements.txt."
    else:
        reason = ""
    return {
        "available": is_enabled(),
        "provider": "Google Gemini",
        "model": model_name() if not reason else "",
        "reason": reason,
    }


def _timeout_ms() -> int:
    try:
        seconds = float(os.environ.get("LLM_TIMEOUT_SECONDS", "90"))
    except ValueError:
        seconds = 90
    return max(1000, int(seconds * 1000))


def _client():
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=_api_key(),
        http_options=types.HttpOptions(timeout=_timeout_ms()),
    )


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Respons LLM kosong.")
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("Respons LLM tidak mengandung objek JSON.")
    try:
        parsed, _end = json.JSONDecoder().raw_decode(text[start:])
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    end = text.rfind("}")
    if end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("JSON dari LLM tidak valid.")


def _call_json(system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    from google.genai import types

    client = _client()
    model = model_name()
    max_tokens = max_tokens or int(os.environ.get("LLM_MAX_TOKENS", "6000"))
    last_error: Exception | None = None
    for attempt in range(2):
        prompt = user_prompt
        if attempt:
            prompt += "\n\nPENTING: respons sebelumnya tidak dapat diparse. Keluarkan HANYA objek JSON valid, tanpa markdown."
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
            ),
        )
        try:
            text = response.text or ""
        except Exception:
            text = ""
        try:
            data = _parse_json(text)
            usage = getattr(response, "usage_metadata", None)
            return data, {
                "provider": "google-gemini",
                "model": getattr(response, "model_version", "") or model,
                "input_tokens": getattr(usage, "prompt_token_count", None),
                "output_tokens": getattr(usage, "candidates_token_count", None),
            }
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            LOGGER.warning("Respons LLM tidak dapat diparse (percobaan %s): %s", attempt + 1, exc)
    raise ValueError(f"Gagal memperoleh JSON valid dari LLM: {last_error}")


# ----------------------------------------------------------------------
# Ekstraksi
# ----------------------------------------------------------------------
def _master_excerpt(master: MasterNomenklatur, text: str) -> str:
    codes = list(dict.fromkeys(CODE_RE.findall(text)))[:250]
    entries = master.compact_entries_for_codes(codes, limit=250)
    if len(entries) < 20:
        for level in ("Program", "Kegiatan", "Subkegiatan"):
            for node in master.index_by_level.get(level, [])[:30]:
                entries.append(
                    {
                        "level": node["level"],
                        "kode": node["kode"],
                        "nama": node["nama"],
                        "program": node["program_nama"],
                        "kegiatan": node["kegiatan_nama"],
                    }
                )
    seen: set[tuple[int, ...]] = set()
    unique_entries = []
    for entry in entries:
        key = code_key(entry.get("kode"))
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(entry)
    payload = {
        "meta": master.meta,
        "jumlah_master": master.counts(),
        "entri_relevan": unique_entries[:300],
    }
    return json.dumps(payload, ensure_ascii=False)


def _norm_indicator_list(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        result.append(
            {
                "nama": norm_space(raw.get("nama") or raw.get("indikator")),
                "target": norm_space(raw.get("target")),
                "satuan": norm_space(raw.get("satuan")),
            }
        )
    return result


def _normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    programs = []
    for raw_program in data.get("program") or []:
        if not isinstance(raw_program, dict):
            continue
        program = {
            "kode": normalize_code(raw_program.get("kode")),
            "nama": norm_space(raw_program.get("nama")),
            "indikator": _norm_indicator_list(raw_program.get("indikator")),
            "sasaran": [],
            "pagu": None,
            "kegiatan": [],
            "_sintetis": False,
            "sumber": "llm",
        }
        for raw_kegiatan in raw_program.get("kegiatan") or []:
            if not isinstance(raw_kegiatan, dict):
                continue
            kegiatan = {
                "kode": normalize_code(raw_kegiatan.get("kode")),
                "nama": norm_space(raw_kegiatan.get("nama")),
                "indikator": _norm_indicator_list(raw_kegiatan.get("indikator")),
                "pagu": None,
                "lokasi": norm_space(raw_kegiatan.get("lokasi")),
                "keterangan": norm_space(raw_kegiatan.get("keterangan")),
                "subkegiatan": [],
                "_sintetis": False,
                "sumber": "llm",
            }
            for raw_sub in raw_kegiatan.get("subkegiatan") or []:
                if not isinstance(raw_sub, dict):
                    continue
                indicators = _norm_indicator_list(raw_sub.get("indikator"))
                sub = {
                    "kode": normalize_code(raw_sub.get("kode")),
                    "nama": norm_space(raw_sub.get("nama")),
                    "indikator": indicators,
                    "target": indicators[0]["target"] if indicators else norm_space(raw_sub.get("target")),
                    "satuan": indicators[0]["satuan"] if indicators else norm_space(raw_sub.get("satuan")),
                    "pagu": parse_pagu(raw_sub.get("pagu")),
                    "lokasi": norm_space(raw_sub.get("lokasi")),
                    "keterangan": norm_space(raw_sub.get("keterangan")),
                    "_sintetis": False,
                    "sumber": "llm",
                }
                kegiatan["subkegiatan"].append(sub)
            program["kegiatan"].append(kegiatan)
        programs.append(program)
    return {
        "opd": norm_space(data.get("opd")),
        "tahun": norm_space(data.get("tahun")),
        "judul": norm_space(data.get("judul")),
        "program": programs,
    }


def extract_document(text: str, master: MasterNomenklatur, filename: str = "") -> dict[str, Any]:
    """Fallback ekstraksi teks menggunakan LLM. Mengembalikan struktur dokumen."""
    if not is_enabled():
        return {"program": [], "opd": "", "tahun": "", "judul": "", "error": "LLM tidak aktif."}
    max_chars = int(os.environ.get("LLM_MAX_INPUT_CHARS", "60000"))
    excerpt = text[:max_chars]
    user_prompt = (
        PROMPT_EKSTRAKSI_USER.replace("{{FILENAME}}", filename or "(tanpa nama)")
        .replace("{{MASTER_RINGKAS}}", _master_excerpt(master, excerpt))
        .replace("{{TEKS}}", excerpt)
    )
    try:
        data, usage = _call_json(PROMPT_EKSTRAKSI_SISTEM, user_prompt, max_tokens=int(os.environ.get("LLM_EXTRACT_MAX_TOKENS", "8000")))
        normalized = _normalize_extraction(data)
        normalized["_meta"] = {"usage": usage, "terpotong": len(text) > max_chars, "model": usage.get("model")}
        return normalized
    except Exception as exc:  # pragma: no cover - bergantung layanan eksternal
        LOGGER.warning("Ekstraksi LLM gagal: %s", exc)
        return {"program": [], "opd": "", "tahun": "", "judul": "", "error": str(exc)}


# ----------------------------------------------------------------------
# Analisis
# ----------------------------------------------------------------------
def _compact_hierarchy(doc: dict[str, Any], max_sub: int = 160) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    sub_count = 0
    for program in doc.get("program", []):
        program_payload = {
            "level": "Program",
            "kode": program.get("kode", ""),
            "nama": program.get("nama", ""),
            "indikator": program.get("indikator", []),
            "status_nomenklatur": program.get("_status_nomenklatur", ""),
            "catatan_nomenklatur": program.get("_catatan_nomenklatur", ""),
            "kegiatan": [],
        }
        for kegiatan in program.get("kegiatan", []):
            kegiatan_payload = {
                "level": "Kegiatan",
                "kode": kegiatan.get("kode", ""),
                "nama": kegiatan.get("nama", ""),
                "indikator": kegiatan.get("indikator", []),
                "pagu": kegiatan.get("pagu"),
                "status_nomenklatur": kegiatan.get("_status_nomenklatur", ""),
                "catatan_nomenklatur": kegiatan.get("_catatan_nomenklatur", ""),
                "subkegiatan": [],
            }
            for sub in kegiatan.get("subkegiatan", []):
                if sub_count >= max_sub:
                    break
                kegiatan_payload["subkegiatan"].append(
                    {
                        "level": "Subkegiatan",
                        "kode": sub.get("kode", ""),
                        "nama": sub.get("nama", ""),
                        "indikator": sub.get("indikator", []),
                        "target": sub.get("target", ""),
                        "satuan": sub.get("satuan", ""),
                        "pagu": sub.get("pagu"),
                        "lokasi": sub.get("lokasi", ""),
                        "keterangan": sub.get("keterangan", ""),
                        "status_nomenklatur": sub.get("_status_nomenklatur", ""),
                        "catatan_nomenklatur": sub.get("_catatan_nomenklatur", ""),
                    }
                )
                sub_count += 1
            program_payload["kegiatan"].append(kegiatan_payload)
        result.append(program_payload)
        if sub_count >= max_sub:
            break
    return result


def _reference_excerpt(text: str, item_names: list[str], max_chars: int = 10000) -> str:
    if not text:
        return ""
    parts = [text[: min(len(text), 3500)]]
    lower = text.lower()
    for name in item_names:
        cleaned = re.sub(r"\s+", " ", str(name or "").strip())
        if len(cleaned) < 12:
            continue
        index = lower.find(cleaned.lower())
        if index < 0:
            continue
        start = max(0, index - 300)
        end = min(len(text), index + len(cleaned) + 500)
        snippet = text[start:end]
        if snippet not in parts:
            parts.append(snippet)
        if sum(len(part) for part in parts) >= max_chars:
            break
    return "\n...\n".join(parts)[:max_chars]


def _build_payload(
    doc: dict[str, Any],
    master: MasterNomenklatur,
    references: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> str:
    item_codes = []
    item_names = []
    for program in doc.get("program", []):
        item_codes.append(program.get("kode"))
        item_names.append(program.get("nama"))
        for kegiatan in program.get("kegiatan", []):
            item_codes.append(kegiatan.get("kode"))
            item_names.append(kegiatan.get("nama"))
            for sub in kegiatan.get("subkegiatan", []):
                item_codes.append(sub.get("kode"))
                item_names.append(sub.get("nama"))
    master_entries = master.compact_entries_for_codes(item_codes, limit=300)
    reference_payload = []
    for reference in references:
        reference_payload.append(
            {
                "nama": reference.get("nama", ""),
                "nama_file": reference.get("nama_file", ""),
                "cuplikan": _reference_excerpt(reference.get("teks", ""), item_names),
            }
        )
    payload = {
        "identitas": {
            "opd": (doc.get("opd") or {}).get("nama", ""),
            "kode_opd": (doc.get("opd") or {}).get("kode", ""),
            "tahun": doc.get("tahun", ""),
            "dokumen": doc.get("dokumen", {}),
        },
        "statistik": doc.get("statistik", {}),
        "catatan_ekstraksi": (doc.get("warnings") or [])[:10],
        "master": {
            "meta": master.meta,
            "jumlah": master.counts(),
            "status_contoh": master.is_sample,
        },
        "master_relevan": master_entries,
        "hierarki_renja": _compact_hierarchy(doc),
        "temuan_rule_based": [
            {
                "id": finding.get("id"),
                "level": finding.get("level"),
                "kategori": finding.get("kategori"),
                "jenis": finding.get("jenis"),
                "tingkat": finding.get("tingkat"),
                "kode": finding.get("kode"),
                "nama": finding.get("nama"),
                "pesan": finding.get("pesan"),
                "rekomendasi": finding.get("rekomendasi"),
            }
            for finding in findings[:80]
        ],
        "dokumen_acuan": reference_payload,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_alignment(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "acuan": norm_space(item.get("acuan")),
                "status": norm_space(item.get("status")),
                "catatan": norm_space(item.get("catatan")),
            }
        )
    return result


def analyze(
    doc: dict[str, Any],
    master: MasterNomenklatur,
    references: list[dict[str, Any]],
    rule_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Minta analisis tambahan dari LLM. Tidak pernah melempar error ke aplikasi."""
    if not is_enabled():
        return {"enabled": False, "findings": [], "error": "", "model": ""}
    payload = _build_payload(doc, master, references, rule_findings)
    max_chars = int(os.environ.get("LLM_MAX_PAYLOAD_CHARS", "120000"))
    if len(payload) > max_chars:
        payload = payload[:max_chars] + "\n...[payload dipotong]"
    user_prompt = PROMPT_ANALISIS_USER.replace("{{PAYLOAD}}", payload)
    try:
        data, usage = _call_json(PROMPT_ANALISIS_SISTEM, user_prompt)
        raw_findings = data.get("temuan") or data.get("findings") or []
        findings = []
        if isinstance(raw_findings, list):
            for raw in raw_findings[:60]:
                if not isinstance(raw, dict):
                    continue
                normalized = normalize_finding(raw, source="llm")
                if normalized.get("pesan"):
                    findings.append(normalized)
        recommendations = data.get("rekomendasi_umum") or []
        if not isinstance(recommendations, list):
            recommendations = [recommendations]
        recommendations = [norm_space(item) for item in recommendations if norm_space(item)][:12]
        return {
            "enabled": True,
            "model": usage.get("model", model_name()),
            "usage": usage,
            "findings": findings,
            "ringkasan": norm_space(data.get("ringkasan")),
            "kesimpulan": norm_space(data.get("kesimpulan")),
            "rekomendasi_umum": recommendations,
            "penyelarasan": _normalize_alignment(data.get("penyelarasan")),
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - bergantung layanan eksternal
        LOGGER.warning("Analisis LLM gagal: %s", exc)
        return {
            "enabled": True,
            "model": model_name(),
            "findings": [],
            "ringkasan": "",
            "kesimpulan": "",
            "rekomendasi_umum": [],
            "penyelarasan": [],
            "error": str(exc),
        }


def analysis_prompt_preview() -> str:
    """Tampilkan prompt siap pakai untuk dokumentasi/verifikasi."""
    return f"{PROMPT_ANALISIS_SISTEM}\n\n--- USER TEMPLATE ---\n\n{PROMPT_ANALISIS_USER}"
