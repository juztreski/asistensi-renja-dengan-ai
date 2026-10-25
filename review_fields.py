"""Definisi temuan, status review, dan perhitungan skor asistensi."""

from __future__ import annotations

import re
from typing import Any, Iterable

from master_nomenklatur import code_key, normalize_name, normalize_code

STATUS_TERBUKA = "Terbuka"
STATUS_DITERIMA = "Diterima"
STATUS_DIKOREKSI = "Dikoreksi"
STATUS_DIABAIKAN = "Diabaikan"

STATUS_OPTIONS = [
    {"value": STATUS_TERBUKA, "label": "Terbuka", "hint": "Belum ditinjau"},
    {"value": STATUS_DITERIMA, "label": "Diterima", "hint": "Temuan valid, perlu diperbaiki OPD"},
    {"value": STATUS_DIKOREKSI, "label": "Dikoreksi", "hint": "Sudah diperbaiki/dikoreksi"},
    {"value": STATUS_DIABAIKAN, "label": "Diabaikan", "hint": "Tidak relevan / positif palsu"},
]
STATUS_VALUES = tuple(option["value"] for option in STATUS_OPTIONS)

SEVERITY_ORDER = {"Kritis": 0, "Sedang": 1, "Saran": 2}
SEVERITY_LABELS = {
    "Kritis": "Kritis",
    "Sedang": "Sedang",
    "Saran": "Saran",
}
SEVERITY_COLORS = {"Kritis": "#b42318", "Sedang": "#b54708", "Saran": "#175cd3"}

CATEGORY_ORDER = [
    "Identitas",
    "Nomenklatur",
    "Hierarki",
    "Kelengkapan",
    "Indikator",
    "Duplikasi",
    "Penyelarasan",
]
CATEGORY_CAPS = {
    "Identitas": 5,
    "Nomenklatur": 35,
    "Hierarki": 10,
    "Kelengkapan": 20,
    "Indikator": 15,
    "Duplikasi": 5,
    "Penyelarasan": 10,
}
CATEGORY_ALIASES = {
    "NOMENKLATUR": "Nomenklatur",
    "KODE": "Nomenklatur",
    "HIERARKI": "Hierarki",
    "STRUKTUR": "Hierarki",
    "KELENGKAPAN": "Kelengkapan",
    "DATA": "Kelengkapan",
    "INDIKATOR": "Indikator",
    "SMART": "Indikator",
    "DUPLIKASI": "Duplikasi",
    "PENYELARASAN": "Penyelarasan",
    "SINKRONISASI": "Penyelarasan",
    "IDENTITAS": "Identitas",
    "DOKUMEN": "Identitas",
}
PENALTIES = {"Kritis": 10, "Sedang": 4, "Saran": 1}

ACTIVE_STATUSES = {STATUS_TERBUKA, STATUS_DITERIMA}
LEVEL_ALIASES = {
    "PROGRAM": "Program",
    "KEGIATAN": "Kegiatan",
    "SUBKEGIATAN": "Subkegiatan",
    "SUB KEGIATAN": "Subkegiatan",
    "SUB_KEGIATAN": "Subkegiatan",
    "DOKUMEN": "Dokumen",
    "RENJA": "Dokumen",
}
SEVERITY_ALIASES = {
    "KRITIS": "Kritis",
    "CRITICAL": "Kritis",
    "HIGH": "Kritis",
    "TINGGI": "Kritis",
    "MAYOR": "Kritis",
    "SEDANG": "Sedang",
    "WARNING": "Sedang",
    "MEDIUM": "Sedang",
    "MINOR": "Sedang",
    "SARAN": "Saran",
    "INFO": "Saran",
    "LOW": "Saran",
    "RENDAH": "Saran",
}

TEXT_LIMIT = 4000


def normalize_severity(value: Any, default: str = "Saran") -> str:
    text = str(value or "").strip().upper()
    return SEVERITY_ALIASES.get(text, default if default in SEVERITY_LABELS else "Saran")


def normalize_category(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_")
    for key, category in CATEGORY_ALIASES.items():
        if key in text:
            return category
    return "Kelengkapan"


def normalize_level(value: Any, default: str = "Dokumen") -> str:
    text = str(value or "").strip().upper().replace("-", "_")
    return LEVEL_ALIASES.get(text, default if default in LEVEL_ALIASES.values() else "Dokumen")


def normalize_status(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in STATUS_VALUES else STATUS_TERBUKA


def _trim(value: Any, limit: int = TEXT_LIMIT) -> str:
    return str(value or "").strip()[:limit]


def normalize_finding(raw: dict[str, Any], source: str = "rule") -> dict[str, Any]:
    """Normalisasi temuan dari rule engine maupun LLM."""
    raw = raw or {}
    category = normalize_category(raw.get("kategori") or raw.get("kategori_temuan"))
    level = normalize_level(raw.get("level") or raw.get("unit"))
    severity = normalize_severity(raw.get("tingkat") or raw.get("severity"))
    jenis = re.sub(r"[^A-Z0-9_]+", "_", str(raw.get("jenis") or "TEMUAN_LAIN").upper()).strip("_") or "TEMUAN_LAIN"
    kode = normalize_code(raw.get("kode"))
    nama = _trim(raw.get("nama"), 500)
    pesan = _trim(raw.get("pesan") or raw.get("temuan") or raw.get("uraian"))
    rekomendasi = _trim(raw.get("rekomendasi") or raw.get("saran"))
    acuan = _trim(raw.get("acuan"), 500) or "Permendagri 90/2019 dan pemutakhirannya"
    path = _trim(raw.get("path"), 1000)
    if not pesan:
        pesan = "Temuan tanpa uraian."
    if not rekomendasi:
        rekomendasi = "Verifikasi dan perbaiki sesuai ketentuan serta dokumen acuan."
    return {
        "id": _trim(raw.get("id"), 20),
        "kategori": category,
        "level": level,
        "jenis": jenis,
        "tingkat": severity,
        "kode": kode,
        "nama": nama,
        "pesan": pesan,
        "rekomendasi": rekomendasi,
        "acuan": acuan,
        "path": path,
        "status": normalize_status(raw.get("status")),
        "catatan": _trim(raw.get("catatan") or raw.get("koreksi"), 2000),
        "sumber": source if source in {"rule", "llm"} else _trim(raw.get("sumber"), 20) or "rule",
    }


def finding_key(finding: dict[str, Any]) -> tuple:
    return (
        finding.get("jenis", ""),
        finding.get("level", ""),
        code_key(finding.get("kode", "")),
        normalize_name(finding.get("nama", "")),
        normalize_name(finding.get("pesan", ""))[:180],
    )


def dedupe_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for finding in findings:
        key = finding_key(finding)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def assign_ids(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"F{index:03d}"
    return findings


def sort_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(finding: dict[str, Any]) -> tuple:
        return (
            SEVERITY_ORDER.get(finding.get("tingkat", "Saran"), 9),
            CATEGORY_ORDER.index(finding["kategori"]) if finding.get("kategori") in CATEGORY_ORDER else 99,
            finding.get("kode", ""),
            finding.get("id", ""),
        )

    return sorted(findings, key=key)


def category_penalty(findings: Iterable[dict[str, Any]], category: str, include_inactive: bool = False) -> int:
    total = 0
    for finding in findings:
        if finding.get("kategori") != category:
            continue
        if not include_inactive and finding.get("status") not in ACTIVE_STATUSES:
            continue
        total += PENALTIES.get(finding.get("tingkat", "Saran"), 1)
    return min(total, CATEGORY_CAPS.get(category, 10))


def compute_score(findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Hitung skor 0-100 dengan batas penalti per kategori."""
    findings = list(findings)
    penalties: dict[str, int] = {}
    total_penalty = 0
    for category in CATEGORY_ORDER:
        penalty = category_penalty(findings, category)
        penalties[category] = penalty
        total_penalty += penalty
    score = max(0, min(100, 100 - total_penalty))
    return {
        "score": score,
        "predikat": predikat(score),
        "penalty": total_penalty,
        "penalty_per_kategori": penalties,
    }


def predikat(score: int) -> str:
    if score >= 90:
        return "Sangat Baik"
    if score >= 75:
        return "Baik"
    if score >= 60:
        return "Cukup"
    return "Perlu Perbaikan"


def predikat_class(score: int) -> str:
    if score >= 90:
        return "very-good"
    if score >= 75:
        return "good"
    if score >= 60:
        return "fair"
    return "poor"


def summarize(findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    findings = list(findings)
    by_severity = {level: 0 for level in SEVERITY_ORDER}
    by_status = {status: 0 for status in STATUS_VALUES}
    by_category = {category: 0 for category in CATEGORY_ORDER}
    for finding in findings:
        by_severity[finding.get("tingkat", "Saran")] = by_severity.get(finding.get("tingkat", "Saran"), 0) + 1
        by_status[finding.get("status", STATUS_TERBUKA)] = by_status.get(finding.get("status", STATUS_TERBUKA), 0) + 1
        by_category[finding.get("kategori", "Kelengkapan")] = (
            by_category.get(finding.get("kategori", "Kelengkapan"), 0) + 1
        )
    return {
        "total": len(findings),
        "by_severity": by_severity,
        "by_status": by_status,
        "by_category": by_category,
        "active": sum(1 for finding in findings if finding.get("status") in ACTIVE_STATUSES),
        "resolved": len(findings) - sum(1 for finding in findings if finding.get("status") in ACTIVE_STATUSES),
    }


def apply_review(findings: list[dict[str, Any]], form: Any) -> tuple[list[dict[str, Any]], int]:
    """Terapkan status dan catatan koreksi dari form review.

    ``form`` dapat berupa ``request.form`` (MultiDict) atau dict biasa.
    """
    updated: list[dict[str, Any]] = []
    changed = 0
    for index, original in enumerate(findings):
        finding = dict(original)
        finding_id = finding.get("id") or f"F{index + 1:03d}"
        status_raw = _form_get(form, f"status_{finding_id}")
        note_raw = _form_get(form, f"catatan_{finding_id}")
        if status_raw is None:
            status_raw = _form_get(form, f"status_{index}")
        if note_raw is None:
            note_raw = _form_get(form, f"catatan_{index}")
        status = normalize_status(status_raw) if status_raw not in (None, "") else finding.get("status", STATUS_TERBUKA)
        note = _trim(note_raw if note_raw is not None else finding.get("catatan", ""), 2000)
        if status != finding.get("status") or note != finding.get("catatan", ""):
            changed += 1
        finding["status"] = status
        finding["catatan"] = note
        updated.append(finding)
    return updated, changed


def _form_get(form: Any, key: str) -> Any:
    if form is None:
        return None
    try:
        return form.get(key)
    except AttributeError:
        return None


def format_rupiah(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number == 0:
        return "Rp 0"
    formatted = f"{number:,.0f}".replace(",", ".")
    return f"Rp {formatted}"


def format_percent(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"
