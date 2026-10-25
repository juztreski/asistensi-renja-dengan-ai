"""Sistem Asistensi Rencana Kinerja Perangkat Daerah - Bappeda Kabupaten Soppeng.

Alur: unggah dokumen -> ekstraksi -> analisis rule-based (+ LLM opsional) ->
review temuan -> unduh Lembar Kerja Asistensi (DOCX/XLSX).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

import llm_analyzer
import settings_store
from align_check import analyze_document, recompute_after_review
from extract_renja import document_statistics, extract_reference, extract_renja, utc_now_iso
from generate_worksheet import (
    build_docx,
    build_xlsx,
    format_date_id,
    safe_filename,
    today_indonesia,
)
from job_store import JobStore
from master_nomenklatur import MasterNomenklatur
from review_fields import (
    CATEGORY_ORDER,
    SEVERITY_ORDER,
    STATUS_OPTIONS,
    STATUS_VALUES,
    apply_review,
    format_rupiah,
    normalize_status,
    predikat_class,
    sort_findings,
    summarize,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_ROOT = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads"))).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
DEFAULT_MASTER_PATH = Path(os.environ.get("MASTER_PATH", str(BASE_DIR / "data" / "nomenklatur_kab_kota.json")))

ALLOWED_RENJA = {".pdf", ".docx", ".xlsx", ".csv"}
ALLOWED_REFERENCE = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}
ALLOWED_MASTER = {".json", ".csv"}
MAX_TOTAL_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "60")) * 1024 * 1024
MAX_FILE_UPLOAD = int(os.environ.get("MAX_FILE_MB", "25")) * 1024 * 1024
MASTER_MAX_BYTES = int(os.environ.get("MAX_MASTER_MB", "15")) * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", str(6 * 60 * 60)))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("asistensi")


def _secret_key() -> str:
    env = os.environ.get("FLASK_SECRET_KEY")
    if env:
        return env
    path = BASE_DIR / ".flask_secret"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        key = secrets.token_hex(32)
        path.write_text(key, encoding="utf-8")
        return key
    except OSError:
        return secrets.token_hex(32)


def _valid_workdir(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved == UPLOAD_ROOT or UPLOAD_ROOT in resolved.parents
    except OSError:
        return False


def _cleanup_orphans() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=JOB_TTL_SECONDS)
    for child in UPLOAD_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
            if mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret_key(),
    MAX_CONTENT_LENGTH=MAX_TOTAL_UPLOAD,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    JSON_SORT_KEYS=False,
)
store = JobStore(ttl_seconds=JOB_TTL_SECONDS, max_jobs=250, workdir_validator=_valid_workdir)
_cleanup_orphans()


# ----------------------------------------------------------------------
# Utilitas
# ----------------------------------------------------------------------
def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _check_csrf() -> None:
    expected = session.get("_csrf_token", "")
    provided = request.form.get("csrf_token", "")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        abort(400)


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            flash("Masuk sebagai administrator untuk membuka panel pengaturan.", "info")
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _start_admin_session() -> None:
    session.clear()
    session.permanent = True
    session["admin_authenticated"] = True
    session["_csrf_token"] = secrets.token_urlsafe(32)


def _safe_next(target: str | None) -> str:
    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return ""
    if target.startswith("/admin/login"):
        return ""
    return target


def _ai_api_key_source() -> str:
    if settings_store.load()["gemini_api_key"]:
        return "panel"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "env"
    return ""


@app.template_filter("rupiah")
def _rupiah(value):
    return format_rupiah(value)


@app.template_filter("tanggal_id")
def _tanggal_id(value):
    return format_date_id(value)


@app.context_processor
def inject_globals():
    return {
        "SEVERITY_ORDER": SEVERITY_ORDER,
        "CATEGORY_ORDER": CATEGORY_ORDER,
        "STATUS_OPTIONS": STATUS_OPTIONS,
        "STATUS_VALUES": STATUS_VALUES,
        "predikat_class": predikat_class,
        "now_year": datetime.now().year,
        "app_version": "1.0.0",
        "llm": llm_analyzer.status(),
        "max_upload_mb": MAX_TOTAL_UPLOAD // (1024 * 1024),
        "admin_logged_in": bool(session.get("admin_authenticated")),
        "admin_configured": settings_store.is_admin_configured(),
        "csrf_token": _csrf_token,
    }


def _allowed(filename: str, allowed: set[str]) -> bool:
    return Path(filename).suffix.lower() in allowed


def _save_upload(storage, workdir: Path, allowed: set[str], label: str) -> dict | None:
    if storage is None or not storage.filename:
        return None
    original = storage.filename
    if not _allowed(original, allowed):
        raise ValueError(f"Format file {label} tidak didukung: {Path(original).suffix or '(tanpa ekstensi)'}.")
    safe = secure_filename(original)
    if not safe:
        safe = f"dokumen{Path(original).suffix.lower()}"
    stored = f"{label}_{uuid.uuid4().hex[:8]}_{safe}"
    target = workdir / stored
    storage.save(target)
    size = target.stat().st_size
    if size > MAX_FILE_UPLOAD:
        target.unlink(missing_ok=True)
        raise ValueError(f"File {label} melebihi batas {MAX_FILE_UPLOAD // (1024 * 1024)} MB.")
    if size == 0:
        target.unlink(missing_ok=True)
        raise ValueError(f"File {label} kosong.")
    return {"path": target, "filename": original, "stored": stored, "size": size}


def _load_master(storage) -> MasterNomenklatur:
    if storage is None:
        return MasterNomenklatur.load_default(DEFAULT_MASTER_PATH)
    if isinstance(storage, (str, Path)):
        master = MasterNomenklatur.from_file(storage)
    else:
        if not storage.filename:
            return MasterNomenklatur.load_default(DEFAULT_MASTER_PATH)
        if not _allowed(storage.filename, ALLOWED_MASTER):
            raise ValueError("Master harus berformat JSON atau CSV.")
        data = storage.read(MASTER_MAX_BYTES + 1)
        if len(data) > MASTER_MAX_BYTES:
            raise ValueError(f"Master melebihi batas {MASTER_MAX_BYTES // (1024 * 1024)} MB.")
        master = MasterNomenklatur.from_bytes(data, storage.filename)
    if master.counts()["program"] == 0:
        raise ValueError("Master tidak memuat program yang valid. Periksa header/format file.")
    return master


def _reference_year(text: str) -> str:
    match = re.search(r"TAHUN\s+(?:ANGGARAN\s+)?(20\d{2})", text.upper())
    if match:
        return match.group(1)
    match = re.search(r"\b(20\d{2})\b", text.upper())
    return match.group(1) if match else ""


def _build_references(files: dict[str, dict | None]) -> list[dict]:
    labels = {
        "rpjmd": "RPJMD",
        "renstra": "Renstra OPD",
        "rkpd": "RKPD",
    }
    references = []
    for key, uploaded in files.items():
        if not uploaded:
            continue
        reference = extract_reference(uploaded["path"], uploaded["filename"])
        label = labels.get(key, key.upper())
        year = _reference_year(reference.get("teks", ""))
        if year and year not in label:
            label = f"{label} {year}"
        references.append(
            {
                "jenis": key,
                "nama": label,
                "nama_file": uploaded["filename"],
                "teks": reference.get("teks", ""),
                "jumlah_karakter": reference.get("jumlah_karakter", 0),
                "warnings": reference.get("warnings", []),
            }
        )
    return references


def _identity_from_form(form, doc: dict, existing: dict | None = None) -> dict:
    existing = existing or {}
    fields = {
        "opd": form.get("opd", "").strip(),
        "tahun": form.get("tahun", "").strip(),
        "tanggal": form.get("tanggal", "").strip(),
        "nomor": form.get("nomor", "").strip(),
        "perencana": form.get("perencana", "").strip(),
        "nip_perencana": form.get("nip_perencana", "").strip(),
        "koordinator": form.get("koordinator", "").strip(),
        "nip_koordinator": form.get("nip_koordinator", "").strip(),
        "kepala": form.get("kepala", "").strip(),
        "nip_kepala": form.get("nip_kepala", "").strip(),
        "catatan": form.get("catatan", "").strip(),
    }
    identity = dict(existing)
    for key, value in fields.items():
        if value or key not in identity:
            identity[key] = value
    if not identity.get("opd"):
        identity["opd"] = (doc.get("opd") or {}).get("nama") or ""
    if not identity.get("tahun"):
        identity["tahun"] = doc.get("tahun") or ""
    if not identity.get("tanggal"):
        identity["tanggal"] = datetime.now().date().isoformat()
    return identity


def _merge_llm_alignment(analysis: dict, llm_result: dict | None) -> None:
    if not llm_result:
        return
    for suggestion in llm_result.get("penyelarasan", []) or []:
        acuan = str(suggestion.get("acuan") or "").strip()
        catatan = str(suggestion.get("catatan") or "").strip()
        if not acuan or not catatan:
            continue
        existing = next(
            (item for item in analysis.get("alignment", []) if item.get("acuan", "").lower() == acuan.lower()),
            None,
        )
        if existing:
            if catatan not in existing.get("catatan", ""):
                existing["catatan"] = f"{existing.get('catatan', '')} Catatan AI: {catatan}".strip()
        else:
            analysis.setdefault("alignment", []).append(
                {
                    "acuan": acuan,
                    "nama_file": "",
                    "status": str(suggestion.get("status") or "Tidak Diperiksa"),
                    "jumlah_diperiksa": 0,
                    "jumlah_selaras": 0,
                    "jumlah_belum": 0,
                    "contoh_belum": [],
                    "catatan": f"Catatan AI: {catatan}",
                }
            )


def _attach_reference_warnings(analysis: dict, references: list[dict]) -> None:
    warnings = list(analysis.get("warnings") or [])
    for reference in references:
        for warning in reference.get("warnings") or []:
            text = f"{reference.get('nama', 'Dokumen acuan')}: {warning}"
            if text not in warnings:
                warnings.append(text)
    analysis["warnings"] = warnings


def _regenerate_final(analysis: dict) -> None:
    findings = sort_findings(analysis.get("findings") or [])
    recomputed = recompute_after_review(findings)
    analysis["findings"] = findings
    analysis["score_final"] = recomputed["score"]
    analysis["summary_final"] = recomputed["summary"]


def _get_job_or_404(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        abort(404)
    return job


def _default_master_summary() -> dict:
    try:
        return MasterNomenklatur.load_default(DEFAULT_MASTER_PATH).summary()
    except Exception as exc:  # pragma: no cover
        LOGGER.exception("Gagal memuat master bawaan")
        return {
            "meta": {"sumber": "Master gagal dimuat", "status": "ERROR", "catatan": str(exc)},
            "counts": {"program": 0, "kegiatan": 0, "subkegiatan": 0},
            "is_sample": True,
            "warnings": [str(exc)],
        }


# ----------------------------------------------------------------------
# Panel admin
# ----------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_authenticated"):
        return redirect(url_for("admin_panel"))
    configured = settings_store.is_admin_configured()
    next_url = _safe_next(request.values.get("next"))

    if request.method == "POST":
        _check_csrf()
        password = request.form.get("password", "")
        if not configured:
            confirm = request.form.get("confirm", "")
            if len(password) < settings_store.MIN_PASSWORD_LENGTH:
                flash(f"Kata sandi minimal {settings_store.MIN_PASSWORD_LENGTH} karakter.", "error")
            elif password != confirm:
                flash("Konfirmasi kata sandi tidak cocok.", "error")
            else:
                settings_store.set_admin_password(password)
                _start_admin_session()
                LOGGER.info("Akun admin dibuat dari %s", request.remote_addr)
                flash("Kata sandi admin berhasil dibuat.", "success")
                return redirect(url_for("admin_panel"))
        elif settings_store.verify_admin_password(password):
            _start_admin_session()
            LOGGER.info("Admin masuk dari %s", request.remote_addr)
            flash("Berhasil masuk sebagai administrator.", "success")
            return redirect(next_url or url_for("admin_panel"))
        else:
            LOGGER.warning("Login admin gagal dari %s", request.remote_addr)
            flash("Kata sandi admin salah.", "error")
    return render_template("admin_login.html", configured=configured, next_url=next_url)


@app.get("/admin")
@_admin_required
def admin_panel():
    data = settings_store.load()
    source = _ai_api_key_source()
    return render_template(
        "admin.html",
        settings=data,
        key_masked=settings_store.mask_secret(settings_store.effective_api_key()),
        key_source=source,
        key_from_env=source == "env",
        llm=llm_analyzer.status(),
        master=_default_master_summary(),
        jobs=store.stats(),
        settings_path=str(settings_store.path()),
        env_llm_disabled=os.environ.get("LLM_ENABLED", "").strip().lower() in {"0", "false", "no", "off"},
    )


@app.post("/admin/settings")
@_admin_required
def admin_settings():
    _check_csrf()
    form = request.form
    model = form.get("gemini_model", "").strip()
    if model and not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", model):
        flash("Nama model tidak valid. Gunakan huruf, angka, titik, garis bawah, titik dua, atau tanda hubung.", "error")
        return redirect(url_for("admin_panel"))

    key_input = form.get("gemini_api_key", "").strip()
    changes: dict[str, object] = {
        "gemini_model": model,
        "llm_enabled": form.get("llm_enabled") == "on",
    }
    cleared = form.get("clear_api_key") == "on"
    if cleared:
        changes["gemini_api_key"] = ""
    elif key_input:
        if len(key_input) > 512 or any(character.isspace() for character in key_input):
            flash("Kunci API tidak valid: tidak boleh memuat spasi dan maksimal 512 karakter.", "error")
            return redirect(url_for("admin_panel"))
        changes["gemini_api_key"] = key_input

    settings_store.update(**changes)
    LOGGER.info("Pengaturan analisis AI diperbarui oleh admin")
    flash("Kunci API dihapus. Analisis AI akan memakai variabel lingkungan bila tersedia." if cleared else "Pengaturan analisis AI disimpan.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/password")
@_admin_required
def admin_password():
    _check_csrf()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not settings_store.verify_admin_password(current):
        flash("Kata sandi saat ini salah.", "error")
    elif len(new) < settings_store.MIN_PASSWORD_LENGTH:
        flash(f"Kata sandi baru minimal {settings_store.MIN_PASSWORD_LENGTH} karakter.", "error")
    elif new != confirm:
        flash("Konfirmasi kata sandi baru tidak cocok.", "error")
    else:
        settings_store.set_admin_password(new)
        LOGGER.info("Kata sandi admin diperbarui")
        flash("Kata sandi admin berhasil diperbarui.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/jobs/clear")
@_admin_required
def admin_clear_jobs():
    _check_csrf()
    removed = store.clear()
    LOGGER.info("Admin menghapus %s pekerjaan sementara", removed)
    flash(f"{removed} pekerjaan sementara dan berkas unggahannya dihapus.", "success")
    return redirect(url_for("admin_panel"))


@app.post("/admin/logout")
def admin_logout():
    _check_csrf()
    session.clear()
    flash("Anda telah keluar dari panel admin.", "success")
    return redirect(url_for("index"))


# ----------------------------------------------------------------------
# Rute
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return render_template(
        "index.html",
        master=_default_master_summary(),
        today=datetime.now().date().isoformat(),
        default_tahun=datetime.now().year + 1,
    )


@app.post("/analyze")
def analyze():
    workdir = Path(tempfile.mkdtemp(prefix="job_", dir=str(UPLOAD_ROOT)))
    job_id = store.create({"workdir": str(workdir)})
    try:
        renja_upload = request.files.get("renja")
        if renja_upload is None or not renja_upload.filename:
            raise ValueError("Dokumen Renja wajib diunggah.")
        renja_file = _save_upload(renja_upload, workdir, ALLOWED_RENJA, "renja")
        if renja_file is None:
            raise ValueError("Dokumen Renja gagal disimpan.")

        reference_uploads = {
            "rpjmd": _save_upload(request.files.get("rpjmd"), workdir, ALLOWED_REFERENCE, "rpjmd"),
            "renstra": _save_upload(request.files.get("renstra"), workdir, ALLOWED_REFERENCE, "renstra"),
            "rkpd": _save_upload(request.files.get("rkpd"), workdir, ALLOWED_REFERENCE, "rkpd"),
        }
        master_file = _save_upload(request.files.get("master"), workdir, ALLOWED_MASTER, "master")
        master = _load_master(master_file["path"] if master_file else None)

        doc = extract_renja(renja_file["path"], renja_file["filename"], use_llm=False, master=master)
        identity = _identity_from_form(request.form, doc)
        if identity.get("opd"):
            doc.setdefault("opd", {})["nama"] = identity["opd"]
        if identity.get("tahun"):
            doc["tahun"] = identity["tahun"]
        doc["statistik"] = document_statistics(doc)

        references = _build_references(reference_uploads)
        use_llm = request.form.get("use_llm") == "on"

        rule_analysis = analyze_document(
            doc,
            master,
            references,
            extraction_warnings=doc.get("warnings", []),
        )
        analysis = rule_analysis
        llm_result = None
        if use_llm and llm_analyzer.is_enabled():
            llm_result = llm_analyzer.analyze(doc, master, references, rule_analysis["findings"])
            if llm_result.get("findings") or llm_result.get("ringkasan"):
                analysis = analyze_document(
                    doc,
                    master,
                    references,
                    llm_result=llm_result,
                    extraction_warnings=doc.get("warnings", []),
                )
                _merge_llm_alignment(analysis, llm_result)
            else:
                analysis["llm"] = {
                    "enabled": True,
                    "error": llm_result.get("error", ""),
                    "model": llm_result.get("model", ""),
                }
                if llm_result.get("error"):
                    analysis.setdefault("warnings", []).append(
                        f"Analisis LLM tidak digunakan: {llm_result['error']}"
                    )
        elif use_llm and not llm_analyzer.is_enabled():
            reason = llm_analyzer.status().get("reason") or "Analisis AI tidak tersedia."
            analysis["warnings"].append(f"Analisis AI diminta, tetapi tidak dijalankan. {reason}")

        _attach_reference_warnings(analysis, references)
        _regenerate_final(analysis)

        job = {
            "id": job_id,
            "workdir": str(workdir),
            "created_at": utc_now_iso(),
            "doc": doc,
            "references": references,
            "master": master.summary(),
            "identity": identity,
            "files": {
                "renja": {"filename": renja_file["filename"], "size": renja_file["size"]},
                "master": {"filename": master_file["filename"]} if master_file else None,
                "referensi": {
                    key: {"filename": uploaded["filename"]}
                    for key, uploaded in reference_uploads.items()
                    if uploaded
                },
            },
            "analysis": analysis,
            "llm": analysis.get("llm", {}),
            "reviewed": False,
        }
        store.set(job_id, job)
        LOGGER.info("Job %s dibuat: %s (%s)", job_id, renja_file["filename"], identity.get("opd"))
        flash("Dokumen berhasil dianalisis. Tinjau temuan sebelum membuat lembar kerja.", "success")
        return redirect(url_for("review", job_id=job_id))
    except ValueError as exc:
        store.delete(job_id)
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except Exception:
        LOGGER.exception("Gagal menganalisis dokumen")
        store.delete(job_id)
        flash("Terjadi kesalahan saat memproses dokumen. Periksa format file atau hubungi administrator.", "error")
        return redirect(url_for("index"))


@app.get("/review/<job_id>")
def review(job_id: str):
    job = _get_job_or_404(job_id)
    analysis = job.get("analysis") or {}
    findings = sort_findings(analysis.get("findings") or [])
    analysis["findings"] = findings
    return render_template(
        "review.html",
        job=job,
        doc=job.get("doc") or {},
        analysis=analysis,
        findings=findings,
        summary=analysis.get("summary_final") or summarize(findings),
        identity=job.get("identity") or {},
        score_initial=analysis.get("score") or {},
        score_final=analysis.get("score_final") or analysis.get("score") or {},
        alignment=analysis.get("alignment") or [],
        warnings=analysis.get("warnings") or [],
    )


@app.post("/review/<job_id>")
def review_post(job_id: str):
    job = _get_job_or_404(job_id)
    analysis = job.get("analysis") or {}
    findings = analysis.get("findings") or []
    identity = _identity_from_form(request.form, job.get("doc") or {}, job.get("identity") or {})

    bulk = request.form.get("bulk", "").strip()
    if bulk:
        if bulk not in STATUS_VALUES:
            flash("Aksi massal tidak dikenal.", "error")
            return redirect(url_for("review", job_id=job_id))
        for finding in findings:
            finding["status"] = normalize_status(bulk)
            if bulk == "Terbuka":
                finding["catatan"] = ""
        changed = len(findings)
        flash(f"Status {len(findings)} temuan diubah menjadi {bulk}.", "success")
        action = "simpan"
    else:
        findings, changed = apply_review(findings, request.form)
        action = request.form.get("aksi", "simpan")
        flash("Perubahan review berhasil disimpan." if changed else "Tidak ada perubahan status.", "success")

    analysis["findings"] = sort_findings(findings)
    _regenerate_final(analysis)
    analysis["reviewed_at"] = utc_now_iso()
    job["analysis"] = analysis
    job["identity"] = identity
    job["reviewed"] = True
    job["reviewed_at"] = analysis["reviewed_at"]
    job["review"] = {
        "changed": changed,
        "reviewed_at": analysis["reviewed_at"],
        "bulk": bulk,
    }
    store.set(job_id, job)

    if action == "generate":
        return redirect(url_for("result", job_id=job_id))
    return redirect(url_for("review", job_id=job_id))


@app.get("/result/<job_id>")
def result(job_id: str):
    job = _get_job_or_404(job_id)
    analysis = job.get("analysis") or {}
    findings = sort_findings(analysis.get("findings") or [])
    analysis["findings"] = findings
    return render_template(
        "result.html",
        job=job,
        doc=job.get("doc") or {},
        analysis=analysis,
        findings=findings,
        summary=analysis.get("summary_final") or summarize(findings),
        identity=job.get("identity") or {},
        score_initial=analysis.get("score") or {},
        score_final=analysis.get("score_final") or analysis.get("score") or {},
        alignment=analysis.get("alignment") or [],
        warnings=analysis.get("warnings") or [],
    )


@app.get("/download/<job_id>/<fmt>")
def download(job_id: str, fmt: str):
    job = _get_job_or_404(job_id)
    fmt = fmt.lower()
    if fmt not in {"docx", "xlsx"}:
        abort(404)
    analysis = job.get("analysis") or {}
    if not analysis.get("score_final"):
        _regenerate_final(analysis)
        job["analysis"] = analysis
        store.set(job_id, job)
    identity = job.get("identity") or {}
    opd = identity.get("opd") or (job.get("doc") or {}).get("opd", {}).get("nama") or "OPD"
    tahun = identity.get("tahun") or (job.get("doc") or {}).get("tahun") or ""
    base = safe_filename(f"LKA Renja {opd} {tahun}")
    try:
        if fmt == "docx":
            buffer = build_docx(job)
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            buffer = build_xlsx(job)
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except Exception:
        LOGGER.exception("Gagal membuat lembar kerja %s untuk job %s", fmt, job_id)
        abort(500)
    LOGGER.info("Unduh %s job %s", fmt, job_id)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{base}.{fmt}",
        mimetype=mimetype,
        max_age=0,
    )


@app.post("/job/<job_id>/delete")
def delete_job(job_id: str):
    job = store.delete(job_id)
    if not job:
        abort(404)
    flash("Data pekerjaan dihapus dari server.", "success")
    return redirect(url_for("index"))


@app.get("/template/master.csv")
def template_master():
    path = BASE_DIR / "data" / "master_template.csv"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name="template-master-nomenklatur.csv", max_age=0)


@app.get("/contoh/<name>")
def sample_file(name: str):
    allowed = {
        "renja.csv": ("sample/renja_bappeda_contoh.csv", "contoh-renja-bappeda.csv", "text/csv"),
        "renja.xlsx": (
            "sample/renja_bappeda_contoh.xlsx",
            "contoh-renja-bappeda.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        "rkpd.txt": ("sample/rkpd_2027_cuplikan.txt", "contoh-rkpd-2027.txt", "text/plain"),
        "renstra.txt": ("sample/renstra_bappeda_cuplikan.txt", "contoh-renstra-bappeda.txt", "text/plain"),
    }
    if name not in allowed:
        abort(404)
    relative, download_name, mimetype = allowed[name]
    path = BASE_DIR / relative
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=download_name, mimetype=mimetype, max_age=0)


@app.get("/healthz")
def healthz():
    try:
        master = MasterNomenklatur.load_default(DEFAULT_MASTER_PATH)
        master_info = {"counts": master.counts(), "sample": master.is_sample}
    except Exception as exc:
        master_info = {"error": str(exc)}
    return {
        "status": "ok",
        "time": utc_now_iso(),
        "jobs": store.stats(),
        "llm": llm_analyzer.status(),
        "master": master_info,
    }


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return (
        render_template(
            "error.html",
            code=413,
            title="Ukuran unggahan terlalu besar",
            message=f"Total ukuran file melebihi batas {MAX_TOTAL_UPLOAD // (1024 * 1024)} MB. Kompres atau unggah dokumen secara terpisah.",
        ),
        413,
    )


@app.errorhandler(404)
def not_found(_error):
    return (
        render_template(
            "error.html",
            code=404,
            title="Halaman atau pekerjaan tidak ditemukan",
            message="Tautan tidak valid atau data pekerjaan sudah kedaluwarsa. Silakan mulai analisis baru.",
        ),
        404,
    )


@app.errorhandler(500)
def server_error(_error):
    return (
        render_template(
            "error.html",
            code=500,
            title="Terjadi kesalahan pada server",
            message="Sistem tidak dapat menyelesaikan permintaan. Coba lagi atau hubungi administrator Bappeda.",
        ),
        500,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
