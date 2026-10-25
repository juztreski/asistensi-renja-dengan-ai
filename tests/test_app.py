from __future__ import annotations

import io
import zipfile


def _upload_data(sample_dir):
    renja = (sample_dir / "renja_bappeda_contoh.xlsx").read_bytes()
    rkpd = (sample_dir / "rkpd_2027_cuplikan.txt").read_bytes()
    renstra = (sample_dir / "renstra_bappeda_cuplikan.txt").read_bytes()
    return {
        "renja": (io.BytesIO(renja), "renja_bappeda_contoh.xlsx"),
        "rkpd": (io.BytesIO(rkpd), "rkpd_2027_cuplikan.txt"),
        "renstra": (io.BytesIO(renstra), "renstra_bappeda_cuplikan.txt"),
        "opd": "Bappeda Kabupaten Soppeng",
        "tahun": "2027",
        "tanggal": "2026-10-24",
        "nomor": "LKA/001/X/2026",
        "perencana": "Perencana Ahli Pertama",
        "nip_perencana": "19800101 200501 1 001",
        "koordinator": "Koordinator Bidang",
        "nip_koordinator": "19750101 200001 1 001",
        "kepala": "Kepala Bappeda",
        "nip_kepala": "19700101 199501 1 001",
    }


def test_home_and_supporting_routes(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Asistensi Rencana Kinerja Perangkat Daerah" in body
    assert "Master masih contoh" in body
    assert 'id="analyze-form"' in body

    sample = client.get("/contoh/renja.xlsx")
    assert sample.status_code == 200
    assert sample.data[:2] == b"PK"
    assert "attachment" in sample.headers["Content-Disposition"]

    template = client.get("/template/master.csv")
    assert template.status_code == 200
    assert b"kode" in template.data.lower()

    health = client.get("/healthz")
    assert health.status_code == 200
    payload = health.get_json()
    assert payload["status"] == "ok"
    assert payload["master"]["counts"]["program"] == 3


def test_analyze_review_result_download_and_delete(client, sample_dir):
    response = client.post("/analyze", data=_upload_data(sample_dir), content_type="multipart/form-data")
    assert response.status_code == 302
    review_path = response.headers["Location"]
    assert review_path.startswith("/review/")
    job_id = review_path.rsplit("/", 1)[-1]

    review = client.get(review_path)
    assert review.status_code == 200
    review_body = review.get_data(as_text=True)
    assert "Review temuan asistensi" in review_body
    assert "Bappeda Kabupaten Soppeng" in review_body
    assert "Penyediaan Alat Tulis Kantor" in review_body
    assert "Skor setelah review" in review_body

    bulk = client.post(
        review_path,
        data={"bulk": "Dikoreksi", "aksi": "simpan"},
        follow_redirects=True,
    )
    assert bulk.status_code == 200
    bulk_body = bulk.get_data(as_text=True)
    assert "Status 17 temuan diubah menjadi Dikoreksi" in bulk_body
    assert "100<small>/100</small>" in bulk_body

    generate = client.post(review_path, data={"aksi": "generate"})
    assert generate.status_code == 302
    assert generate.headers["Location"] == f"/result/{job_id}"

    result = client.get(f"/result/{job_id}")
    assert result.status_code == 200
    result_body = result.get_data(as_text=True)
    assert "Lembar Kerja Asistensi" in result_body
    assert "Unduh DOCX" in result_body

    docx = client.get(f"/download/{job_id}/docx")
    assert docx.status_code == 200
    assert "attachment" in docx.headers["Content-Disposition"]
    assert docx.data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(docx.data)) as archive:
        assert "word/document.xml" in archive.namelist()

    xlsx = client.get(f"/download/{job_id}/xlsx")
    assert xlsx.status_code == 200
    assert "attachment" in xlsx.headers["Content-Disposition"]
    assert xlsx.data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(xlsx.data)) as archive:
        assert "xl/workbook.xml" in archive.namelist()

    deleted = client.post(f"/job/{job_id}/delete", follow_redirects=True)
    assert deleted.status_code == 200
    assert "Data pekerjaan dihapus" in deleted.get_data(as_text=True)
    assert client.get(review_path).status_code == 404


def test_invalid_upload_shows_error(client):
    response = client.post(
        "/analyze",
        data={"renja": (io.BytesIO(b"bukan dokumen"), "renja.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Format file renja tidak didukung" in response.get_data(as_text=True)


def test_missing_job_returns_404_page(client):
    response = client.get("/review/tidak-ada")
    assert response.status_code == 404
    assert "tidak ditemukan" in response.get_data(as_text=True).lower()
