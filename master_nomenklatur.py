"""Pengelolaan master nomenklatur Program, Kegiatan, dan Subkegiatan.

Master dapat berasal dari JSON bertingkat, JSON baris datar, atau CSV dengan
kolom kode/nama per level. Modul ini menyediakan pencarian kode, pencarian nama,
deteksi ketidaksesuaian nomenklatur, dan validasi hierarki kode.

Format JSON bertingkat yang didukung::

    {
      "meta": {...},
      "program": [
        {"kode": "1.01", "nama": "...", "kegiatan": [
          {"kode": "1.01.01", "nama": "...", "subkegiatan": [
            {"kode": "1.01.01.2.01", "nama": "..."}
          ]}
        ]}
      ]
    }

Format CSV yang didukung menggunakan header seperti pada
``data/master_template.csv``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path
from typing import Any, Iterable

LEVEL_PROGRAM = "Program"
LEVEL_KEGIATAN = "Kegiatan"
LEVEL_SUBKEGIATAN = "Subkegiatan"
LEVELS = (LEVEL_PROGRAM, LEVEL_KEGIATAN, LEVEL_SUBKEGIATAN)

LEVEL_BY_SEGMENTS = {2: LEVEL_PROGRAM, 3: LEVEL_KEGIATAN}

CODE_RE = re.compile(r"\d{1,2}(?:\.\d{1,2}){1,4}")
NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")
SPACE_RE = re.compile(r"\s+")

NAME_PREFIXES = (
    "SUB KEGIATAN ",
    "SUBKEGIATAN ",
    "PROGRAM ",
    "KEGIATAN ",
)

_HEADER_ALIASES = {
    "kode_program": ("kode_program", "kode program", "kd_program", "kd program", "kodeprogram"),
    "nama_program": ("nama_program", "nama program", "uraian_program", "uraian program", "program"),
    "kode_kegiatan": ("kode_kegiatan", "kode kegiatan", "kd_kegiatan", "kd kegiatan", "kodekegiatan"),
    "nama_kegiatan": ("nama_kegiatan", "nama kegiatan", "uraian_kegiatan", "uraian kegiatan", "kegiatan"),
    "kode_subkegiatan": (
        "kode_subkegiatan",
        "kode subkegiatan",
        "kode_sub_kegiatan",
        "kode sub kegiatan",
        "kd_subkegiatan",
        "kd subkegiatan",
        "kodesubkegiatan",
    ),
    "nama_subkegiatan": (
        "nama_subkegiatan",
        "nama subkegiatan",
        "nama_sub_kegiatan",
        "nama sub kegiatan",
        "uraian_subkegiatan",
        "uraian subkegiatan",
        "subkegiatan",
        "sub kegiatan",
    ),
    "kode": ("kode", "kd", "kode_nomenklatur", "kode nomenklatur", "kode_urusan"),
    "nama": ("nama", "uraian", "nomenklatur", "nama_nomenklatur", "uraian_nomenklatur", "uraian nomenklatur"),
    "level": ("level", "tingkat", "jenis", "level_nomenklatur", "jenis_nomenklatur"),
    "parent": ("parent", "parent_kode", "kode_induk", "induk"),
}


def norm_space(value: Any) -> str:
    """Rapikan spasi dan konversi nilai menjadi string."""
    if value is None:
        return ""
    return SPACE_RE.sub(" ", str(value)).strip()


def normalize_code(value: Any) -> str:
    """Normalisasi kode menjadi bentuk angka bertitik tanpa spasi.

    Contoh: ``" 1.01.01.2.01 "`` menjadi ``"1.01.01.2.01"``.
    """
    text = norm_space(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("..", ".")
    found = CODE_RE.search(text)
    if found:
        return found.group(0).strip(".")
    digits = re.sub(r"[^0-9.]", "", text)
    return digits.strip(".")


def code_parts(code: Any) -> tuple[int, ...]:
    """Ubah kode menjadi tuple angka untuk perbandingan yang aman."""
    normalized = normalize_code(code)
    parts: list[int] = []
    for part in normalized.split("."):
        if not part.isdigit():
            continue
        parts.append(int(part))
    return tuple(parts)


def code_key(code: Any) -> tuple[int, ...]:
    """Kunci pencarian kode yang mengabaikan nol di depan."""
    return code_parts(code)


def code_level(code: Any) -> str | None:
    """Tentukan level berdasarkan jumlah segmen kode."""
    parts = code_parts(code)
    if len(parts) == 2:
        return LEVEL_PROGRAM
    if len(parts) == 3:
        return LEVEL_KEGIATAN
    if len(parts) >= 4:
        return LEVEL_SUBKEGIATAN
    return None


def normalize_name(value: Any) -> str:
    """Normalisasi nama untuk pencocokan nomenklatur.

    Huruf diseragamkan menjadi kapital, tanda baca dihilangkan, ``&`` diubah
    menjadi ``DAN``, dan label level di awal nama dibuang agar
    ``"Program Penunjang..."`` setara dengan ``"Penunjang..."``.
    """
    text = unicodedata.normalize("NFKD", norm_space(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper().replace("&", " DAN ")
    text = NON_ALNUM_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    for prefix in NAME_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return text


def name_similarity(left: Any, right: Any) -> float:
    """Nilai kemiripan dua nama (0.0 - 1.0)."""
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if (a in b or b in a) and min(len(a), len(b)) >= 12:
        return 0.94
    return SequenceMatcher(None, a, b).ratio()


def _row_lookup(row: dict[str, Any], aliases: Iterable[str]) -> Any:
    lowered = {str(k).strip().lower().replace("__", "_"): v for k, v in row.items()}
    for alias in aliases:
        if alias in lowered and norm_space(lowered[alias]):
            return lowered[alias]
    return ""


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


class MasterNomenklatur:
    """Master nomenklatur beserta indeks pencarian kode dan nama."""

    def __init__(self, programs: list[dict], meta: dict | None = None, warnings: list[str] | None = None):
        self.programs = programs
        self.meta = meta or {}
        self.warnings = warnings or []
        self.index_code: dict[tuple[int, ...], dict[str, Any]] = {}
        self.index_name: dict[str, list[dict[str, Any]]] = {}
        self.index_by_level: dict[str, list[dict[str, Any]]] = {level: [] for level in LEVELS}
        self._candidate_names: dict[str, list[str]] = {level: [] for level in LEVELS}
        self._build_index()

    # ------------------------------------------------------------------
    # Pembuatan dan pemuatan
    # ------------------------------------------------------------------
    @classmethod
    def load_default(cls, path: str | Path | None = None) -> "MasterNomenklatur":
        """Muat master bawaan dari ``data/nomenklatur_kab_kota.json``."""
        target = Path(path) if path else Path(__file__).with_name("data") / "nomenklatur_kab_kota.json"
        if not target.exists():
            return cls([], meta={"sumber": "Master kosong", "status": "KOSONG"})
        return cls.from_file(target)

    @classmethod
    def from_file(cls, path: str | Path) -> "MasterNomenklatur":
        target = Path(path)
        data = target.read_bytes()
        return cls.from_bytes(data, target.name)

    @classmethod
    def from_bytes(cls, data: bytes, filename: str = "master.json") -> "MasterNomenklatur":
        text = _decode_bytes(data)
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv" or (suffix != ".json" and _looks_like_csv(text)):
            return cls._from_csv_text(text)
        return cls._from_json_text(text)

    @classmethod
    def _from_json_text(cls, text: str) -> "MasterNomenklatur":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON master tidak valid: baris {exc.lineno}, kolom {exc.colno}: {exc.msg}") from exc
        return cls.from_payload(payload)

    @classmethod
    def _from_csv_text(cls, text: str) -> "MasterNomenklatur":
        text = text.lstrip("\ufeff")
        lines = text.splitlines()
        if lines and lines[0].lower().startswith("sep="):
            lines = lines[1:]
        sample = "\n".join(lines[:20])
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
            delimiter = dialect.delimiter
        except csv.Error:
            pass
        reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV master tidak memiliki header.")
        rows = [dict(row) for row in reader]
        programs, warnings = _programs_from_flat_rows(rows)
        meta = {"sumber": "Impor CSV", "status": "IMPOR", "jumlah_baris": len(rows)}
        return cls(programs, meta=meta, warnings=warnings)

    @classmethod
    def from_payload(cls, payload: Any) -> "MasterNomenklatur":
        if isinstance(payload, list):
            programs, warnings = _programs_from_flat_rows(payload)
            return cls(programs, meta={"sumber": "JSON baris datar", "status": "IMPOR"}, warnings=warnings)

        if not isinstance(payload, dict):
            raise ValueError("Master harus berupa objek JSON atau daftar baris.")

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        for key in ("program", "programs", "data_program"):
            value = payload.get(key)
            if isinstance(value, list):
                if value and isinstance(value[0], dict) and any(
                    k in value[0] for k in ("kegiatan", "subkegiatan", "sub_kegiatan", "children")
                ):
                    programs, warnings = _programs_from_nested(value)
                    return cls(programs, meta=meta, warnings=warnings)
                programs, warnings = _programs_from_flat_rows(value)
                return cls(programs, meta=meta, warnings=warnings)

        for key in ("rows", "data", "items", "nomenklatur"):
            value = payload.get(key)
            if isinstance(value, list):
                programs, warnings = _programs_from_flat_rows(value)
                return cls(programs, meta=meta, warnings=warnings)

        if "kode" in payload or "nama" in payload:
            programs, warnings = _programs_from_flat_rows([payload])
            return cls(programs, meta=meta, warnings=warnings)

        raise ValueError(
            "Struktur master tidak dikenali. Gunakan kunci 'program', 'rows', "
            "atau unggah CSV sesuai template."
        )

    # ------------------------------------------------------------------
    # Indeks
    # ------------------------------------------------------------------
    def _build_index(self) -> None:
        for program in self.programs:
            self._index_node(program, LEVEL_PROGRAM, None, None)
            for kegiatan in program.get("kegiatan", []):
                self._index_node(kegiatan, LEVEL_KEGIATAN, program, None)
                for sub in kegiatan.get("subkegiatan", []):
                    self._index_node(sub, LEVEL_SUBKEGIATAN, program, kegiatan)
        for level in LEVELS:
            self._candidate_names[level] = [node["_nama_norm"] for node in self.index_by_level[level] if node["_nama_norm"]]

    def _index_node(
        self,
        node: dict[str, Any],
        level: str,
        program: dict[str, Any] | None,
        kegiatan: dict[str, Any] | None,
    ) -> None:
        code = normalize_code(node.get("kode"))
        name = norm_space(node.get("nama"))
        if not code and not name:
            return
        key = code_key(code) if code else ()
        record = {
            "level": level,
            "kode": code,
            "nama": name,
            "kode_key": key,
            "program_kode": normalize_code(program.get("kode")) if program else "",
            "program_nama": norm_space(program.get("nama")) if program else "",
            "kegiatan_kode": normalize_code(kegiatan.get("kode")) if kegiatan else "",
            "kegiatan_nama": norm_space(kegiatan.get("nama")) if kegiatan else "",
            "_nama_norm": normalize_name(name),
        }
        if key:
            self.index_code.setdefault(key, record)
        if record["_nama_norm"]:
            self.index_name.setdefault(record["_nama_norm"], []).append(record)
        self.index_by_level[level].append(record)

    # ------------------------------------------------------------------
    # Pencarian dan validasi
    # ------------------------------------------------------------------
    def lookup_code(self, code: Any) -> dict[str, Any] | None:
        return self.index_code.get(code_key(code))

    def lookup_name(self, name: Any) -> list[dict[str, Any]]:
        return list(self.index_name.get(normalize_name(name), []))

    def find_similar(self, name: Any, level: str | None = None, limit: int = 3) -> list[dict[str, Any]]:
        """Cari nomenklatur terdekat berdasarkan kemiripan nama."""
        target = normalize_name(name)
        if not target:
            return []
        levels = [level] if level in LEVELS else list(LEVELS)
        collected: list[tuple[float, dict[str, Any]]] = []
        for current_level in levels:
            candidates = self._candidate_names.get(current_level, [])
            if not candidates:
                continue
            matches = get_close_matches(target, candidates, n=limit, cutoff=0.72)
            for match in matches:
                for node in self.index_name.get(match, []):
                    collected.append((name_similarity(target, node["nama"]), node))
        collected.sort(key=lambda pair: pair[0], reverse=True)
        results: list[dict[str, Any]] = []
        seen: set[tuple[int, ...]] = set()
        for score, node in collected:
            if node["kode_key"] in seen:
                continue
            seen.add(node["kode_key"])
            results.append({"skor": round(score, 3), **node})
            if len(results) >= limit:
                break
        return results

    def resolve(self, code: Any, name: Any, level: str | None = None) -> dict[str, Any]:
        """Bandingkan satu item Renja dengan master.

        Mengembalikan status, tingkat keparahan, pesan, rekomendasi, dan node
        master yang diharapkan (bila ada).
        """
        code_n = normalize_code(code)
        name_s = norm_space(name)
        name_n = normalize_name(name)
        detected_level = level or code_level(code_n)
        base = {
            "status": "tidak_terdaftar",
            "tingkat": "Kritis",
            "pesan": "",
            "rekomendasi": "",
            "node": None,
            "kandidat": [],
            "level": detected_level,
            "acuan": "Master nomenklatur Permendagri 90/2019 dan pemutakhirannya",
        }

        if not code_n and not name_n:
            return {
                **base,
                "status": "data_kosong",
                "pesan": "Kode dan nama nomenklatur kosong.",
                "rekomendasi": "Lengkapi kode serta nama sesuai longlist nomenklatur resmi.",
            }

        node = self.lookup_code(code_n) if code_n else None
        if node:
            expected_level = node["level"]
            if detected_level and detected_level != expected_level:
                return {
                    **base,
                    "status": "level_tidak_sesuai",
                    "node": node,
                    "pesan": (
                        f"Kode {code_n} pada master merupakan {expected_level.lower()}, "
                        f"bukan {detected_level.lower()}."
                    ),
                    "rekomendasi": (
                        f"Perbaiki level/penempatan menjadi {expected_level} dengan nama "
                        f"\"{node['nama']}\" atau gunakan kode yang sesuai."
                    ),
                }
            if not name_n:
                return {
                    **base,
                    "status": "nama_kosong",
                    "node": node,
                    "pesan": f"Nama untuk kode {code_n} kosong.",
                    "rekomendasi": f"Gunakan nama resmi \"{node['nama']}\".",
                }
            official_norm = node["_nama_norm"]
            if name_n == official_norm:
                return {
                    **base,
                    "status": "sesuai",
                    "tingkat": None,
                    "node": node,
                    "pesan": "Nomenklatur sesuai master.",
                    "rekomendasi": "",
                }

            ratio = name_similarity(name_s, node["nama"])
            if ratio >= 0.90 or (
                (name_n in official_norm or official_norm in name_n) and min(len(name_n), len(official_norm)) >= 12
            ):
                return {
                    **base,
                    "status": "nama_tidak_sesuai_ringan",
                    "tingkat": "Sedang",
                    "node": node,
                    "pesan": (
                        f"Nama tidak persis sama dengan master. Tertulis \"{name_s}\"; "
                        f"nama resmi \"{node['nama']}\"."
                    ),
                    "rekomendasi": f"Samakan penulisan nama menjadi \"{node['nama']}\".",
                }
            return {
                **base,
                "status": "nama_tidak_sesuai",
                "tingkat": "Kritis",
                "node": node,
                "pesan": (
                    f"Kode {code_n} benar, tetapi nama tidak sesuai master. "
                    f"Tertulis \"{name_s}\"; seharusnya \"{node['nama']}\"."
                ),
                "rekomendasi": f"Ganti nama menjadi \"{node['nama']}\" atau gunakan kode lain yang sesuai.",
            }

        # Kode tidak ditemukan. Coba cocokkan berdasarkan nama.
        name_matches = self.lookup_name(name_s) if name_n else []
        if name_matches:
            match = name_matches[0]
            return {
                **base,
                "status": "kode_tidak_sesuai",
                "node": match,
                "pesan": (
                    f"Kode {code_n or '(kosong)'} tidak ditemukan pada master. "
                    f"Nama \"{name_s}\" terdaftar dengan kode {match['kode']}."
                ),
                "rekomendasi": f"Gunakan kode {match['kode']} untuk nomenklatur \"{match['nama']}\".",
                "kandidat": name_matches[:3],
            }

        similar = self.find_similar(name_s, level=detected_level) if name_n else []
        if similar and similar[0]["skor"] >= 0.86:
            best = similar[0]
            return {
                **base,
                "status": "kode_dan_nama_tidak_sesuai",
                "node": best,
                "pesan": (
                    f"Nomenklatur tidak ditemukan pada master. Kemungkinan maksudnya "
                    f"\"{best['nama']}\" (kode {best['kode']})."
                ),
                "rekomendasi": (
                    f"Gunakan nomenklatur resmi \"{best['nama']}\" dengan kode {best['kode']}, "
                    "setelah diverifikasi pada longlist resmi."
                ),
                "kandidat": similar,
            }

        if not code_n:
            return {
                **base,
                "status": "kode_kosong",
                "pesan": f"Kode kosong untuk \"{name_s}\"; nomenklatur tidak dapat diverifikasi.",
                "rekomendasi": "Lengkapi kode sesuai longlist nomenklatur resmi.",
                "kandidat": similar,
            }

        return {
            **base,
            "status": "tidak_terdaftar",
            "pesan": f"Nomenklatur \"{name_s}\" dengan kode {code_n} tidak ditemukan pada master.",
            "rekomendasi": "Cocokkan dengan longlist resmi; perbaiki kode/nama atau gunakan nomenklatur yang terdaftar.",
            "kandidat": similar,
        }

    def validate_hierarchy(self, code: Any, expected_level: str | None = None) -> dict[str, Any]:
        """Validasi bentuk kode dan keberadaan induknya pada master."""
        code_n = normalize_code(code)
        parts = code_key(code_n)
        level = expected_level or code_level(code_n)
        result: dict[str, Any] = {
            "valid": True,
            "level": level,
            "kode": code_n,
            "issues": [],
            "program_kode": "",
            "kegiatan_kode": "",
        }
        if len(parts) < 2:
            result["valid"] = False
            result["issues"].append(
                {
                    "jenis": "FORMAT_KODE",
                    "pesan": f"Kode {code_n or '(kosong)'} tidak memiliki format hierarki yang dikenali.",
                    "rekomendasi": "Gunakan format kode Program (x.xx), Kegiatan (x.xx.xx), atau Subkegiatan (x.xx.xx.x.xx).",
                }
            )
            return result

        segments = code_n.split(".")

        def join(count: int) -> str:
            return ".".join(segments[:count])

        if len(parts) == 2:
            result["program_kode"] = code_n
        elif len(parts) == 3:
            result["program_kode"] = join(2)
            result["kegiatan_kode"] = code_n
        else:
            result["program_kode"] = join(2)
            result["kegiatan_kode"] = join(3)
            if len(parts) == 4:
                result["issues"].append(
                    {
                        "jenis": "FORMAT_SUBKEGIATAN",
                        "pesan": f"Kode {code_n} memiliki 4 segmen; format subkegiatan umumnya 5 segmen.",
                        "rekomendasi": "Verifikasi format kode subkegiatan pada longlist resmi.",
                    }
                )

        node = self.lookup_code(code_n)
        if not node:
            result["valid"] = False
            result["issues"].append(
                {
                    "jenis": "KODE_TIDAK_DITEMUKAN",
                    "pesan": f"Kode {code_n} tidak ditemukan pada master.",
                    "rekomendasi": "Gunakan kode yang terdaftar pada longlist nomenklatur resmi.",
                }
            )
            return result

        if level and node["level"] != level:
            result["valid"] = False
            result["issues"].append(
                {
                    "jenis": "LEVEL_TIDAK_SESUAI",
                    "pesan": f"Kode {code_n} adalah {node['level']}, bukan {level}.",
                    "rekomendasi": "Perbaiki penempatan level pada matriks Renja.",
                }
            )

        if result["program_kode"] and result["program_kode"] != "":
            parent_program = self.lookup_code(result["program_kode"])
            if not parent_program:
                result["valid"] = False
                result["issues"].append(
                    {
                        "jenis": "PROGRAM_INDUK_TIDAK_DITEMUKAN",
                        "pesan": f"Program induk {result['program_kode']} tidak ditemukan pada master.",
                        "rekomendasi": "Pastikan program induk tercantum pada master nomenklatur.",
                    }
                )
            elif node["program_kode"] and code_key(node["program_kode"]) != code_key(result["program_kode"]):
                result["valid"] = False
                result["issues"].append(
                    {
                        "jenis": "HIERARKI_TIDAK_KONSISTEN",
                        "pesan": (
                            f"Menurut master, kode {code_n} berada di bawah program "
                            f"{node['program_kode']}, bukan {result['program_kode']}."
                        ),
                        "rekomendasi": "Perbaiki kode atau susunan hierarki sesuai master.",
                    }
                )

        if level in (LEVEL_KEGIATAN, LEVEL_SUBKEGIATAN) and result["kegiatan_kode"]:
            parent_kegiatan = self.lookup_code(result["kegiatan_kode"])
            if not parent_kegiatan:
                result["valid"] = False
                result["issues"].append(
                    {
                        "jenis": "KEGIATAN_INDUK_TIDAK_DITEMUKAN",
                        "pesan": f"Kegiatan induk {result['kegiatan_kode']} tidak ditemukan pada master.",
                        "rekomendasi": "Pastikan kegiatan induk tercantum pada master nomenklatur.",
                    }
                )
            elif node["kegiatan_kode"] and code_key(node["kegiatan_kode"]) != code_key(result["kegiatan_kode"]):
                result["valid"] = False
                result["issues"].append(
                    {
                        "jenis": "HIERARKI_TIDAK_KONSISTEN",
                        "pesan": (
                            f"Menurut master, kode {code_n} berada di bawah kegiatan "
                            f"{node['kegiatan_kode']}, bukan {result['kegiatan_kode']}."
                        ),
                        "rekomendasi": "Perbaiki kode atau susunan hierarki sesuai master.",
                    }
                )

        return result

    # ------------------------------------------------------------------
    # Informasi umum
    # ------------------------------------------------------------------
    @property
    def is_sample(self) -> bool:
        status = str(self.meta.get("status", "")).upper()
        return status in {"CONTOH", "DEMO", "SEED"} or bool(self.meta.get("wajib_diganti"))

    def counts(self) -> dict[str, int]:
        return {
            "program": len(self.index_by_level[LEVEL_PROGRAM]),
            "kegiatan": len(self.index_by_level[LEVEL_KEGIATAN]),
            "subkegiatan": len(self.index_by_level[LEVEL_SUBKEGIATAN]),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "counts": self.counts(),
            "is_sample": self.is_sample,
            "warnings": self.warnings,
        }

    def compact_entries_for_codes(self, codes: Iterable[Any], limit: int = 300) -> list[dict[str, str]]:
        """Ambil entri master relevan untuk dikirim ke LLM."""
        entries: list[dict[str, str]] = []
        seen: set[tuple[int, ...]] = set()
        for raw_code in codes:
            node = self.lookup_code(raw_code)
            if not node or node["kode_key"] in seen:
                continue
            seen.add(node["kode_key"])
            entries.append(
                {
                    "level": node["level"],
                    "kode": node["kode"],
                    "nama": node["nama"],
                    "program": node["program_nama"],
                    "kegiatan": node["kegiatan_nama"],
                }
            )
            if len(entries) >= limit:
                break
        return entries


# ----------------------------------------------------------------------
# Pembentuk struktur dari data datar
# ----------------------------------------------------------------------
def _display_code(value: Any) -> str:
    return normalize_code(value)


def _programs_from_nested(items: list[dict]) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    programs: list[dict] = []
    seen_program: set[tuple[int, ...]] = set()
    for raw_program in items:
        if not isinstance(raw_program, dict):
            continue
        p_code = _display_code(raw_program.get("kode") or raw_program.get("kode_program"))
        p_name = norm_space(
            raw_program.get("nama")
            or raw_program.get("nama_program")
            or raw_program.get("uraian")
            or raw_program.get("uraian_program")
        )
        if not p_code and not p_name:
            continue
        if not p_code:
            warnings.append(f"Program \"{p_name}\" tidak memiliki kode dan dilewati.")
            continue
        p_key = code_key(p_code)
        if p_key in seen_program:
            warnings.append(f"Kode program duplikat {p_code} dilewati.")
            continue
        seen_program.add(p_key)
        program = {"kode": p_code, "nama": p_name, "kegiatan": []}
        seen_keg: set[tuple[int, ...]] = set()
        for raw_keg in _as_list(raw_program.get("kegiatan")):
            if not isinstance(raw_keg, dict):
                continue
            k_code = _display_code(raw_keg.get("kode") or raw_keg.get("kode_kegiatan"))
            k_name = norm_space(
                raw_keg.get("nama")
                or raw_keg.get("nama_kegiatan")
                or raw_keg.get("uraian")
                or raw_keg.get("uraian_kegiatan")
            )
            if not k_code and not k_name:
                continue
            if not k_code:
                warnings.append(f"Kegiatan \"{k_name}\" tidak memiliki kode dan dilewati.")
                continue
            k_key = code_key(k_code)
            if k_key in seen_keg:
                warnings.append(f"Kode kegiatan duplikat {k_code} dilewati.")
                continue
            seen_keg.add(k_key)
            kegiatan = {"kode": k_code, "nama": k_name, "subkegiatan": []}
            seen_sub: set[tuple[int, ...]] = set()
            raw_subs = raw_keg.get("subkegiatan") or raw_keg.get("sub_kegiatan")
            for raw_sub in _as_list(raw_subs):
                if not isinstance(raw_sub, dict):
                    continue
                s_code = _display_code(
                    raw_sub.get("kode")
                    or raw_sub.get("kode_subkegiatan")
                    or raw_sub.get("kode_sub_kegiatan")
                )
                s_name = norm_space(
                    raw_sub.get("nama")
                    or raw_sub.get("nama_subkegiatan")
                    or raw_sub.get("nama_sub_kegiatan")
                    or raw_sub.get("uraian")
                )
                if not s_code and not s_name:
                    continue
                if not s_code:
                    warnings.append(f"Subkegiatan \"{s_name}\" tidak memiliki kode dan dilewati.")
                    continue
                s_key = code_key(s_code)
                if s_key in seen_sub:
                    warnings.append(f"Kode subkegiatan duplikat {s_code} dilewati.")
                    continue
                seen_sub.add(s_key)
                kegiatan["subkegiatan"].append({"kode": s_code, "nama": s_name})
            program["kegiatan"].append(kegiatan)
        programs.append(program)
    programs = _sort_nested(programs)
    return programs, warnings


def _programs_from_flat_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    program_map: dict[tuple[int, ...], dict] = {}
    kegiatan_map: dict[tuple[int, ...], dict] = {}
    sub_seen: set[tuple[int, ...]] = set()
    row_count = 0

    def ensure_program(code: Any, name: Any) -> dict | None:
        code_n = _display_code(code)
        if not code_n:
            return None
        key = code_key(code_n)
        node = program_map.get(key)
        if node is None:
            node = {"kode": code_n, "nama": norm_space(name), "kegiatan": []}
            program_map[key] = node
        elif norm_space(name) and not node["nama"]:
            node["nama"] = norm_space(name)
        return node

    def ensure_kegiatan(program: dict, code: Any, name: Any) -> dict | None:
        code_n = _display_code(code)
        if not code_n:
            return None
        key = code_key(code_n)
        node = kegiatan_map.get(key)
        if node is None:
            node = {"kode": code_n, "nama": norm_space(name), "subkegiatan": []}
            kegiatan_map[key] = node
            program["kegiatan"].append(node)
        elif norm_space(name) and not node["nama"]:
            node["nama"] = norm_space(name)
        return node

    for index, raw_row in enumerate(rows, start=2):
        if not isinstance(raw_row, dict):
            continue
        row = {str(k).strip().lower().replace("__", "_"): v for k, v in raw_row.items()}
        p_code = _row_lookup(row, _HEADER_ALIASES["kode_program"])
        p_name = _row_lookup(row, _HEADER_ALIASES["nama_program"])
        k_code = _row_lookup(row, _HEADER_ALIASES["kode_kegiatan"])
        k_name = _row_lookup(row, _HEADER_ALIASES["nama_kegiatan"])
        s_code = _row_lookup(row, _HEADER_ALIASES["kode_subkegiatan"])
        s_name = _row_lookup(row, _HEADER_ALIASES["nama_subkegiatan"])
        generic_code = _row_lookup(row, _HEADER_ALIASES["kode"])
        generic_name = _row_lookup(row, _HEADER_ALIASES["nama"])
        generic_level = norm_space(_row_lookup(row, _HEADER_ALIASES["level"]))
        parent_code = _row_lookup(row, _HEADER_ALIASES["parent"])

        if not p_code and not k_code and not s_code and not generic_code:
            continue
        row_count += 1

        if s_code:
            s_code_n = _display_code(s_code)
            if not p_code:
                p_code = ".".join(s_code_n.split(".")[:2])
            if not k_code:
                k_code = ".".join(s_code_n.split(".")[:3])
            program = ensure_program(p_code, p_name)
            if program is None:
                warnings.append(f"Baris {index}: subkegiatan {s_code_n} tanpa kode program yang valid.")
                continue
            kegiatan = ensure_kegiatan(program, k_code, k_name)
            if kegiatan is None:
                warnings.append(f"Baris {index}: subkegiatan {s_code_n} tanpa kode kegiatan yang valid.")
                continue
            skey = code_key(s_code_n)
            if skey in sub_seen:
                warnings.append(f"Baris {index}: subkegiatan duplikat {s_code_n} dilewati.")
                continue
            sub_seen.add(skey)
            kegiatan["subkegiatan"].append({"kode": s_code_n, "nama": norm_space(s_name)})
            continue

        if k_code:
            k_code_n = _display_code(k_code)
            if not p_code:
                p_code = ".".join(k_code_n.split(".")[:2])
            program = ensure_program(p_code, p_name)
            if program is None:
                warnings.append(f"Baris {index}: kegiatan {k_code_n} tanpa kode program yang valid.")
                continue
            ensure_kegiatan(program, k_code, k_name)
            continue

        # Baris dengan kolom kode/nama generik dan opsional level.
        code_n = _display_code(generic_code)
        level = _level_from_label(generic_level) or code_level(code_n)
        name = norm_space(generic_name or p_name or k_name or s_name)

        if level == LEVEL_PROGRAM or (len(code_key(code_n)) == 2):
            ensure_program(code_n, name)
        elif level == LEVEL_KEGIATAN or (len(code_key(code_n)) == 3):
            if not p_code and len(code_key(code_n)) >= 2:
                p_code = ".".join(code_n.split(".")[:2])
            program = ensure_program(p_code, p_name)
            if program:
                ensure_kegiatan(program, code_n, name)
        elif level == LEVEL_SUBKEGIATAN or len(code_key(code_n)) >= 4:
            if not p_code and len(code_key(code_n)) >= 2:
                p_code = ".".join(code_n.split(".")[:2])
            if not k_code and len(code_key(code_n)) >= 3:
                k_code = ".".join(code_n.split(".")[:3])
            program = ensure_program(p_code, p_name)
            if program is None:
                continue
            kegiatan = ensure_kegiatan(program, k_code, k_name)
            if kegiatan is None:
                continue
            skey = code_key(code_n)
            if skey in sub_seen:
                warnings.append(f"Baris {index}: subkegiatan duplikat {code_n} dilewati.")
                continue
            sub_seen.add(skey)
            kegiatan["subkegiatan"].append({"kode": code_n, "nama": name})
        elif parent_code:
            # Format dengan parent_kode tetapi tanpa level yang jelas.
            parent_key = code_key(parent_code)
            if parent_key in kegiatan_map:
                kegiatan = kegiatan_map[parent_key]
                kegiatan["subkegiatan"].append({"kode": code_n, "nama": name})
            elif parent_key in program_map:
                ensure_kegiatan(program_map[parent_key], code_n, name)
        else:
            warnings.append(f"Baris {index}: level kode {code_n or '(kosong)'} tidak dapat ditentukan; dilewati.")

    programs = _sort_nested(list(program_map.values()))
    missing_parent_names = [
        f"{node['level']} {node['kode']} belum memiliki nama pada master."
        for node in list(program_map.values())
        if not node["nama"]
    ] + [
        f"Kegiatan {node['kode']} belum memiliki nama pada master."
        for node in list(kegiatan_map.values())
        if not node["nama"]
    ]
    warnings.extend(missing_parent_names)
    if row_count == 0:
        warnings.append("Tidak ada baris master yang dapat dibaca.")
    return programs, warnings


def _level_from_label(value: Any) -> str | None:
    text = normalize_name(value)
    if not text:
        return None
    if text.startswith("SUB"):
        return LEVEL_SUBKEGIATAN
    if text.startswith("KEGIATAN") or "KEGIATAN" in text:
        return LEVEL_KEGIATAN
    if text.startswith("PROGRAM") or "PROGRAM" in text:
        return LEVEL_PROGRAM
    return None


def _sort_nested(programs: list[dict]) -> list[dict]:
    programs.sort(key=lambda item: code_key(item.get("kode")))
    for program in programs:
        program["kegiatan"].sort(key=lambda item: code_key(item.get("kode")))
        for kegiatan in program["kegiatan"]:
            kegiatan["subkegiatan"].sort(key=lambda item: code_key(item.get("kode")))
    return programs


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _looks_like_csv(text: str) -> bool:
    first = text.lstrip()[:500].lower()
    return first.startswith("kode") or first.startswith("sep=") or ("kode_program" in first)
