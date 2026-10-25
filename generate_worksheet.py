"""Pembuatan Lembar Kerja Asistensi dalam format DOCX dan XLSX."""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from review_fields import (
    ACTIVE_STATUSES,
    CATEGORY_CAPS,
    CATEGORY_ORDER,
    SEVERITY_ORDER,
    format_rupiah,
    predikat,
    predikat_class,
    sort_findings,
    summarize,
)

NAVY = "16324F"
TEAL = "0F766E"
LIGHT_BLUE = "DCE6F1"
LIGHT_TEAL = "D9EFEC"
LIGHT_GRAY = "F2F4F7"
MID_GRAY = "D0D5DD"
RED = "B42318"
ORANGE = "B54708"
BLUE = "175CD3"
WHITE = "FFFFFF"

SEVERITY_FILL = {"Kritis": "F8CBAD", "Sedang": "FFE699", "Saran": "DDEBF7"}
STATUS_FILL = {
    "Sesuai": "C6EFCE",
    "Perlu Perbaikan": "FFEB9C",
    "Tidak Sesuai": "FFC7CE",
    "Tidak Terverifikasi": "E7E6E6",
    "Terbuka": "FCE4D6",
    "Diterima": "FFF2CC",
    "Dikoreksi": "C6EFCE",
    "Diabaikan": "E7E6E6",
}
LEVEL_FILL = {"Program": "DCE6F1", "Kegiatan": "EAF1F8", "Subkegiatan": "FFFFFF"}

MONTHS_ID = [
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]


# ----------------------------------------------------------------------
# Utilitas umum
# ----------------------------------------------------------------------
def today_indonesia() -> str:
    try:
        now = datetime.now(ZoneInfo("Asia/Makassar"))
    except Exception:
        now = datetime.now(timezone.utc)
    return f"{now.day} {MONTHS_ID[now.month - 1]} {now.year}"


def format_date_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return today_indonesia()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return f"{day} {MONTHS_ID[month - 1]} {year}"
    return text


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()


def _safe_excel(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = _clean(value)
    if text in {"-", "—", "–"}:
        return text
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text[:32000]


def _statistik(job: dict[str, Any]) -> dict[str, Any]:
    doc = job.get("doc") or {}
    return doc.get("statistik") or {}


def _identity(job: dict[str, Any]) -> dict[str, Any]:
    doc = job.get("doc") or {}
    identity = job.get("identity") or {}
    return {
        "opd": _clean(identity.get("opd") or (doc.get("opd") or {}).get("nama") or "................"),
        "tahun": _clean(identity.get("tahun") or doc.get("tahun") or "................"),
        "tanggal": format_date_id(identity.get("tanggal")),
        "nomor": _clean(identity.get("nomor") or "................"),
        "perencana": _clean(identity.get("perencana") or ".............................."),
        "nip_perencana": _clean(identity.get("nip_perencana") or ".............................."),
        "koordinator": _clean(identity.get("koordinator") or ".............................."),
        "nip_koordinator": _clean(identity.get("nip_koordinator") or ".............................."),
        "kepala": _clean(identity.get("kepala") or ".............................."),
        "nip_kepala": _clean(identity.get("nip_kepala") or ".............................."),
        "catatan": _clean(identity.get("catatan")),
    }


def _findings(job: dict[str, Any]) -> list[dict[str, Any]]:
    analysis = job.get("analysis") or {}
    return list(analysis.get("findings") or [])


def _score(job: dict[str, Any], key: str = "score") -> dict[str, Any]:
    analysis = job.get("analysis") or {}
    score = analysis.get(key) or {}
    if not score:
        score = analysis.get("score") or {}
    return score


def _final_score(job: dict[str, Any]) -> dict[str, Any]:
    analysis = job.get("analysis") or {}
    return analysis.get("score_final") or analysis.get("score") or {}


def _alignment(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list((job.get("analysis") or {}).get("alignment") or [])


def _narrative(job: dict[str, Any]) -> dict[str, Any]:
    return (job.get("analysis") or {}).get("narrative") or {}


def _is_sample(job: dict[str, Any]) -> bool:
    return bool((job.get("master") or {}).get("is_sample"))


def _matrix_rows(doc: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for program in doc.get("program", []):
        yield program, None, None, "Program"
        for kegiatan in program.get("kegiatan", []):
            yield program, kegiatan, None, "Kegiatan"
            for sub in kegiatan.get("subkegiatan", []):
                yield program, kegiatan, sub, "Subkegiatan"


def _indicator_lines(item: dict[str, Any], key: str) -> str:
    indicators = item.get("indikator") or []
    if not indicators:
        if key == "target":
            return _clean(item.get("target")) or "-"
        if key == "satuan":
            return _clean(item.get("satuan")) or "-"
        return "-"
    values = []
    for indicator in indicators:
        value = _clean(indicator.get(key))
        if value:
            values.append(value)
    return "\n".join(values) if values else "-"


def _indicator_names(item: dict[str, Any]) -> str:
    indicators = item.get("indikator") or []
    names = [_clean(indicator.get("nama")) for indicator in indicators if _clean(indicator.get("nama"))]
    if not names:
        return "-"
    if len(names) == 1:
        return names[0]
    return "\n".join(f"{index}. {name}" for index, name in enumerate(names, start=1))


def _item_pagu(item: dict[str, Any]) -> str:
    value = item.get("pagu")
    if isinstance(value, (int, float)):
        return format_rupiah(value)
    return "-"


def _label_item(item: dict[str, Any]) -> str:
    kode = _clean(item.get("kode"))
    nama = _clean(item.get("nama"))
    return f"{kode} {nama}".strip() or "(tanpa nama)"


# ----------------------------------------------------------------------
# DOCX helpers
# ----------------------------------------------------------------------
def _set_run(run, size: float = 9, bold: bool = False, color: str | None = None, italic: bool = False):
    run.font.name = "Arial"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _para(
    container,
    text: str = "",
    size: float = 9,
    bold: bool = False,
    color: str | None = None,
    align=WD_ALIGN_PARAGRAPH.LEFT,
    space_after: float = 0,
    space_before: float = 0,
    italic: bool = False,
    indent: float | None = None,
):
    paragraph = container.add_paragraph()
    paragraph.alignment = align
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.space_before = Pt(space_before)
    if indent is not None:
        paragraph.paragraph_format.left_indent = Cm(indent)
    if text:
        run = paragraph.add_run(text)
        _set_run(run, size=size, bold=bold, color=color, italic=italic)
    return paragraph


def _shade(cell, color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    tc_pr.append(shading)


def _cell_text(
    cell,
    text: Any,
    size: float = 8,
    bold: bool = False,
    color: str | None = None,
    align=WD_ALIGN_PARAGRAPH.LEFT,
    valign=WD_ALIGN_VERTICAL.CENTER,
) -> None:
    cell.vertical_alignment = valign
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.space_before = Pt(0)
    for index, line in enumerate(_clean(text).split("\n")):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(line)
        _set_run(run, size=size, bold=bold, color=color)


def _col_widths(table, widths_cm: list[float]) -> None:
    table.autofit = False
    for row in table.rows:
        for index, width in enumerate(widths_cm):
            if index < len(row.cells):
                row.cells[index].width = Cm(width)


def _repeat_header(table) -> None:
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _row_height(row, height_cm: float) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    height = OxmlElement("w:trHeight")
    height.set(qn("w:val"), str(int(height_cm * 567)))
    height.set(qn("w:hRule"), "atLeast")
    tr_pr.append(height)


def _section_title(document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(10)
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.paragraph_format.keep_with_next = True
    run = paragraph.add_run(text)
    _set_run(run, size=10.5, bold=True, color=NAVY)
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), TEAL)
    borders.append(bottom)
    p_pr.append(borders)


def _add_page_number(paragraph) -> None:
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)
    _set_run(run, size=8, color="667085")


def _signature_block(cell, role: str, name: str, nip: str) -> None:
    _cell_text(cell, role, size=8.5, align=WD_ALIGN_PARAGRAPH.CENTER, valign=WD_ALIGN_VERTICAL.TOP)
    _para(cell, "", size=8)
    _para(cell, "", size=8)
    _para(cell, "", size=8)
    _para(cell, "", size=8)
    _para(cell, name, size=9, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    _para(cell, f"NIP. {nip}", size=8.5, align=WD_ALIGN_PARAGRAPH.CENTER)


# ----------------------------------------------------------------------
# DOCX builder
# ----------------------------------------------------------------------
def build_docx(job: dict[str, Any]) -> io.BytesIO:
    doc_data = job.get("doc") or {}
    identity = _identity(job)
    findings = sort_findings(_findings(job))
    summary = summarize(findings)
    initial = _score(job, "score")
    final = _final_score(job)
    stat = _statistik(job)
    narrative = _narrative(job)
    alignment = _alignment(job)
    sample = _is_sample(job)

    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Cm(29.7)
    section.page_height = Cm(21)
    section.left_margin = Cm(1.4)
    section.right_margin = Cm(1.4)
    section.top_margin = Cm(1.2)
    section.bottom_margin = Cm(1.2)

    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(9)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")

    core = document.core_properties
    core.title = f"Lembar Kerja Asistensi Renja {identity['opd']} {identity['tahun']}"
    core.author = "Bappeda Kabupaten Soppeng"
    core.subject = "Asistensi Rencana Kinerja Perangkat Daerah"
    core.comments = "Dihasilkan oleh Sistem Asistensi Renja Bappeda Kabupaten Soppeng."

    # Header dan footer
    header_paragraph = section.header.paragraphs[0]
    header_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header_paragraph.add_run(f"Lembar Kerja Asistensi Renja — {identity['opd']} — {identity['tahun']}")
    _set_run(run, size=7.5, color="667085")
    footer_paragraph = section.footer.paragraphs[0]
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer_paragraph.add_run(
        "Dokumen dihasilkan dengan bantuan sistem. Verifikasi akhir dilakukan oleh perencana.   |   Halaman "
    )
    _set_run(run, size=8, color="667085")
    _add_page_number(footer_paragraph)

    # Kop
    _para(
        document,
        "PEMERINTAH KABUPATEN SOPPENG",
        size=11,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        color=NAVY,
    )
    _para(
        document,
        "BADAN PERENCANAAN PEMBANGUNAN DAERAH",
        size=14,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        color=NAVY,
    )
    kop_line = document.add_paragraph()
    kop_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kop_line.paragraph_format.space_after = Pt(2)
    run = kop_line.add_run("Jl. ____________________________  •  Kabupaten Soppeng")
    _set_run(run, size=7.5, color="667085", italic=True)
    _para(
        document,
        "LEMBAR KERJA ASISTENSI RENCANA KERJA (RENJA) PERANGKAT DAERAH",
        size=11.5,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_before=4,
        space_after=1,
    )
    _para(
        document,
        f"TAHUN ANGGARAN {identity['tahun']}",
        size=11,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=2,
    )
    _para(
        document,
        f"Nomor: {identity['nomor']}",
        size=8.5,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=6,
        color="475467",
    )

    if sample:
        warning_table = document.add_table(rows=1, cols=1)
        warning_table.style = "Table Grid"
        cell = warning_table.rows[0].cells[0]
        _shade(cell, "FEE4E2")
        _cell_text(
            cell,
            "PERHATIAN: Master nomenklatur yang digunakan masih berstatus CONTOH/SEED. "
            "Ganti dengan longlist resmi Permendagri 90/2019 dan pemutakhirannya sebelum "
            "lembar kerja ini digunakan untuk keputusan resmi.",
            size=8.5,
            bold=True,
            color=RED,
        )
        _para(document, "", size=4)

    # Identitas
    _section_title(document, "A. IDENTITAS DOKUMEN DAN HASIL ASISTENSI")
    identity_rows = [
        ("OPD", identity["opd"], "Tahun Anggaran", identity["tahun"]),
        ("Dokumen", _clean((doc_data.get("dokumen") or {}).get("judul")) or "Renja OPD", "Tanggal Asistensi", identity["tanggal"]),
        ("Jumlah Program", str(stat.get("program", 0)), "Jumlah Kegiatan", str(stat.get("kegiatan", 0))),
        ("Jumlah Subkegiatan", str(stat.get("subkegiatan", 0)), "Total Pagu Indikatif", format_rupiah(stat.get("pagu", 0))),
        (
            "Skor Awal",
            f"{initial.get('score', '-')} — {initial.get('predikat', '-')}",
            "Skor Setelah Review",
            f"{final.get('score', '-')} — {final.get('predikat', '-')}",
        ),
        (
            "Status Master",
            "CONTOH (wajib diganti)" if sample else "Master terunggah",
            "Format Sumber",
            _clean((doc_data.get("dokumen") or {}).get("format", "-")).upper(),
        ),
    ]
    table = document.add_table(rows=0, cols=4)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for left_label, left_value, right_label, right_value in identity_rows:
        row = table.add_row()
        _shade(row.cells[0], LIGHT_BLUE)
        _shade(row.cells[2], LIGHT_BLUE)
        _cell_text(row.cells[0], left_label, size=8.5, bold=True)
        _cell_text(row.cells[1], left_value, size=8.5)
        _cell_text(row.cells[2], right_label, size=8.5, bold=True)
        _cell_text(row.cells[3], right_value, size=8.5)
    _col_widths(table, [4.0, 9.4, 4.0, 9.3])
    _para(document, "", size=3)

    # Ringkasan
    _section_title(document, "B. RINGKASAN EKSEKUTIF")
    _para(
        document,
        narrative.get("ringkasan")
        or "Ringkasan belum tersedia. Silakan periksa daftar temuan dan matriks di bawah.",
        size=9,
        align=WD_ALIGN_PARAGRAPH.JUSTIFY,
        space_after=4,
    )
    summary_table = document.add_table(rows=1, cols=8)
    summary_table.style = "Table Grid"
    summary_headers = ["Total Temuan", "Kritis", "Sedang", "Saran", "Diterima", "Dikoreksi", "Diabaikan", "Terbuka"]
    for index, label in enumerate(summary_headers):
        cell = summary_table.rows[0].cells[index]
        _shade(cell, NAVY)
        _cell_text(cell, label, size=8, bold=True, color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
    row = summary_table.add_row()
    values = [
        summary["total"],
        summary["by_severity"].get("Kritis", 0),
        summary["by_severity"].get("Sedang", 0),
        summary["by_severity"].get("Saran", 0),
        summary["by_status"].get("Diterima", 0),
        summary["by_status"].get("Dikoreksi", 0),
        summary["by_status"].get("Diabaikan", 0),
        summary["by_status"].get("Terbuka", 0),
    ]
    for index, value in enumerate(values):
        _cell_text(row.cells[index], str(value), size=8.5, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    _col_widths(summary_table, [3.3, 3.0, 3.0, 3.0, 3.1, 3.1, 3.1, 3.1])

    # Matriks
    _section_title(document, "C. MATRIKS PROGRAM, KEGIATAN, DAN SUBKEGIATAN")
    headers = [
        "No",
        "Kode",
        "Uraian Program / Kegiatan / Subkegiatan",
        "Indikator Kinerja",
        "Target",
        "Satuan",
        "Pagu (Rp)",
        "Status Nomenklatur",
        "Catatan",
    ]
    widths = [0.9, 2.1, 6.2, 5.3, 1.8, 1.7, 2.6, 2.6, 3.6]
    matrix = document.add_table(rows=1, cols=len(headers))
    matrix.style = "Table Grid"
    matrix.alignment = WD_TABLE_ALIGNMENT.CENTER
    for index, header in enumerate(headers):
        cell = matrix.rows[0].cells[index]
        _shade(cell, NAVY)
        _cell_text(cell, header, size=7.5, bold=True, color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
    _repeat_header(matrix)

    for row_index, (program, kegiatan, sub, level) in enumerate(_matrix_rows(doc_data), start=1):
        item = sub or kegiatan or program
        row = matrix.add_row()
        fill = LEVEL_FILL.get(level, WHITE)
        for cell in row.cells:
            _shade(cell, fill)
        _cell_text(row.cells[0], str(row_index), size=7, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[1], _clean(item.get("kode")) or "-", size=7, bold=(level == "Program"))
        prefix = {"Program": "PROGRAM: ", "Kegiatan": "KEGIATAN: ", "Subkegiatan": "SUBKEGIATAN: "}[level]
        _cell_text(row.cells[2], prefix + (_clean(item.get("nama")) or "-"), size=7, bold=(level == "Program"))
        row.cells[2].paragraphs[0].paragraph_format.left_indent = Cm({"Program": 0.0, "Kegiatan": 0.35, "Subkegiatan": 0.7}[level])
        _cell_text(row.cells[3], _indicator_names(item), size=7)
        _cell_text(row.cells[4], _indicator_lines(item, "target"), size=7, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[5], _indicator_lines(item, "satuan"), size=7, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[6], _item_pagu(item), size=7, align=WD_ALIGN_PARAGRAPH.RIGHT)
        status = _clean(item.get("_status_nomenklatur")) or "Tidak Terverifikasi"
        status_cell = row.cells[7]
        _cell_text(status_cell, status, size=7, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _shade(status_cell, STATUS_FILL.get(status, "FFFFFF"))
        _cell_text(row.cells[8], _clean(item.get("_catatan_nomenklatur")) or "-", size=7)
    _col_widths(matrix, widths)
    _para(
        document,
        "Keterangan: status nomenklatur mengacu pada master yang diunggah. "
        "Matriks ini mengikuti urutan dokumen sumber; verifikasi terhadap longlist resmi tetap diperlukan.",
        size=7.5,
        italic=True,
        color="475467",
        space_before=2,
    )

    # Temuan
    _section_title(document, "D. DAFTAR TEMUAN DAN REKOMENDASI PERBAIKAN")
    finding_headers = [
        "No",
        "Tingkat",
        "Kategori",
        "Level",
        "Kode / Nama",
        "Uraian Temuan",
        "Rekomendasi Perbaikan",
        "Acuan",
        "Status Review",
        "Catatan Koreksi",
    ]
    finding_widths = [0.8, 1.4, 1.8, 1.6, 3.2, 5.4, 5.4, 2.2, 1.6, 3.0]
    ftable = document.add_table(rows=1, cols=len(finding_headers))
    ftable.style = "Table Grid"
    ftable.alignment = WD_TABLE_ALIGNMENT.CENTER
    for index, header in enumerate(finding_headers):
        cell = ftable.rows[0].cells[index]
        _shade(cell, NAVY)
        _cell_text(cell, header, size=7.5, bold=True, color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
    _repeat_header(ftable)
    if not findings:
        row = ftable.add_row()
        row.cells[0].merge(row.cells[-1])
        _cell_text(row.cells[0], "Tidak ditemukan temuan material.", size=8, align=WD_ALIGN_PARAGRAPH.CENTER)
    for index, finding in enumerate(findings, start=1):
        row = ftable.add_row()
        _cell_text(row.cells[0], str(index), size=7, align=WD_ALIGN_PARAGRAPH.CENTER)
        severity_cell = row.cells[1]
        _cell_text(severity_cell, finding.get("tingkat", ""), size=7, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _shade(severity_cell, SEVERITY_FILL.get(finding.get("tingkat"), "FFFFFF"))
        _cell_text(row.cells[2], finding.get("kategori", ""), size=7, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[3], finding.get("level", ""), size=7, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[4], _label_item(finding), size=7)
        _cell_text(row.cells[5], finding.get("pesan", ""), size=7)
        _cell_text(row.cells[6], finding.get("rekomendasi", ""), size=7)
        _cell_text(row.cells[7], finding.get("acuan", ""), size=6.5)
        status_cell = row.cells[8]
        _cell_text(status_cell, finding.get("status", "Terbuka"), size=7, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _shade(status_cell, STATUS_FILL.get(finding.get("status"), "FFFFFF"))
        _cell_text(row.cells[9], finding.get("catatan", "") or "-", size=7)
    _col_widths(ftable, finding_widths)

    # Penyelarasan
    _section_title(document, "E. CATATAN PENYELARASAN DENGAN RPJMD / RENSTRA / RKPD")
    if alignment:
        aheaders = ["Dokumen Acuan", "Status", "Diperiksa", "Selaras", "Belum Selaras", "Catatan"]
        atable = document.add_table(rows=1, cols=len(aheaders))
        atable.style = "Table Grid"
        atable.alignment = WD_TABLE_ALIGNMENT.CENTER
        for index, header in enumerate(aheaders):
            cell = atable.rows[0].cells[index]
            _shade(cell, NAVY)
            _cell_text(cell, header, size=7.5, bold=True, color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
        for item in alignment:
            row = atable.add_row()
            _cell_text(row.cells[0], item.get("acuan", ""), size=7.5, bold=True)
            status = item.get("status", "")
            _cell_text(row.cells[1], status, size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER)
            _shade(row.cells[1], STATUS_FILL.get(status, "FFFFFF"))
            _cell_text(row.cells[2], str(item.get("jumlah_diperiksa", 0)), size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(row.cells[3], str(item.get("jumlah_selaras", 0)), size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(row.cells[4], str(item.get("jumlah_belum", 0)), size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER)
            catatan = item.get("catatan", "")
            examples = item.get("contoh_belum") or []
            if examples:
                catatan += " Contoh belum selaras: " + "; ".join(
                    f"{example.get('kode', '')} {example.get('nama', '')}".strip() for example in examples[:4]
                )
            _cell_text(row.cells[5], catatan, size=7)
        _col_widths(atable, [4.5, 3.0, 2.0, 2.0, 2.3, 12.9])
    else:
        _para(
            document,
            "Belum ada dokumen acuan yang diunggah. Penyelarasan dengan RPJMD/Renstra/RKPD belum diperiksa.",
            size=8.5,
            italic=True,
            color=ORANGE,
        )

    # Rekomendasi umum
    _section_title(document, "F. REKOMENDASI UMUM DAN TINDAK LANJUT")
    recommendations = narrative.get("rekomendasi_umum") or []
    if recommendations:
        for index, recommendation in enumerate(recommendations, start=1):
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.6)
            paragraph.paragraph_format.first_line_indent = Cm(-0.6)
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(f"{index}. ")
            _set_run(run, size=9, bold=True)
            run = paragraph.add_run(_clean(recommendation))
            _set_run(run, size=9)
    else:
        _para(document, "Tidak ada rekomendasi umum tambahan.", size=9)

    _para(document, "", size=2)
    _para(
        document,
        "KESIMPULAN:",
        size=9,
        bold=True,
        space_before=3,
        space_after=1,
    )
    _para(
        document,
        narrative.get("kesimpulan") or "Kesimpulan belum tersedia.",
        size=9,
        align=WD_ALIGN_PARAGRAPH.JUSTIFY,
        space_after=4,
    )
    _para(
        document,
        "Catatan/tindak lanjut perencana:",
        size=8.5,
        bold=True,
        space_before=2,
    )
    for _ in range(3):
        _para(document, "................................................................................................................................................................", size=8.5, color="98A2B3")

    # Tanda tangan
    _section_title(document, "G. PARAF DAN TANDA TANGAN ASISTENSI")
    _para(
        document,
        f"Soppeng, {identity['tanggal']}",
        size=8.5,
        align=WD_ALIGN_PARAGRAPH.RIGHT,
        space_after=2,
    )
    sig_table = document.add_table(rows=1, cols=3)
    sig_table.style = "Table Grid"
    sig_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _signature_block(
        sig_table.rows[0].cells[0],
        "Disusun oleh,\nPerencana Ahli Pertama",
        identity["perencana"],
        identity["nip_perencana"],
    )
    _signature_block(
        sig_table.rows[0].cells[1],
        "Diverifikasi oleh,\nKoordinator Bidang Perencanaan",
        identity["koordinator"],
        identity["nip_koordinator"],
    )
    _signature_block(
        sig_table.rows[0].cells[2],
        "Disetujui oleh,\nKepala Bappeda Kabupaten Soppeng",
        identity["kepala"],
        identity["nip_kepala"],
    )
    _col_widths(sig_table, [8.9, 8.9, 8.9])

    _para(document, "", size=4)
    opd_table = document.add_table(rows=1, cols=2)
    opd_table.style = "Table Grid"
    left = opd_table.rows[0].cells[0]
    right = opd_table.rows[0].cells[1]
    _shade(left, LIGHT_GRAY)
    _cell_text(left, "CATATAN / TANGGAPAN OPD", size=8.5, bold=True, valign=WD_ALIGN_VERTICAL.TOP)
    _para(left, identity["catatan"] or "", size=8.5)
    _cell_text(right, "PARAF PERWAKILAN OPD", size=8.5, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, valign=WD_ALIGN_VERTICAL.TOP)
    _para(right, "", size=8)
    _para(right, "", size=8)
    _para(right, "(......................................)", size=8.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    _col_widths(opd_table, [17.6, 9.1])
    _row_height(opd_table.rows[0], 3.0)

    _para(document, "", size=8)
    _para(
        document,
        "Lembar kerja ini merupakan alat bantu asistensi. Keputusan akhir atas kebenaran nomenklatur, "
        "target, dan pagu tetap berada pada perencana dan OPD dengan merujuk pada peraturan serta dokumen "
        "perencanaan resmi yang berlaku.",
        size=7.5,
        italic=True,
        color="667085",
        align=WD_ALIGN_PARAGRAPH.JUSTIFY,
    )

    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer


# ----------------------------------------------------------------------
# XLSX helpers
# ----------------------------------------------------------------------
THIN = Side(style="thin", color="BFC5CE")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL_HEADER = PatternFill("solid", fgColor=NAVY)
FILL_SECTION = PatternFill("solid", fgColor=LIGHT_BLUE)
FILL_LIGHT = PatternFill("solid", fgColor=LIGHT_GRAY)
FONT_HEADER = Font(name="Arial", size=9, bold=True, color=WHITE)
FONT_BODY = Font(name="Arial", size=9)
FONT_SMALL = Font(name="Arial", size=8)


def _xl_title(ws, row: int, text: str, last_col: int, fill: str = NAVY, color: str = WHITE, size: int = 12) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    cell = ws.cell(row=row, column=1, value=_safe_excel(text))
    cell.fill = PatternFill("solid", fgColor=fill)
    cell.font = Font(name="Arial", size=size, bold=True, color=color)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 24 if size >= 12 else 20


def _xl_header(ws, row: int, headers: list[str]) -> None:
    for column, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=column, value=_safe_excel(header))
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 30


def _xl_row(ws, row: int, values: Iterable[Any], *, center: set[int] | None = None, bold: bool = False, fill: str | None = None) -> None:
    center = center or set()
    for column, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=column, value=_safe_excel(value))
        cell.font = Font(name="Arial", size=8.5, bold=bold)
        cell.alignment = Alignment(
            horizontal="center" if column in center else "left",
            vertical="top",
            wrap_text=True,
        )
        cell.border = BORDER
        if fill:
            cell.fill = PatternFill("solid", fgColor=fill)


def _xl_widths(ws, widths: list[float]) -> None:
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def _xl_autofit_height(ws, row: int, values: Iterable[Any], widths: list[float], minimum: float = 16, maximum: float = 120) -> None:
    lines = 1
    for index, value in enumerate(values):
        if index >= len(widths):
            break
        text = _clean(value)
        if not text:
            continue
        estimated = 0
        for part in text.split("\n"):
            estimated += max(1, len(part) // max(8, int(widths[index] * 1.05)) + 1)
        lines = max(lines, estimated)
    ws.row_dimensions[row].height = min(maximum, max(minimum, lines * 12))


def _xl_status_fill(ws, cell, status: str) -> None:
    color = STATUS_FILL.get(status)
    if color:
        cell.fill = PatternFill("solid", fgColor=color)


def _xl_page_setup(ws, title_rows: str | None = None) -> None:
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.35
    ws.page_margins.right = 0.35
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5
    if title_rows:
        ws.print_title_rows = title_rows
    ws.oddFooter.center.text = "Lembar Kerja Asistensi Renja - Halaman &P dari &N"
    ws.oddFooter.center.size = 8


def _xl_identity_sheet(ws, job: dict[str, Any], identity: dict[str, Any]) -> None:
    doc = job.get("doc") or {}
    stat = _statistik(job)
    initial = _score(job, "score")
    final = _final_score(job)
    _xl_page_setup(ws)
    ws.page_setup.fitToHeight = 1
    _xl_widths(ws, [28, 24, 30, 20, 24])
    _xl_title(ws, 1, "PEMERINTAH KABUPATEN SOPPENG", 5, size=14)
    _xl_title(ws, 2, "BADAN PERENCANAAN PEMBANGUNAN DAERAH", 5, fill=TEAL, size=14)
    _xl_title(ws, 3, "LEMBAR KERJA ASISTENSI RENCANA KERJA (RENJA) PERANGKAT DAERAH", 5, size=12)
    _xl_title(ws, 4, f"TAHUN ANGGARAN {identity['tahun']}", 5, fill=LIGHT_BLUE, color=NAVY, size=11)
    ws.merge_cells("A5:E5")
    ws["A5"] = _safe_excel(f"Nomor: {identity['nomor']}    |    Tanggal Asistensi: {identity['tanggal']}")
    ws["A5"].font = Font(name="Arial", size=9, italic=True, color="475467")
    ws["A5"].alignment = Alignment(horizontal="center")

    row = 7
    if _is_sample(job):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        cell = ws.cell(row=row, column=1, value=_safe_excel(
            "PERHATIAN: master nomenklatur masih CONTOH/SEED. Ganti dengan longlist resmi sebelum digunakan."
        ))
        cell.fill = PatternFill("solid", fgColor="FEE4E2")
        cell.font = Font(name="Arial", size=9, bold=True, color=RED)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER
        ws.row_dimensions[row].height = 28
        row += 2

    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    ws.cell(row=row, column=1, value="IDENTITAS DOKUMEN").fill = FILL_SECTION
    ws.cell(row=row, column=1).font = Font(name="Arial", size=10, bold=True, color=NAVY)
    ws.cell(row=row, column=1).border = BORDER
    row += 1
    fields = [
        ("OPD", identity["opd"]),
        ("Tahun Anggaran", identity["tahun"]),
        ("Judul Dokumen", _clean((doc.get("dokumen") or {}).get("judul")) or "Renja OPD"),
        ("Nama File", _clean((doc.get("dokumen") or {}).get("file"))),
        ("Format Sumber", _clean((doc.get("dokumen") or {}).get("format"))),
        ("Jumlah Program", stat.get("program", 0)),
        ("Jumlah Kegiatan", stat.get("kegiatan", 0)),
        ("Jumlah Subkegiatan", stat.get("subkegiatan", 0)),
        ("Total Pagu Indikatif", stat.get("pagu", 0)),
        ("Skor Awal", f"{initial.get('score', '-')} ({initial.get('predikat', '-')})"),
        ("Skor Setelah Review", f"{final.get('score', '-')} ({final.get('predikat', '-')})"),
        ("Status Master Nomenklatur", "CONTOH - wajib diganti" if _is_sample(job) else "Master terunggah"),
    ]
    for label, value in fields:
        ws.cell(row=row, column=1, value=_safe_excel(label)).font = Font(name="Arial", size=9, bold=True)
        ws.cell(row=row, column=1).fill = FILL_LIGHT
        ws.cell(row=row, column=1).border = BORDER
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
        cell = ws.cell(row=row, column=2, value=_safe_excel(value))
        cell.font = FONT_BODY
        cell.alignment = Alignment(horizontal="left", wrap_text=True, vertical="top")
        cell.border = BORDER
        if label == "Total Pagu Indikatif" and isinstance(value, (int, float)):
            cell.number_format = '"Rp" #,##0'
        ws.row_dimensions[row].height = 18
        row += 1

    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    ws.cell(row=row, column=1, value="CATATAN/TANGGAPAN OPD").fill = FILL_SECTION
    ws.cell(row=row, column=1).font = Font(name="Arial", size=10, bold=True, color=NAVY)
    ws.cell(row=row, column=1).border = BORDER
    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 5, end_column=5)
    cell = ws.cell(row=row, column=1, value=_safe_excel(identity["catatan"]))
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    cell.border = BORDER
    for r in range(row, row + 6):
        for c in range(1, 6):
            ws.cell(row=r, column=c).border = BORDER

    row += 8
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    ws.cell(row=row, column=1, value=(
        "Dokumen ini dihasilkan dengan bantuan sistem. Verifikasi akhir atas kebenaran nomenklatur, target, "
        "dan pagu tetap dilakukan oleh perencana dan OPD."
    )).font = Font(name="Arial", size=8, italic=True, color="667085")
    ws.cell(row=row, column=1).alignment = Alignment(wrap_text=True)


def _xl_summary_sheet(ws, job: dict[str, Any]) -> None:
    findings = sort_findings(_findings(job))
    summary = summarize(findings)
    score = _final_score(job)
    _xl_page_setup(ws)
    ws.page_setup.fitToHeight = 1
    _xl_widths(ws, [6, 28, 18, 18, 18, 18])
    _xl_title(ws, 1, "RINGKASAN HASIL ASISTENSI", 6, size=13)
    row = 3
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.cell(row=row, column=1, value="SKOR KESESUAIAN").fill = FILL_SECTION
    ws.cell(row=row, column=1).font = Font(name="Arial", size=10, bold=True, color=NAVY)
    row += 1
    score_rows = [
        ("Skor Akhir (0-100)", score.get("score", "-")),
        ("Predikat", score.get("predikat", "-")),
        ("Total Penalti", score.get("penalty", "-")),
        ("Temuan Aktif", summary["active"]),
        ("Temuan Selesai/Diabaikan", summary["resolved"]),
    ]
    for label, value in score_rows:
        for column in (1, 2):
            cell = ws.cell(row=row, column=column)
            cell.fill = FILL_LIGHT
            cell.border = BORDER
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        label_cell = ws.cell(row=row, column=1, value=_safe_excel(label))
        label_cell.font = Font(name="Arial", size=9, bold=True)
        label_cell.alignment = Alignment(vertical="center")

        for column in (3, 4):
            ws.cell(row=row, column=column).border = BORDER
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
        cell = ws.cell(row=row, column=3, value=_safe_excel(value))
        cell.font = Font(name="Arial", size=11, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
        row += 1

    row += 1
    headers = ["No", "Kategori", "Kritis", "Sedang", "Saran", "Total"]
    _xl_header(ws, row, headers)
    header_row = row
    row += 1
    total = {"Kritis": 0, "Sedang": 0, "Saran": 0}
    for index, category in enumerate(CATEGORY_ORDER, start=1):
        counts = {
            severity: sum(
                1 for finding in findings if finding.get("kategori") == category and finding.get("tingkat") == severity
            )
            for severity in ("Kritis", "Sedang", "Saran")
        }
        for severity in total:
            total[severity] += counts[severity]
        _xl_row(
            ws,
            row,
            [index, category, counts["Kritis"], counts["Sedang"], counts["Saran"], sum(counts.values())],
            center={1, 3, 4, 5, 6},
        )
        if sum(counts.values()) == 0:
            for column in range(1, 7):
                ws.cell(row=row, column=column).fill = PatternFill("solid", fgColor="F2F4F7")
        row += 1
    _xl_row(ws, row, ["", "TOTAL", total["Kritis"], total["Sedang"], total["Saran"], sum(total.values())], center={3, 4, 5, 6}, bold=True, fill="DCE6F1")
    ws.auto_filter.ref = f"A{header_row}:F{row - 1}"

    row += 2
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.cell(row=row, column=1, value="STATUS REVIEW TEMUAN").fill = FILL_SECTION
    ws.cell(row=row, column=1).font = Font(name="Arial", size=10, bold=True, color=NAVY)
    row += 1
    _xl_header(ws, row, ["No", "Status", "Jumlah", "Keterangan", "", ""])
    row += 1
    status_notes = {
        "Terbuka": "Belum ditinjau",
        "Diterima": "Valid, perlu diperbaiki OPD",
        "Dikoreksi": "Sudah diperbaiki",
        "Diabaikan": "Tidak relevan/positif palsu",
    }
    for index, (status, count) in enumerate(summary["by_status"].items(), start=1):
        _xl_row(ws, row, [index, status, count, status_notes.get(status, "")], center={1, 3})
        _xl_status_fill(ws, ws.cell(row=row, column=2), status)
        row += 1

    row += 2
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.cell(row=row, column=1, value="PENALTI PER KATEGORI (batas maksimum)").fill = FILL_SECTION
    ws.cell(row=row, column=1).font = Font(name="Arial", size=10, bold=True, color=NAVY)
    row += 1
    _xl_header(ws, row, ["No", "Kategori", "Penalti", "Batas", "", ""])
    row += 1
    penalties = score.get("penalty_per_kategori") or {}
    for index, category in enumerate(CATEGORY_ORDER, start=1):
        _xl_row(ws, row, [index, category, penalties.get(category, 0), CATEGORY_CAPS.get(category, 0)], center={1, 3, 4})
        row += 1


def _xl_matrix_sheet(ws, job: dict[str, Any]) -> None:
    doc = job.get("doc") or {}
    _xl_page_setup(ws, "1:5")
    widths = [5, 11, 13, 46, 38, 10, 10, 15, 18, 32]
    _xl_widths(ws, widths)
    _xl_title(ws, 1, "MATRIKS PROGRAM, KEGIATAN, DAN SUBKEGIATAN", 10, size=13)
    _xl_title(ws, 2, _identity(job)["opd"], 10, fill=TEAL, size=10)
    ws.merge_cells("A3:J3")
    ws["A3"] = _safe_excel(
        "Status nomenklatur mengacu pada master yang diunggah. Verifikasi terhadap longlist resmi tetap diperlukan."
    )
    ws["A3"].font = Font(name="Arial", size=8, italic=True, color="667085")
    ws["A3"].alignment = Alignment(horizontal="center")
    headers = ["No", "Level", "Kode", "Uraian", "Indikator Kinerja", "Target", "Satuan", "Pagu (Rp)", "Status Nomenklatur", "Catatan"]
    _xl_header(ws, 5, headers)
    row = 6
    for index, (program, kegiatan, sub, level) in enumerate(_matrix_rows(doc), start=1):
        item = sub or kegiatan or program
        values = [
            index,
            level,
            _clean(item.get("kode")) or "-",
            _clean(item.get("nama")) or "-",
            _indicator_names(item),
            _indicator_lines(item, "target"),
            _indicator_lines(item, "satuan"),
            item.get("pagu") if isinstance(item.get("pagu"), (int, float)) else "-",
            _clean(item.get("_status_nomenklatur")) or "Tidak Terverifikasi",
            _clean(item.get("_catatan_nomenklatur")) or "-",
        ]
        _xl_row(ws, row, values, center={1, 2, 3, 6, 7, 8, 9}, bold=(level == "Program"), fill=LEVEL_FILL.get(level))
        if isinstance(item.get("pagu"), (int, float)):
            ws.cell(row=row, column=8).number_format = '"Rp" #,##0'
        if level == "Kegiatan":
            ws.cell(row=row, column=4).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True, indent=1)
        elif level == "Subkegiatan":
            ws.cell(row=row, column=4).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True, indent=2)
        _xl_status_fill(ws, ws.cell(row=row, column=9), values[8])
        _xl_autofit_height(ws, row, values, widths)
        row += 1
    if row > 6:
        ws.auto_filter.ref = f"A5:J{row - 1}"
    ws.freeze_panes = "A6"


def _xl_findings_sheet(ws, job: dict[str, Any]) -> None:
    findings = sort_findings(_findings(job))
    _xl_page_setup(ws, "1:5")
    widths = [5, 9, 13, 11, 14, 30, 45, 45, 26, 12, 30, 8]
    _xl_widths(ws, widths)
    _xl_title(ws, 1, "DAFTAR TEMUAN DAN REKOMENDASI PERBAIKAN", 12, size=13)
    _xl_title(ws, 2, _identity(job)["opd"], 12, fill=TEAL, size=10)
    ws.merge_cells("A3:L3")
    ws["A3"] = _safe_excel(
        "Status review: Terbuka (belum ditinjau), Diterima (valid), Dikoreksi (sudah diperbaiki), "
        "Diabaikan (tidak relevan). Skor akhir hanya menghitung temuan berstatus Terbuka/Diterima."
    )
    ws["A3"].font = Font(name="Arial", size=8, italic=True, color="667085")
    ws["A3"].alignment = Alignment(horizontal="center")
    headers = [
        "No",
        "Tingkat",
        "Kategori",
        "Level",
        "Kode",
        "Nama",
        "Uraian Temuan",
        "Rekomendasi Perbaikan",
        "Acuan",
        "Status Review",
        "Catatan Koreksi",
        "Sumber",
    ]
    _xl_header(ws, 5, headers)
    row = 6
    if not findings:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
        ws.cell(row=row, column=1, value="Tidak ditemukan temuan material.").alignment = Alignment(horizontal="center")
    for index, finding in enumerate(findings, start=1):
        values = [
            index,
            finding.get("tingkat", ""),
            finding.get("kategori", ""),
            finding.get("level", ""),
            finding.get("kode", ""),
            finding.get("nama", ""),
            finding.get("pesan", ""),
            finding.get("rekomendasi", ""),
            finding.get("acuan", ""),
            finding.get("status", "Terbuka"),
            finding.get("catatan", ""),
            finding.get("sumber", "rule"),
        ]
        _xl_row(ws, row, values, center={1, 2, 4, 10})
        ws.cell(row=row, column=2).fill = PatternFill("solid", fgColor=SEVERITY_FILL.get(values[1], "FFFFFF"))
        ws.cell(row=row, column=2).font = Font(name="Arial", size=8.5, bold=True)
        _xl_status_fill(ws, ws.cell(row=row, column=10), values[9])
        _xl_autofit_height(ws, row, values, widths)
        row += 1
    if row > 6:
        ws.auto_filter.ref = f"A5:L{row - 1}"
    ws.freeze_panes = "A6"


def _xl_alignment_sheet(ws, job: dict[str, Any]) -> None:
    _xl_page_setup(ws)
    widths = [5, 30, 18, 12, 12, 14, 65]
    _xl_widths(ws, widths)
    _xl_title(ws, 1, "CATATAN PENYELARASAN DENGAN RPJMD / RENSTRA / RKPD", 7, size=13)
    headers = ["No", "Dokumen Acuan", "Status", "Diperiksa", "Selaras", "Belum Selaras", "Catatan"]
    _xl_header(ws, 3, headers)
    row = 4
    alignment = _alignment(job)
    if not alignment:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        ws.cell(row=row, column=1, value=(
            "Belum ada dokumen acuan yang diunggah. Penyelarasan dengan RPJMD/Renstra/RKPD belum diperiksa."
        )).alignment = Alignment(horizontal="center", wrap_text=True)
    for index, item in enumerate(alignment, start=1):
        catatan = _clean(item.get("catatan"))
        examples = item.get("contoh_belum") or []
        if examples:
            catatan += " Contoh belum selaras: " + "; ".join(
                f"{example.get('kode', '')} {example.get('nama', '')}".strip() for example in examples[:6]
            )
        values = [
            index,
            item.get("acuan", ""),
            item.get("status", ""),
            item.get("jumlah_diperiksa", 0),
            item.get("jumlah_selaras", 0),
            item.get("jumlah_belum", 0),
            catatan,
        ]
        _xl_row(ws, row, values, center={1, 3, 4, 5, 6})
        _xl_status_fill(ws, ws.cell(row=row, column=3), values[2])
        _xl_autofit_height(ws, row, values, widths)
        row += 1
    row += 2
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    ws.cell(row=row, column=1, value=(
        "Metode penyelarasan berbasis kemiripan teks dan struktur, bukan verifikasi penuh matriks target. "
        "Perencana wajib memverifikasi kesesuaian target dan pagu pada dokumen RKPD/Renstra/RPJMD."
    )).font = Font(name="Arial", size=8, italic=True, color="667085")
    ws.cell(row=row, column=1).alignment = Alignment(wrap_text=True)


def _xl_signature_sheet(ws, job: dict[str, Any]) -> None:
    identity = _identity(job)
    _xl_page_setup(ws)
    _xl_widths(ws, [30, 30, 30])
    _xl_title(ws, 1, "PARAF DAN TANDA TANGAN ASISTENSI", 3, size=13)
    ws.merge_cells("A3:C3")
    ws["A3"] = _safe_excel(f"Soppeng, {identity['tanggal']}")
    ws["A3"].font = Font(name="Arial", size=9)
    ws["A3"].alignment = Alignment(horizontal="right")
    roles = [
        ("Disusun oleh,\nPerencana Ahli Pertama", identity["perencana"], identity["nip_perencana"]),
        ("Diverifikasi oleh,\nKoordinator Bidang Perencanaan", identity["koordinator"], identity["nip_koordinator"]),
        ("Disetujui oleh,\nKepala Bappeda Kabupaten Soppeng", identity["kepala"], identity["nip_kepala"]),
    ]
    row = 5
    for column, (role, name, nip) in enumerate(roles, start=1):
        cell = ws.cell(row=row, column=column, value=_safe_excel(role))
        cell.font = Font(name="Arial", size=9, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
        ws.row_dimensions[row].height = 34
    for r in range(row + 1, row + 5):
        for c in range(1, 4):
            ws.cell(row=r, column=c).border = BORDER
    row += 5
    for column, (_, name, nip) in enumerate(roles, start=1):
        cell = ws.cell(row=row, column=column, value=_safe_excel(name))
        cell.font = Font(name="Arial", size=9, bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.border = BORDER
        cell2 = ws.cell(row=row + 1, column=column, value=_safe_excel(f"NIP. {nip}"))
        cell2.font = Font(name="Arial", size=8.5)
        cell2.alignment = Alignment(horizontal="center")
        cell2.border = BORDER

    row += 4
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    ws.cell(row=row, column=1, value="CATATAN / TANGGAPAN OPD").fill = FILL_SECTION
    ws.cell(row=row, column=1).font = Font(name="Arial", size=10, bold=True, color=NAVY)
    ws.cell(row=row, column=1).border = BORDER
    for r in range(row + 1, row + 8):
        for c in range(1, 4):
            ws.cell(row=r, column=c).border = BORDER
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 7, end_column=3)
    ws.cell(row=row + 1, column=1, value=_safe_excel(identity["catatan"]))
    ws.cell(row=row + 1, column=1).alignment = Alignment(wrap_text=True, vertical="top")


# ----------------------------------------------------------------------
# XLSX builder
# ----------------------------------------------------------------------
def build_xlsx(job: dict[str, Any]) -> io.BytesIO:
    workbook = Workbook()
    identity = _identity(job)
    ws_identity = workbook.active
    ws_identity.title = "Identitas"
    _xl_identity_sheet(ws_identity, job, identity)
    _xl_summary_sheet(workbook.create_sheet("Ringkasan"), job)
    _xl_matrix_sheet(workbook.create_sheet("Matriks"), job)
    _xl_findings_sheet(workbook.create_sheet("Temuan"), job)
    _xl_alignment_sheet(workbook.create_sheet("Penyelarasan"), job)
    _xl_signature_sheet(workbook.create_sheet("Paraf"), job)

    workbook.properties.title = f"Lembar Kerja Asistensi Renja {identity['opd']} {identity['tahun']}"
    workbook.properties.creator = "Bappeda Kabupaten Soppeng"
    workbook.properties.subject = "Asistensi Rencana Kinerja Perangkat Daerah"
    workbook.properties.description = "Dihasilkan oleh Sistem Asistensi Renja Bappeda Kabupaten Soppeng."
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def safe_filename(value: str, fallback: str = "lembar-kerja-asistensi") -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or fallback)[:90]
