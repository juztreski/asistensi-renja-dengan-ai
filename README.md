# Sistem Asistensi Renja OPD — Bappeda Kabupaten Soppeng

Aplikasi web untuk membantu perencana Bappeda memeriksa Rencana Kinerja Perangkat Daerah (Renja OPD): ekstraksi Program/Kegiatan/Subkegiatan, pemeriksaan nomenklatur dan indikator, penyelarasan dengan RKPD/Renstra/RPJMD, review temuan, serta pembuatan Lembar Kerja Asistensi dalam format DOCX dan XLSX.

## Fitur

- Unggah Renja OPD berformat PDF, DOCX, XLSX, atau CSV.
- Unggah dokumen acuan opsional: RPJMD, Renstra OPD, dan RKPD (PDF/DOCX/XLSX/CSV/TXT/MD).
- Ekstraksi tabel hierarkis dan deteksi identitas OPD/tahun.
- Pemeriksaan rule-based:
  - kesesuaian kode dan nama dengan master nomenklatur;
  - konsistensi hierarki program, kegiatan, subkegiatan;
  - kelengkapan nama, indikator, target, satuan, dan pagu;
  - indikator terlalu umum/tidak terukur, target tidak jelas, target nol;
  - duplikasi kode/nama;
  - penyelarasan tekstual dengan RKPD, Renstra, dan RPJMD.
- Analisis AI opsional (Google Gemini) untuk melengkapi temuan rule-based.
- Review per temuan: `Terbuka`, `Diterima`, `Dikoreksi`, `Diabaikan`, disertai catatan koreksi.
- Skor 0–100 dihitung ulang setelah review.
- Lembar kerja DOCX/XLSX memuat identitas, ringkasan, matriks, temuan, penyelarasan, dan kolom paraf/tanda tangan.
- Data pekerjaan disimpan sementara dengan masa berlaku dan dapat dihapus manual.

## Peringatan penting: master nomenklatur

Repositori ini **tidak memuat longlist resmi**. File `data/nomenklatur_kab_kota.json` adalah data **CONTOH/SEED** (3 program, 9 kegiatan, 36 subkegiatan) untuk demonstrasi. Hasil analisis dengan master ini tidak valid untuk dokumen resmi.

Ganti master melalui formulir unggah atau letakkan file resmi di `data/nomenklatur_kab_kota.json`. Master dapat berupa:

1. JSON bertingkat (`program` → `kegiatan` → `subkegiatan`).
2. JSON baris datar dengan kolom kode/nama per level.
3. CSV dengan header seperti pada `data/master_template.csv` atau unduh melalui `/template/master.csv`.

Gunakan longlist Permendagri 90/2019 beserta pemutakhirannya dan dokumen perencanaan daerah yang berlaku.

## Menjalankan aplikasi

Prasyarat: Python 3.11+ dan pip.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Buka `http://127.0.0.1:5000`.

Untuk pengembangan dan pengujian:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Contoh dokumen tersedia di halaman beranda atau langsung:

- `/contoh/renja.xlsx`
- `/contoh/renja.csv`
- `/contoh/rkpd.txt`
- `/contoh/renstra.txt`

Contoh Renja sengaja memuat kesalahan nomenklatur, indikator, dan pagu untuk menguji sistem. Buat ulang contoh XLSX dengan:

```bash
python scripts/build_sample_xlsx.py
```

## Konfigurasi

| Variabel | Default | Keterangan |
| --- | --- | --- |
| `FLASK_SECRET_KEY` | dibuat otomatis di `.flask_secret` | Kunci sesi Flask; wajib diset di produksi. |
| `HOST` | `127.0.0.1` | Alamat bind server pengembangan. |
| `PORT` | `5000` | Port server pengembangan. |
| `UPLOAD_DIR` | `./uploads` | Direktori kerja sementara. |
| `MASTER_PATH` | `./data/nomenklatur_kab_kota.json` | Master bawaan. |
| `MAX_UPLOAD_MB` | `60` | Batas total unggahan per permintaan. |
| `MAX_FILE_MB` | `25` | Batas satu file. |
| `MAX_MASTER_MB` | `15` | Batas file master. |
| `JOB_TTL_SECONDS` | `21600` | Masa berlaku pekerjaan (6 jam). |
| `LOG_LEVEL` | `INFO` | Level log aplikasi. |
| `GEMINI_API_KEY` | kosong | Mengaktifkan analisis AI Google Gemini. |
| `GOOGLE_API_KEY` | kosong | Alternatif nama env untuk kunci API Gemini. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model Gemini yang digunakan. |
| `LLM_ENABLED` | kosong | Set `0` untuk memaksa AI nonaktif. |
| `LLM_TIMEOUT_SECONDS` | `90` | Timeout panggilan AI. |
| `LLM_MAX_INPUT_CHARS` | `60000` | Batas teks yang dikirim ke AI. |
| `LLM_MAX_PAYLOAD_CHARS` | `120000` | Batas payload analisis untuk AI. |
| `LLM_MAX_TOKENS` | `6000` | Token keluaran analisis. |
| `LLM_EXTRACT_MAX_TOKENS` | `8000` | Token keluaran ekstraksi AI. |

AI bersifat opsional. Tanpa `GEMINI_API_KEY`, seluruh pemeriksaan rule-based tetap berjalan. Jika AI diaktifkan, cuplikan dokumen dikirim ke Google Gemini API; sesuaikan dengan kebijakan keamanan data instansi.

```bash
export GEMINI_API_KEY="kunci-dari-Google-AI-Studio"
export GEMINI_MODEL="gemini-2.5-flash"   # opsional
python app.py
```

## Format dokumen Renja

Format paling akurat adalah XLSX/DOCX dengan tabel yang memiliki kolom:

`kode`, `nama`/`uraian`, `indikator`, `target`, `satuan`, `pagu`, `lokasi`, `keterangan`.

Kolom `level` bersifat opsional karena level dapat ditentukan dari pola kode. Untuk PDF hasil pindai, ekstraksi teks memerlukan OCR. Pemeriksaan penyelarasan berbasis kemiripan teks, bukan pembacaan matriks target secara penuh, sehingga verifikasi manual tetap diperlukan.

## Alur dan penilaian

1. Unggah Renja dan dokumen acuan.
2. Sistem mengekstrak dan memeriksa dokumen.
3. Perencana meninjau setiap temuan.
4. Sistem menghitung skor akhir dan membuat lembar kerja.

Skor awal 100. Penalti per temuan: Kritis 10, Sedang 4, Saran 1, dengan batas penalti per kategori:

| Kategori | Batas |
| --- | ---: |
| Identitas | 5 |
| Nomenklatur | 35 |
| Hierarki | 10 |
| Kelengkapan | 20 |
| Indikator | 15 |
| Duplikasi | 5 |
| Penyelarasan | 10 |

Hanya temuan berstatus `Terbuka` dan `Diterima` yang dihitung dalam skor. Status `Dikoreksi` dan `Diabaikan` tidak dihitung.

## Struktur proyek

```text
app.py                  Rute Flask dan orkestrasi alur
extract_renja.py        Ekstraksi PDF/DOCX/XLSX/CSV dan parser hierarki
master_nomenklatur.py   Loader, indeks, fuzzy match, validasi hierarki
align_check.py          Mesin pemeriksaan dan skor
review_fields.py        Status, normalisasi temuan, scoring
llm_analyzer.py         Integrasi Google Gemini opsional
generate_worksheet.py   Generator DOCX/XLSX
job_store.py            Penyimpanan pekerjaan sementara
prompts/                Prompt ekstraksi dan analisis AI
templates/              Halaman Jinja
static/                 CSS dan JavaScript
data/                   Master seed dan template CSV
sample/                 Contoh dokumen uji
scripts/                Utilitas pembuat contoh XLSX
tests/                  Pengujian pytest
```

## Deployment

Karena pekerjaan disimpan di memori proses, jalankan **satu worker**:

```bash
python -m gunicorn --workers 1 --threads 4 --timeout 180 --bind 127.0.0.1:8000 app:app
```

Contoh konfigurasi Nginx:

```nginx
server {
    listen 443 ssl;
    server_name asistensi.example.go.id;

    client_max_body_size 64M;
    proxy_read_timeout 180s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Catatan produksi:

- Set `FLASK_SECRET_KEY` yang kuat dan gunakan HTTPS.
- Aplikasi belum memiliki autentikasi bawaan. Tempatkan di belakang SSO/reverse proxy atau tambahkan autentikasi sebelum akses publik.
- Untuk beberapa worker/beberapa server, ganti `JobStore` in-memory dengan penyimpanan bersama (Redis/database) dan simpan berkas di object storage.
- Pastikan direktori `uploads/` memiliki izin yang tepat dan pembersihan berkas berjalan.
- Ganti master contoh dengan longlist resmi sebelum digunakan untuk dokumen dinas.

## Pengujian

```bash
python -m pytest -q
```

Pengujian mencakup master, ekstraksi XLSX, deteksi kesalahan contoh, perhitungan ulang skor, pembuatan DOCX/XLSX, alur unggah–review–unduh, unggahan tidak valid, dan halaman 404.

## Dasar regulasi

- Permendagri Nomor 86 Tahun 2017 tentang Tata Cara Perencanaan, Pengendalian dan Evaluasi Pembangunan Daerah.
- Permendagri Nomor 90 Tahun 2019 tentang Klasifikasi, Kodefikasi, dan Nomenklatur Perencanaan Pembangunan dan Keuangan Daerah, beserta pemutakhirannya.
- RPJMD, Renstra OPD, dan RKPD Kabupaten Soppeng yang berlaku.

Verifikasi akhir atas kebenaran nomenklatur, target, pagu, dan penyelarasan tetap menjadi tanggung jawab perencana dan OPD.
