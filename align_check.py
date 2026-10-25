"""Analisis kesesuaian Renja: nomenklatur, hierarki, kelengkapan, indikator,
duplikasi, dan penyelarasan dengan dokumen acuan.

Mesin ini bersifat rule-based dan deterministik. Temuan dari LLM (bila aktif)
dapat digabungkan melalui parameter ``llm_result``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from master_nomenklatur import (
    LEVEL_KEGIATAN,
    LEVEL_PROGRAM,
    LEVEL_SUBKEGIATAN,
    MasterNomenklatur,
    code_key,
    normalize_name,
)
from extract_renja import document_statistics
from review_fields import (
    assign_ids,
    compute_score,
    dedupe_findings,
    normalize_finding,
    predikat,
    sort_findings,
    summarize,
)

REQUIRED_LEVEL_LABEL = "Permendagri 90/2019 dan pemutakhirannya"
INDICATOR_ACUAN = "Prinsip indikator kinerja SMART dan Permendagri 86/2017"
PAGU_ACUAN = "Permendagri 90/2019; keselarasan pagu indikatif Renja"
ALIGNMENT_ACUAN = "Permendagri 86/2017; RPJMD, Renstra OPD, dan RKPD"

VAGUE_INDICATOR_WORDS = {
    "OPTIMAL",
    "OPTIMALNYA",
    "BAIK",
    "LEBIH BAIK",
    "MENINGKAT",
    "MENINGKATNYA",
    "TERWUJUD",
    "TERWUJUDNYA",
    "TERPENUHI",
    "TERPENUHINYA",
    "TERLAKSANA",
    "TERLAKSANANYA",
    "SESUAI",
    "MEMADAI",
    "SEBAGAIMANA MESTINYA",
    "MAKSIMAL",
    "EFEKTIF",
    "EFISIEN",
}
VAGUE_TARGETS = {"", "-", "--", "TBD", "N/A", "NA", "NIHIL", "SESUAI", "MAKSIMAL", "OPTIMAL", "...", "……"}
STANDARD_UNITS = {
    "ORANG",
    "DOKUMEN",
    "PERSEN",
    "%",
    "UNIT",
    "KALI",
    "KEGIATAN",
    "LAPORAN",
    "PAKET",
    "BULAN",
    "TAHUN",
    "HARI",
    "JAM",
    "METER",
    "M",
    "M2",
    "M3",
    "HEKTAR",
    "HA",
    "TON",
    "KG",
    "RUPIAH",
    "INDEKS",
    "SKOR",
    "NASKAH",
    "REKOMENDASI",
    "SOP",
    "REGULASI",
    "PRODUK",
    "UMKM",
    "RESPONDEN",
    "SISWA",
    "GURU",
    "TENAGA KESEHATAN",
    "FASILITAS",
    "SARANA",
    "PRASARANA",
    "SISTEM",
    "APLIKASI",
    "DATA",
    "INFORMASI",
    "JARINGAN",
    "KAWASAN",
    "DAERAH",
    "WILAYAH",
    "TITIK",
    "LOKASI",
    "DESA",
    "KELURAHAN",
    "KECAMATAN",
    "OPD",
    "SEKOLAH",
    "PUSKESMAS",
    "RUMAH",
    "JALAN",
    "JEMBATAN",
    "KELOMPOK",
    "BUAH",
    "BATANG",
    "BIDANG",
    "PIMPINAN",
    "PESERTA",
    "KLIEN",
    "PASIEN",
    "DOKTER",
    "BED",
    "KAMAR",
    "PERSIL",
    "BIDANG TANAH",
    "SERTIFIKAT",
    "KEPALA KELUARGA",
    "JIWA",
    "KENDARAAN",
    "MESIN",
    "PERALATAN",
    "MEREK",
    "JENIS",
}
NUMERIC_TARGET_RE = re.compile(r"^[\d.,]+$")
REFERENCE_YEAR_RE = re.compile(r"TAHUN\s+(?:ANGGARAN\s+)?(20\d{2})")
STOPWORDS = {
    "DAN",
    "ATAU",
    "YANG",
    "UNTUK",
    "DENGAN",
    "PADA",
    "DI",
    "KE",
    "DARI",
    "DALAM",
    "SEBAGAI",
    "SECARA",
    "SERTA",
    "JUMLAH",
    "PERSENTASE",
    "TINGKAT",
    "KUALITAS",
    "KAPASITAS",
    "PEMERINTAH",
    "DAERAH",
    "KABUPATEN",
    "KOTA",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _path(program: dict | None, kegiatan: dict | None = None, sub: dict | None = None) -> str:
    parts: list[str] = []
    if program:
        parts.append(_text(program.get("kode")))
    if kegiatan:
        parts.append(_text(kegiatan.get("kode")))
    if sub:
        parts.append(_text(sub.get("kode")))
    return " > ".join(part for part in parts if part)


def _display(item: dict) -> str:
    kode = _text(item.get("kode"))
    nama = _text(item.get("nama"))
    if kode and nama:
        return f"{kode} {nama}"
    return kode or nama or "(tanpa nama)"


def _add(
    findings: list[dict[str, Any]],
    *,
    kategori: str,
    level: str,
    jenis: str,
    tingkat: str,
    pesan: str,
    rekomendasi: str,
    acuan: str = REQUIRED_LEVEL_LABEL,
    item: dict | None = None,
    path: str = "",
) -> None:
    item = item or {}
    findings.append(
        normalize_finding(
            {
                "kategori": kategori,
                "level": level,
                "jenis": jenis,
                "tingkat": tingkat,
                "kode": item.get("kode", ""),
                "nama": item.get("nama", ""),
                "pesan": pesan,
                "rekomendasi": rekomendasi,
                "acuan": acuan,
                "path": path,
            },
            source="rule",
        )
    )


def _iter_items(doc: dict[str, Any]):
    for program in doc.get("program", []):
        yield LEVEL_PROGRAM, program, None, None, program
        for kegiatan in program.get("kegiatan", []):
            yield LEVEL_KEGIATAN, kegiatan, program, None, program
            for sub in kegiatan.get("subkegiatan", []):
                yield LEVEL_SUBKEGIATAN, sub, program, kegiatan, program


def _indicator_list(item: dict) -> list[dict[str, Any]]:
    indicators = item.get("indikator")
    if isinstance(indicators, list) and indicators:
        return [indicator for indicator in indicators if isinstance(indicator, dict)]
    if item.get("target") or item.get("satuan"):
        return [{"nama": "", "target": item.get("target", ""), "satuan": item.get("satuan", "")}]
    return []


def _is_numeric_target(value: str) -> bool:
    cleaned = value.replace(" ", "").rstrip("%")
    return bool(NUMERIC_TARGET_RE.match(cleaned))


def _numeric_target(value: str) -> float | None:
    if not _is_numeric_target(value):
        return None
    cleaned = value.replace(" ", "").rstrip("%").replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _unit_norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9%]+", " ", value.upper()).strip()


# ----------------------------------------------------------------------
# Pemeriksaan identitas dan kelengkapan struktur
# ----------------------------------------------------------------------
def check_identity(doc: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    opd = _text((doc.get("opd") or {}).get("nama"))
    tahun = _text(doc.get("tahun"))
    if not opd:
        _add(
            findings,
            kategori="Identitas",
            level="Dokumen",
            jenis="OPD_TIDAK_TERDETEKSI",
            tingkat="Sedang",
            pesan="Nama OPD tidak terdeteksi pada dokumen.",
            rekomendasi="Lengkapi identitas OPD pada halaman judul dan isi formulir asistensi.",
            acuan="Identitas dokumen Renja",
        )
    if not tahun or not re.fullmatch(r"20\d{2}", tahun):
        _add(
            findings,
            kategori="Identitas",
            level="Dokumen",
            jenis="TAHUN_TIDAK_TERDETEKSI",
            tingkat="Sedang",
            pesan="Tahun anggaran dokumen tidak terdeteksi dengan jelas.",
            rekomendasi="Cantumkan tahun anggaran Renja pada halaman judul dan formulir asistensi.",
            acuan="Identitas dokumen Renja",
        )
    if not doc.get("program"):
        _add(
            findings,
            kategori="Kelengkapan",
            level="Dokumen",
            jenis="PROGRAM_TIDAK_TEREKSTRAK",
            tingkat="Kritis",
            pesan="Tidak ada Program yang berhasil diekstrak dari dokumen.",
            rekomendasi=(
                "Pastikan dokumen memuat tabel Program/Kegiatan/Subkegiatan yang dapat dibaca, "
                "atau gunakan fallback LLM/unggah XLSX/DOCX asli."
            ),
            acuan="Struktur Renja sesuai Permendagri 86/2017",
        )


def check_structure(doc: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    programs = doc.get("program", [])
    for program in programs:
        path = _path(program)
        if program.get("_sintetis"):
            _add(
                findings,
                kategori="Kelengkapan",
                level=LEVEL_PROGRAM,
                jenis="PROGRAM_INDUK_TIDAK_ADA",
                tingkat="Kritis",
                pesan=f"Program induk untuk {path or 'kegiatan/subkegiatan'} tidak ditemukan pada dokumen.",
                rekomendasi="Lengkapi baris Program beserta kode dan nama resminya.",
                item=program,
                path=path,
            )
        if not program.get("kegiatan"):
            _add(
                findings,
                kategori="Kelengkapan",
                level=LEVEL_PROGRAM,
                jenis="PROGRAM_TANPA_KEGIATAN",
                tingkat="Sedang",
                pesan=f"Program {_display(program)} belum memiliki Kegiatan.",
                rekomendasi="Uraikan kegiatan dan subkegiatan yang mendukung program tersebut.",
                item=program,
                path=path,
            )
        for kegiatan in program.get("kegiatan", []):
            kpath = _path(program, kegiatan)
            if kegiatan.get("_sintetis"):
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=LEVEL_KEGIATAN,
                    jenis="KEGIATAN_INDUK_TIDAK_ADA",
                    tingkat="Kritis",
                    pesan=f"Kegiatan induk untuk {kpath or 'subkegiatan'} tidak ditemukan pada dokumen.",
                    rekomendasi="Lengkapi baris Kegiatan beserta kode dan nama resminya.",
                    item=kegiatan,
                    path=kpath,
                )
            if not kegiatan.get("subkegiatan"):
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=LEVEL_KEGIATAN,
                    jenis="KEGIATAN_TANPA_SUBKEGIATAN",
                    tingkat="Sedang",
                    pesan=f"Kegiatan {_display(kegiatan)} belum memiliki Subkegiatan.",
                    rekomendasi="Rinci kegiatan menjadi subkegiatan sesuai longlist nomenklatur.",
                    item=kegiatan,
                    path=kpath,
                )
            elif not _text(kegiatan.get("nama")):
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=LEVEL_KEGIATAN,
                    jenis="NAMA_KEGIATAN_KOSONG",
                    tingkat="Kritis",
                    pesan=f"Kegiatan {kegiatan.get('kode') or '(tanpa kode)'} belum memiliki nama.",
                    rekomendasi="Lengkapi nama kegiatan sesuai master nomenklatur.",
                    item=kegiatan,
                    path=kpath,
                )
            for sub in kegiatan.get("subkegiatan", []):
                spath = _path(program, kegiatan, sub)
                if not _text(sub.get("nama")):
                    _add(
                        findings,
                        kategori="Kelengkapan",
                        level=LEVEL_SUBKEGIATAN,
                        jenis="NAMA_SUBKEGIATAN_KOSONG",
                        tingkat="Kritis",
                        pesan=f"Subkegiatan {sub.get('kode') or '(tanpa kode)'} belum memiliki nama.",
                        rekomendasi="Lengkapi nama subkegiatan sesuai master nomenklatur.",
                        item=sub,
                        path=spath,
                    )
                if not _text(sub.get("kode")):
                    _add(
                        findings,
                        kategori="Kelengkapan",
                        level=LEVEL_SUBKEGIATAN,
                        jenis="KODE_SUBKEGIATAN_KOSONG",
                        tingkat="Kritis",
                        pesan=f"Subkegiatan \"{_text(sub.get('nama'))[:90]}\" belum memiliki kode.",
                        rekomendasi="Lengkapi kode subkegiatan sesuai longlist resmi.",
                        item=sub,
                        path=spath,
                    )


# ----------------------------------------------------------------------
# Nomenklatur dan hierarki
# ----------------------------------------------------------------------
def check_nomenclature(
    doc: dict[str, Any],
    master: MasterNomenklatur,
    findings: list[dict[str, Any]],
) -> None:
    for level, item, program, kegiatan, _root in _iter_items(doc):
        path = _path(program, kegiatan, item if level == LEVEL_SUBKEGIATAN else None)
        if level == LEVEL_KEGIATAN:
            path = _path(program, item)
        if level == LEVEL_PROGRAM:
            path = _path(item)
        code = _text(item.get("kode"))
        name = _text(item.get("nama"))
        if not code and not name:
            continue
        resolution = master.resolve(code, name, level=level)
        status = "Sesuai"
        note = ""

        if resolution["status"] != "sesuai":
            status = "Tidak Sesuai" if resolution.get("tingkat") == "Kritis" else "Perlu Perbaikan"
            note = resolution.get("pesan", "")
            jenis_map = {
                "nama_tidak_sesuai": "NOMENKLATUR_TIDAK_SESUAI",
                "nama_tidak_sesuai_ringan": "NOMENKLATUR_PERLU_RAPI",
                "kode_tidak_sesuai": "KODE_TIDAK_SESUAI",
                "kode_dan_nama_tidak_sesuai": "NOMENKLATUR_TIDAK_TERDAFTAR",
                "kode_tidak_ditemukan": "KODE_TIDAK_DITEMUKAN",
                "tidak_terdaftar": "NOMENKLATUR_TIDAK_TERDAFTAR",
                "kode_kosong": "KODE_KOSONG",
                "nama_kosong": "NAMA_KOSONG",
                "level_tidak_sesuai": "LEVEL_TIDAK_SESUAI",
                "data_kosong": "DATA_KOSONG",
            }
            rekomendasi = resolution.get("rekomendasi", "")
            candidates = resolution.get("kandidat") or []
            if candidates:
                candidate_text = "; ".join(
                    f"{candidate.get('kode')} - {candidate.get('nama')} (skor {candidate.get('skor', 0):.2f})"
                    for candidate in candidates[:3]
                )
                rekomendasi = f"{rekomendasi} Kandidat master: {candidate_text}."
            _add(
                findings,
                kategori="Nomenklatur",
                level=level,
                jenis=jenis_map.get(resolution["status"], "NOMENKLATUR_TIDAK_SESUAI"),
                tingkat=resolution.get("tingkat") or "Kritis",
                pesan=resolution.get("pesan", ""),
                rekomendasi=rekomendasi,
                acuan=resolution.get("acuan", REQUIRED_LEVEL_LABEL),
                item=item,
                path=path,
            )
            if resolution.get("tingkat") == "Kritis":
                status = "Tidak Sesuai"

        # Validasi hierarki kode terhadap master.
        hierarchy = master.validate_hierarchy(code, expected_level=level) if code else {"issues": []}
        for issue in hierarchy.get("issues", []):
            if issue.get("jenis") in {"KODE_TIDAK_DITEMUKAN", "LEVEL_TIDAK_SESUAI"} and resolution["status"] != "sesuai":
                continue
            _add(
                findings,
                kategori="Hierarki",
                level=level,
                jenis=issue.get("jenis", "HIERARKI_TIDAK_KONSISTEN"),
                tingkat="Kritis",
                pesan=issue.get("pesan", ""),
                rekomendasi=issue.get("rekomendasi", "Perbaiki susunan hierarki kode."),
                acuan="Struktur kode Permendagri 90/2019",
                item=item,
                path=path,
            )
            status = "Tidak Sesuai"
            if note:
                note = f"{note} {issue.get('pesan', '')}".strip()
            else:
                note = issue.get("pesan", "")

        # Validasi konsistensi prefiks kode dengan induk pada dokumen.
        if level == LEVEL_KEGIATAN and code and program:
            parent_key = code_key(program.get("kode"))
            child_key = code_key(code)
            if parent_key and child_key and child_key[:2] != parent_key[:2]:
                _add(
                    findings,
                    kategori="Hierarki",
                    level=LEVEL_KEGIATAN,
                    jenis="HIERARKI_KODE_TIDAK_KONSISTEN",
                    tingkat="Kritis",
                    pesan=(
                        f"Kode kegiatan {code} tidak berada dalam prefiks program "
                        f"{program.get('kode')}."
                    ),
                    rekomendasi="Perbaiki kode atau tempatkan kegiatan pada program induk yang sesuai.",
                    acuan="Struktur kode Permendagri 90/2019",
                    item=item,
                    path=path,
                )
                status = "Tidak Sesuai"
        if level == LEVEL_SUBKEGIATAN and code and kegiatan:
            child_key = code_key(code)
            parent_key = code_key(kegiatan.get("kode"))
            if parent_key and child_key and child_key[:3] != parent_key[:3]:
                _add(
                    findings,
                    kategori="Hierarki",
                    level=LEVEL_SUBKEGIATAN,
                    jenis="HIERARKI_KODE_TIDAK_KONSISTEN",
                    tingkat="Kritis",
                    pesan=(
                        f"Kode subkegiatan {code} tidak berada dalam prefiks kegiatan "
                        f"{kegiatan.get('kode')}."
                    ),
                    rekomendasi="Perbaiki kode atau tempatkan subkegiatan pada kegiatan induk yang sesuai.",
                    acuan="Struktur kode Permendagri 90/2019",
                    item=item,
                    path=path,
                )
                status = "Tidak Sesuai"
            if program and code and code_key(code)[:2] != code_key(program.get("kode"))[:2]:
                _add(
                    findings,
                    kategori="Hierarki",
                    level=LEVEL_SUBKEGIATAN,
                    jenis="HIERARKI_PROGRAM_TIDAK_KONSISTEN",
                    tingkat="Kritis",
                    pesan=(
                        f"Kode subkegiatan {code} tidak berada dalam prefiks program "
                        f"{program.get('kode')}."
                    ),
                    rekomendasi="Perbaiki kode atau tempatkan subkegiatan pada program induk yang sesuai.",
                    acuan="Struktur kode Permendagri 90/2019",
                    item=item,
                    path=path,
                )
                status = "Tidak Sesuai"

        item["_status_nomenklatur"] = status
        item["_catatan_nomenklatur"] = note


def check_master_availability(master: MasterNomenklatur, findings: list[dict[str, Any]]) -> bool:
    counts = master.counts()
    if counts["program"] == 0 and counts["kegiatan"] == 0 and counts["subkegiatan"] == 0:
        _add(
            findings,
            kategori="Nomenklatur",
            level="Dokumen",
            jenis="MASTER_NOMENKLATUR_KOSONG",
            tingkat="Kritis",
            pesan="Master nomenklatur belum tersedia atau kosong.",
            rekomendasi="Unggah master JSON/CSV sesuai template sebelum melakukan asistensi.",
            acuan="Permendagri 90/2019 dan pemutakhirannya",
        )
        return False
    return True


# ----------------------------------------------------------------------
# Indikator, target, satuan
# ----------------------------------------------------------------------
def check_indicators(doc: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    for level, item, program, kegiatan, _root in _iter_items(doc):
        path = _path(program, kegiatan, item if level == LEVEL_SUBKEGIATAN else None)
        if level == LEVEL_KEGIATAN:
            path = _path(program, item)
        if level == LEVEL_PROGRAM:
            path = _path(item)
        indicators = _indicator_list(item)
        if not indicators:
            severity = "Sedang" if level in (LEVEL_KEGIATAN, LEVEL_SUBKEGIATAN) else "Saran"
            _add(
                findings,
                kategori="Indikator",
                level=level,
                jenis="INDIKATOR_KOSONG",
                tingkat=severity,
                pesan=f"{level} {_display(item)} belum memiliki indikator kinerja.",
                rekomendasi="Rumuskan indikator yang spesifik, terukur, dapat dicapai, relevan, dan berbatas waktu.",
                acuan=INDICATOR_ACUAN,
                item=item,
                path=path,
            )
            continue

        seen_names: set[str] = set()
        for indicator_index, indicator in enumerate(indicators, start=1):
            name = _text(indicator.get("nama"))
            target = _text(indicator.get("target"))
            satuan = _text(indicator.get("satuan"))
            norm_name = normalize_name(name)
            label = name or f"indikator ke-{indicator_index}"

            if not name:
                _add(
                    findings,
                    kategori="Indikator",
                    level=level,
                    jenis="NAMA_INDIKATOR_KOSONG",
                    tingkat="Sedang",
                    pesan=f"{level} {_display(item)} memiliki target/satuan tanpa nama indikator.",
                    rekomendasi="Lengkapi nama indikator kinerja yang akan diukur.",
                    acuan=INDICATOR_ACUAN,
                    item=item,
                    path=path,
                )
            else:
                if len(norm_name) < 8:
                    _add(
                        findings,
                        kategori="Indikator",
                        level=level,
                        jenis="INDIKATOR_TERLALU_UMUM",
                        tingkat="Saran",
                        pesan=f"Indikator \"{name}\" terlalu singkat/umum untuk mengukur kinerja.",
                        rekomendasi="Gunakan indikator yang menggambarkan output/outcome secara spesifik.",
                        acuan=INDICATOR_ACUAN,
                        item=item,
                        path=path,
                    )
                vague = [word for word in VAGUE_INDICATOR_WORDS if words_contained(word, norm_name)]
                if vague:
                    _add(
                        findings,
                        kategori="Indikator",
                        level=level,
                        jenis="INDIKATOR_BELUM_TERUKUR",
                        tingkat="Saran",
                        pesan=(
                            f"Indikator \"{name}\" mengandung kata kurang terukur "
                            f"({', '.join(sorted(vague)[:3])})."
                        ),
                        rekomendasi="Tambahkan ukuran kuantitatif dan satuan yang jelas, misalnya persentase atau jumlah.",
                        acuan=INDICATOR_ACUAN,
                        item=item,
                        path=path,
                    )
                if norm_name in seen_names:
                    _add(
                        findings,
                        kategori="Duplikasi",
                        level=level,
                        jenis="INDIKATOR_DUPLIKAT",
                        tingkat="Sedang",
                        pesan=f"Indikator \"{name}\" tercantum lebih dari sekali pada {level.lower()} yang sama.",
                        rekomendasi="Hapus atau bedakan indikator yang duplikat.",
                        acuan=INDICATOR_ACUAN,
                        item=item,
                        path=path,
                    )
                seen_names.add(norm_name)

            if not target:
                _add(
                    findings,
                    kategori="Indikator",
                    level=level,
                    jenis="TARGET_KOSONG",
                    tingkat="Sedang",
                    pesan=f"Indikator \"{label}\" pada {level.lower()} {_display(item)} belum memiliki target.",
                    rekomendasi="Isi target kinerja sesuai kewenangan dan kemampuan anggaran.",
                    acuan=INDICATOR_ACUAN,
                    item=item,
                    path=path,
                )
            else:
                upper_target = target.upper().strip()
                if upper_target in VAGUE_TARGETS:
                    _add(
                        findings,
                        kategori="Indikator",
                        level=level,
                        jenis="TARGET_TIDAK_JELAS",
                        tingkat="Sedang",
                        pesan=f"Target \"{target}\" pada indikator \"{label}\" belum terukur.",
                        rekomendasi="Ganti dengan target angka dan satuan yang jelas.",
                        acuan=INDICATOR_ACUAN,
                        item=item,
                        path=path,
                    )
                else:
                    number = _numeric_target(target)
                    if number is None:
                        _add(
                            findings,
                            kategori="Indikator",
                            level=level,
                            jenis="TARGET_NON_NUMERIK",
                            tingkat="Saran",
                            pesan=f"Target \"{target}\" pada indikator \"{label}\" bukan angka tunggal.",
                            rekomendasi="Gunakan angka target yang jelas; rincian dapat diletakkan pada keterangan.",
                            acuan=INDICATOR_ACUAN,
                            item=item,
                            path=path,
                        )
                    elif number < 0:
                        _add(
                            findings,
                            kategori="Indikator",
                            level=level,
                            jenis="TARGET_NEGATIF",
                            tingkat="Kritis",
                            pesan=f"Target indikator \"{label}\" bernilai negatif.",
                            rekomendasi="Perbaiki target menjadi angka yang wajar dan tidak negatif.",
                            acuan=INDICATOR_ACUAN,
                            item=item,
                            path=path,
                        )
                    elif number == 0:
                        _add(
                            findings,
                            kategori="Indikator",
                            level=level,
                            jenis="TARGET_NOL",
                            tingkat="Saran",
                            pesan=f"Target indikator \"{label}\" bernilai nol.",
                            rekomendasi="Pastikan target nol memiliki justifikasi; jika tidak, isi target yang realistis.",
                            acuan=INDICATOR_ACUAN,
                            item=item,
                            path=path,
                        )
                    elif _unit_norm(satuan) in {"PERSEN", "%"} and number > 100:
                        _add(
                            findings,
                            kategori="Indikator",
                            level=level,
                            jenis="TARGET_PERSENTASE_TIDAK_WAJAR",
                            tingkat="Sedang",
                            pesan=f"Target persentase {target}% pada indikator \"{label}\" melebihi 100%.",
                            rekomendasi="Periksa kembali target dan satuan yang digunakan.",
                            acuan=INDICATOR_ACUAN,
                            item=item,
                            path=path,
                        )

            if not satuan:
                _add(
                    findings,
                    kategori="Indikator",
                    level=level,
                    jenis="SATUAN_KOSONG",
                    tingkat="Sedang",
                    pesan=f"Indikator \"{label}\" pada {level.lower()} {_display(item)} belum memiliki satuan.",
                    rekomendasi="Lengkapi satuan target (mis. Orang, Dokumen, Persen, Unit, Kali).",
                    acuan=INDICATOR_ACUAN,
                    item=item,
                    path=path,
                )
            else:
                unit_norm = _unit_norm(satuan)
                if unit_norm and unit_norm not in STANDARD_UNITS and re.search(r"\d", unit_norm):
                    _add(
                        findings,
                        kategori="Indikator",
                        level=level,
                        jenis="SATUAN_TIDAK_STANDAR",
                        tingkat="Saran",
                        pesan=f"Satuan \"{satuan}\" pada indikator \"{label}\" tidak baku/mengandung angka.",
                        rekomendasi="Gunakan satuan baku yang ringkas, seperti Orang, Dokumen, Persen, Unit, atau Kali.",
                        acuan=INDICATOR_ACUAN,
                        item=item,
                        path=path,
                    )


def words_contained(phrase: str, text_norm: str) -> bool:
    pattern = r"(?:^|\s)" + re.escape(phrase) + r"(?:\s|$)"
    return bool(re.search(pattern, text_norm))


# ----------------------------------------------------------------------
# Pagu
# ----------------------------------------------------------------------
def check_pagu(doc: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    for level, item, program, kegiatan, _root in _iter_items(doc):
        path = _path(program, kegiatan, item if level == LEVEL_SUBKEGIATAN else None)
        if level == LEVEL_KEGIATAN:
            path = _path(program, item)
        if level == LEVEL_PROGRAM:
            path = _path(item)
        pagu = item.get("pagu")
        raw = _text(item.get("pagu_raw"))
        if level == LEVEL_SUBKEGIATAN:
            if pagu is None and not raw:
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=level,
                    jenis="PAGU_KOSONG",
                    tingkat="Sedang",
                    pesan=f"Subkegiatan {_display(item)} belum memiliki pagu indikatif.",
                    rekomendasi="Isi pagu indikatif subkegiatan sesuai kebutuhan dan plafon anggaran.",
                    acuan=PAGU_ACUAN,
                    item=item,
                    path=path,
                )
            elif pagu is None and raw:
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=level,
                    jenis="PAGU_TIDAK_TERBACA",
                    tingkat="Sedang",
                    pesan=f"Pagu \"{raw}\" pada {_display(item)} tidak dapat dibaca sebagai angka.",
                    rekomendasi="Gunakan format angka tanpa teks tambahan, misalnya 15000000.",
                    acuan=PAGU_ACUAN,
                    item=item,
                    path=path,
                )
            elif pagu == 0:
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=level,
                    jenis="PAGU_NOL",
                    tingkat="Sedang",
                    pesan=f"Subkegiatan {_display(item)} memiliki pagu nol.",
                    rekomendasi="Pastikan subkegiatan tetap didanai atau hapus jika tidak dilaksanakan.",
                    acuan=PAGU_ACUAN,
                    item=item,
                    path=path,
                )
        if isinstance(pagu, (int, float)) and pagu < 0:
            _add(
                findings,
                kategori="Kelengkapan",
                level=level,
                jenis="PAGU_NEGATIF",
                tingkat="Kritis",
                pesan=f"Pagu pada {_display(item)} bernilai negatif.",
                rekomendasi="Perbaiki nilai pagu; pastikan tanda minus tidak terbawa dari format angka.",
                acuan=PAGU_ACUAN,
                item=item,
                path=path,
            )

    # Konsistensi pagu kegiatan terhadap jumlah subkegiatan.
    for program in doc.get("program", []):
        for kegiatan in program.get("kegiatan", []):
            sub_total = sum(
                float(sub.get("pagu"))
                for sub in kegiatan.get("subkegiatan", [])
                if isinstance(sub.get("pagu"), (int, float))
            )
            pagu = kegiatan.get("pagu")
            if isinstance(pagu, (int, float)) and sub_total > 0 and abs(float(pagu) - sub_total) > max(1000, sub_total * 0.01):
                _add(
                    findings,
                    kategori="Kelengkapan",
                    level=LEVEL_KEGIATAN,
                    jenis="PAGU_KEGIATAN_TIDAK_KONSISTEN",
                    tingkat="Saran",
                    pesan=(
                        f"Pagu kegiatan {_display(kegiatan)} ({pagu:,.0f}) berbeda dengan jumlah "
                        f"pagu subkegiatan ({sub_total:,.0f})."
                    ),
                    rekomendasi="Selaraskan pagu kegiatan dengan penjumlahan pagu subkegiatan.",
                    acuan=PAGU_ACUAN,
                    item=kegiatan,
                    path=_path(program, kegiatan),
                )

        # Konsistensi pagu program terhadap kegiatan (atau subkegiatan).
        kegiatan_total = 0.0
        has_kegiatan_pagu = False
        for kegiatan in program.get("kegiatan", []):
            if isinstance(kegiatan.get("pagu"), (int, float)):
                kegiatan_total += float(kegiatan["pagu"])
                has_kegiatan_pagu = True
        if not has_kegiatan_pagu:
            kegiatan_total = sum(
                float(sub.get("pagu"))
                for kegiatan in program.get("kegiatan", [])
                for sub in kegiatan.get("subkegiatan", [])
                if isinstance(sub.get("pagu"), (int, float))
            )
        pagu = program.get("pagu")
        if isinstance(pagu, (int, float)) and kegiatan_total > 0 and abs(float(pagu) - kegiatan_total) > max(1000, kegiatan_total * 0.01):
            _add(
                findings,
                kategori="Kelengkapan",
                level=LEVEL_PROGRAM,
                jenis="PAGU_PROGRAM_TIDAK_KONSISTEN",
                tingkat="Saran",
                pesan=(
                    f"Pagu program {_display(program)} ({pagu:,.0f}) berbeda dengan jumlah "
                    f"pagu kegiatan/subkegiatan ({kegiatan_total:,.0f})."
                ),
                rekomendasi="Selaraskan pagu program dengan penjumlahan pagu di bawahnya.",
                acuan=PAGU_ACUAN,
                item=program,
                path=_path(program),
            )


# ----------------------------------------------------------------------
# Duplikasi
# ----------------------------------------------------------------------
def check_duplicates(doc: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    code_map: dict[tuple[str, tuple[int, ...]], dict] = {}
    level_code_map: dict[tuple[int, ...], str] = {}
    name_map: dict[tuple[str, str, str], dict] = {}
    all_sub_names: dict[str, list[dict]] = {}

    for level, item, program, kegiatan, _root in _iter_items(doc):
        path = _path(program, kegiatan, item if level == LEVEL_SUBKEGIATAN else None)
        if level == LEVEL_KEGIATAN:
            path = _path(program, item)
        if level == LEVEL_PROGRAM:
            path = _path(item)
        code = _text(item.get("kode"))
        name = _text(item.get("nama"))
        key = code_key(code) if code else ()
        if key:
            map_key = (level, key)
            if map_key in code_map:
                first = code_map[map_key]
                if normalize_name(first.get("nama")) != normalize_name(name):
                    _add(
                        findings,
                        kategori="Duplikasi",
                        level=level,
                        jenis="KODE_GANDA_NAMA_BERBEDA",
                        tingkat="Kritis",
                        pesan=(
                            f"Kode {code} muncul lebih dari sekali dengan nama berbeda: "
                            f"\"{first.get('nama')}\" dan \"{name}\"."
                        ),
                        rekomendasi="Gunakan satu kode untuk satu nomenklatur; koreksi baris yang salah.",
                        acuan=REQUIRED_LEVEL_LABEL,
                        item=item,
                        path=path,
                    )
                else:
                    _add(
                        findings,
                        kategori="Duplikasi",
                        level=level,
                        jenis="NOMENKLATUR_GANDA",
                        tingkat="Sedang",
                        pesan=f"{level} {code} - {name} tercantum lebih dari sekali.",
                        rekomendasi="Hapus duplikasi atau pastikan tidak ada pengulangan baris.",
                        acuan=REQUIRED_LEVEL_LABEL,
                        item=item,
                        path=path,
                    )
            else:
                code_map[map_key] = item
            previous_level = level_code_map.get(key)
            if previous_level and previous_level != level:
                _add(
                    findings,
                    kategori="Duplikasi",
                    level=level,
                    jenis="KODE_LINTAS_LEVEL",
                    tingkat="Kritis",
                    pesan=f"Kode {code} digunakan pada level {previous_level} dan {level}.",
                    rekomendasi="Pastikan setiap kode hanya dipakai pada level yang sesuai.",
                    acuan=REQUIRED_LEVEL_LABEL,
                    item=item,
                    path=path,
                )
            level_code_map.setdefault(key, level)

        if name and level != LEVEL_PROGRAM:
            parent_code = ""
            if level == LEVEL_KEGIATAN:
                parent_code = _text(program.get("kode"))
            elif level == LEVEL_SUBKEGIATAN:
                parent_code = _text(kegiatan.get("kode"))
            name_key = (level, parent_code, normalize_name(name))
            if name_key in name_map:
                _add(
                    findings,
                    kategori="Duplikasi",
                    level=level,
                    jenis="NAMA_GANDA",
                    tingkat="Sedang",
                    pesan=(
                        f"Nama {level.lower()} \"{name}\" muncul lebih dari sekali "
                        f"pada induk yang sama."
                    ),
                    rekomendasi="Hapus atau bedakan nomenklatur yang duplikat.",
                    acuan=REQUIRED_LEVEL_LABEL,
                    item=item,
                    path=path,
                )
            else:
                name_map[name_key] = item
        elif name:
            name_key = (level, "", normalize_name(name))
            if name_key in name_map:
                _add(
                    findings,
                    kategori="Duplikasi",
                    level=level,
                    jenis="NAMA_PROGRAM_GANDA",
                    tingkat="Sedang",
                    pesan=f"Nama program \"{name}\" muncul lebih dari sekali.",
                    rekomendasi="Periksa apakah program benar-benar berbeda atau terjadi duplikasi.",
                    acuan=REQUIRED_LEVEL_LABEL,
                    item=item,
                    path=path,
                )
            else:
                name_map[name_key] = item

        if level == LEVEL_SUBKEGIATAN and name:
            all_sub_names.setdefault(normalize_name(name), []).append(item)

    for norm_name, items in all_sub_names.items():
        if len(items) < 2:
            continue
        codes = {code_key(item.get("kode")) for item in items if item.get("kode")}
        if len(codes) > 1:
            _add(
                findings,
                kategori="Duplikasi",
                level=LEVEL_SUBKEGIATAN,
                jenis="NAMA_SUBKEGIATAN_SERUPA",
                tingkat="Saran",
                pesan=(
                    f"Nama subkegiatan \"{items[0].get('nama')}\" dipakai pada beberapa "
                    f"kode berbeda ({', '.join(sorted(_text(item.get('kode')) for item in items if item.get('kode')))})."
                ),
                rekomendasi="Pastikan penggunaan nama serupa pada kode berbeda memang sesuai longlist.",
                acuan=REQUIRED_LEVEL_LABEL,
                item=items[1],
                path=_text(items[1].get("kode")),
            )


# ----------------------------------------------------------------------
# Penyelarasan dengan dokumen acuan
# ----------------------------------------------------------------------
def _reference_tokens(text: str) -> set[str]:
    normalized = normalize_name(text)
    return {token for token in normalized.split() if len(token) > 3 and token not in STOPWORDS}


def _is_lexically_aligned(name: str, reference_norm: str, reference_tokens: set[str]) -> bool:
    phrase = normalize_name(name)
    if len(phrase) < 12:
        return True
    if phrase in reference_norm:
        return True
    tokens = {token for token in phrase.split() if len(token) > 3 and token not in STOPWORDS}
    if not tokens:
        return True
    matched = len(tokens & reference_tokens)
    return matched / len(tokens) >= 0.8


def check_alignment(
    doc: dict[str, Any],
    references: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alignment: list[dict[str, Any]] = []
    if not references:
        _add(
            findings,
            kategori="Penyelarasan",
            level="Dokumen",
            jenis="DOKUMEN_ACUAN_BELUM_ADA",
            tingkat="Saran",
            pesan="Dokumen acuan (RPJMD/Renstra/RKPD) belum diunggah sehingga penyelarasan vertikal belum diperiksa.",
            rekomendasi=(
                "Unggah cuplikan RKPD tahun berjalan, Renstra OPD, dan/atau RPJMD untuk "
                "memeriksa keselarasan program dan subkegiatan."
            ),
            acuan=ALIGNMENT_ACUAN,
        )
        return alignment

    year_doc = _text(doc.get("tahun"))
    for reference in references:
        label = _text(reference.get("nama")) or _text(reference.get("nama_file")) or "Dokumen acuan"
        text = _text(reference.get("teks"))
        if not text:
            alignment.append(
                {
                    "acuan": label,
                    "nama_file": reference.get("nama_file", ""),
                    "status": "Tidak Diperiksa",
                    "jumlah_diperiksa": 0,
                    "jumlah_selaras": 0,
                    "jumlah_belum": 0,
                    "contoh_belum": [],
                    "catatan": "Teks dokumen acuan tidak berhasil diekstrak.",
                }
            )
            continue

        reference_norm = normalize_name(text)
        reference_tokens = _reference_tokens(text)
        checked = 0
        aligned = 0
        unmatched: list[dict[str, str]] = []
        finding_count = 0

        for program in doc.get("program", []):
            name = _text(program.get("nama"))
            if not name:
                continue
            checked += 1
            if _is_lexically_aligned(name, reference_norm, reference_tokens):
                aligned += 1
            else:
                unmatched.append({"level": LEVEL_PROGRAM, "kode": _text(program.get("kode")), "nama": name})
                if finding_count < 15:
                    _add(
                        findings,
                        kategori="Penyelarasan",
                        level=LEVEL_PROGRAM,
                        jenis="PROGRAM_TIDAK_TERSELARAS",
                        tingkat="Saran",
                        pesan=f"Program \"{name}\" tidak ditemukan secara tekstual pada {label}.",
                        rekomendasi=(
                            f"Pastikan program tersebut memang menjadi prioritas {label}; "
                            "selaraskan nomenklatur dan targetnya atau berikan justifikasi."
                        ),
                        acuan=ALIGNMENT_ACUAN,
                        item=program,
                        path=_path(program),
                    )
                    finding_count += 1

            for kegiatan in program.get("kegiatan", []):
                for sub in kegiatan.get("subkegiatan", []):
                    sub_name = _text(sub.get("nama"))
                    if not sub_name:
                        continue
                    checked += 1
                    if _is_lexically_aligned(sub_name, reference_norm, reference_tokens):
                        aligned += 1
                    else:
                        unmatched.append(
                            {"level": LEVEL_SUBKEGIATAN, "kode": _text(sub.get("kode")), "nama": sub_name}
                        )
                        if finding_count < 40:
                            _add(
                                findings,
                                kategori="Penyelarasan",
                                level=LEVEL_SUBKEGIATAN,
                                jenis="SUBKEGIATAN_TIDAK_TERSELARAS",
                                tingkat="Saran",
                                pesan=f"Subkegiatan \"{sub_name}\" tidak ditemukan secara tekstual pada {label}.",
                                rekomendasi=(
                                    f"Cocokkan subkegiatan dengan matriks {label}; "
                                    "bila tidak dianggarkan, jelaskan dasar kebutuhan atau sesuaikan."
                                ),
                                acuan=ALIGNMENT_ACUAN,
                                item=sub,
                                path=_path(program, kegiatan, sub),
                            )
                            finding_count += 1

        if checked == 0:
            status = "Tidak Diperiksa"
        elif aligned == checked:
            status = "Selaras"
        elif aligned == 0:
            status = "Belum Selaras"
        else:
            status = "Selaras Sebagian"
        catatan = (
            f"{aligned} dari {checked} program/subkegiatan teridentifikasi secara tekstual. "
            "Metode ini berbasis kemiripan teks, bukan verifikasi matriks target; verifikasi manual tetap diperlukan."
        )
        if "RKPD" in label.upper():
            ref_year_match = REFERENCE_YEAR_RE.search(text.upper())
            ref_year = ref_year_match.group(1) if ref_year_match else ""
            if not ref_year:
                fallback = re.search(r"\b(20\d{2})\b", text.upper())
                ref_year = fallback.group(1) if fallback else ""
            if year_doc and ref_year and year_doc != ref_year:
                catatan += f" Tahun pada {label} ({ref_year}) berbeda dengan tahun Renja ({year_doc})."
                _add(
                    findings,
                    kategori="Penyelarasan",
                    level="Dokumen",
                    jenis="TAHUN_ACUAN_BERBEDA",
                    tingkat="Saran",
                    pesan=f"Tahun {label} ({ref_year}) berbeda dengan tahun Renja ({year_doc}).",
                    rekomendasi="Gunakan RKPD tahun berjalan yang sesuai dengan tahun Renja.",
                    acuan=ALIGNMENT_ACUAN,
                )
        alignment.append(
            {
                "acuan": label,
                "nama_file": reference.get("nama_file", ""),
                "status": status,
                "jumlah_diperiksa": checked,
                "jumlah_selaras": aligned,
                "jumlah_belum": max(0, checked - aligned),
                "contoh_belum": unmatched[:10],
                "catatan": catatan,
            }
        )
    return alignment


# ----------------------------------------------------------------------
# Narasi
# ----------------------------------------------------------------------
def _build_narrative(
    doc: dict[str, Any],
    findings: list[dict[str, Any]],
    score: dict[str, Any],
    alignment: list[dict[str, Any]],
    llm_result: dict[str, Any] | None,
) -> dict[str, Any]:
    llm_result = llm_result or {}
    ringkasan = _text(llm_result.get("ringkasan"))
    rekomendasi = llm_result.get("rekomendasi_umum") or []
    kesimpulan = _text(llm_result.get("kesimpulan"))
    if not isinstance(rekomendasi, list):
        rekomendasi = [str(rekomendasi)]
    rekomendasi = [_text(item) for item in rekomendasi if _text(item)]

    summary = summarize(findings)
    opd = _text((doc.get("opd") or {}).get("nama")) or "OPD"
    tahun = _text(doc.get("tahun")) or "(tahun belum terisi)"
    stats = doc.get("statistik") or {}
    if not ringkasan:
        top = [finding for finding in findings if finding.get("tingkat") in {"Kritis", "Sedang"}][:4]
        top_text = (
            "; ".join(finding["pesan"].rstrip(".") for finding in top)
            if top
            else "tidak ada temuan kritis yang menonjol"
        )
        ringkasan = (
            f"Renja {opd} tahun {tahun} memuat {stats.get('program', 0)} program, "
            f"{stats.get('kegiatan', 0)} kegiatan, dan {stats.get('subkegiatan', 0)} subkegiatan. "
            f"Skor kesesuaian awal {score['score']} ({predikat(score['score'])}). "
            f"Terdapat {summary['by_severity'].get('Kritis', 0)} temuan kritis, "
            f"{summary['by_severity'].get('Sedang', 0)} temuan sedang, dan "
            f"{summary['by_severity'].get('Saran', 0)} saran. Temuan utama: {top_text}."
        )

    if not rekomendasi:
        seen: set[str] = set()
        for finding in findings:
            if finding.get("tingkat") not in {"Kritis", "Sedang"}:
                continue
            text = finding.get("rekomendasi", "")
            if text and text not in seen:
                seen.add(text)
                rekomendasi.append(text)
            if len(rekomendasi) >= 8:
                break
        if not any("longlist" in item.lower() or "master" in item.lower() for item in rekomendasi):
            rekomendasi.append("Samakan seluruh nomenklatur dengan longlist resmi Permendagri 90/2019 dan pemutakhirannya.")
        if alignment:
            rekomendasi.append("Dokumentasikan hasil penyelarasan program/kegiatan/subkegiatan dengan RKPD, Renstra, dan RPJMD.")
        rekomendasi.append("Lengkapi indikator kinerja yang SMART beserta target dan satuan yang konsisten.")

    if not kesimpulan:
        if score["score"] >= 90:
            kesimpulan = (
                "Dokumen secara umum telah sesuai dengan master nomenklatur dan prinsip perencanaan kinerja. "
                "Perbaikan bersifat minor dan dapat diselesaikan sebelum penetapan."
            )
        elif score["score"] >= 75:
            kesimpulan = (
                "Dokumen cukup baik, namun masih memerlukan perbaikan pada beberapa nomenklatur, indikator, "
                "dan/atau kelengkapan data sebelum dapat ditetapkan."
            )
        elif score["score"] >= 60:
            kesimpulan = (
                "Dokumen memerlukan perbaikan yang cukup banyak, terutama pada konsistensi nomenklatur, "
                "indikator, dan penyelarasan dengan dokumen acuan."
            )
        else:
            kesimpulan = (
                "Dokumen belum siap ditetapkan. Perbaikan mendasar diperlukan pada struktur, nomenklatur, "
                "indikator, dan penyelarasan dengan RKPD/Renstra/RPJMD."
            )

    return {
        "ringkasan": ringkasan,
        "rekomendasi_umum": rekomendasi,
        "kesimpulan": kesimpulan,
        "sumber_narasi": "llm" if _text(llm_result.get("ringkasan")) else "sistem",
    }


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def analyze_document(
    doc: dict[str, Any],
    master: MasterNomenklatur,
    references: list[dict[str, Any]] | None = None,
    llm_result: dict[str, Any] | None = None,
    extraction_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Jalankan seluruh pemeriksaan dan kembalikan hasil analisis."""
    findings: list[dict[str, Any]] = []
    references = references or []

    check_identity(doc, findings)
    master_available = check_master_availability(master, findings)
    if doc.get("program"):
        check_structure(doc, findings)
        if master_available:
            check_nomenclature(doc, master, findings)
        else:
            for _level, item, *_rest in _iter_items(doc):
                item["_status_nomenklatur"] = "Tidak Terverifikasi"
                item["_catatan_nomenklatur"] = "Master nomenklatur belum tersedia."
        check_indicators(doc, findings)
        check_pagu(doc, findings)
        check_duplicates(doc, findings)
    alignment = check_alignment(doc, references, findings) if doc.get("program") else []

    if llm_result:
        for raw_finding in llm_result.get("findings", []) or []:
            findings.append(normalize_finding(raw_finding, source="llm"))

    findings = dedupe_findings(findings)
    findings = sort_findings(findings)
    findings = assign_ids(findings)
    score = compute_score(findings)
    summary = summarize(findings)
    narrative = _build_narrative(doc, findings, score, alignment, llm_result)

    warnings: list[str] = []
    warnings.extend(extraction_warnings or doc.get("warnings", []))
    warnings.extend(master.warnings)
    if master.is_sample:
        warnings.insert(
            0,
            "Master nomenklatur yang digunakan masih berstatus CONTOH. Hasil analisis belum valid untuk "
            "dokumen resmi; ganti dengan longlist Permendagri 90/2019 dan pemutakhirannya.",
        )
    if master.meta.get("catatan"):
        warnings.append(_text(master.meta.get("catatan")))
    if llm_result and llm_result.get("error"):
        warnings.append(f"Analisis LLM tidak digunakan: {llm_result['error']}")
    warnings = list(dict.fromkeys(warning for warning in warnings if warning))

    doc["statistik"] = document_statistics(doc)
    doc["_status_master"] = "CONTOH" if master.is_sample else "IMPOR"

    return {
        "findings": findings,
        "score": score,
        "summary": summary,
        "alignment": alignment,
        "narrative": narrative,
        "warnings": warnings,
        "master": master.summary(),
        "llm": {
            "enabled": bool(llm_result and llm_result.get("enabled")),
            "error": _text((llm_result or {}).get("error")),
            "model": _text((llm_result or {}).get("model")),
        },
    }


def recompute_after_review(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Hitung ulang skor setelah status review diubah."""
    score = compute_score(findings)
    return {"score": score, "summary": summarize(findings)}
