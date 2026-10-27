from io import BytesIO
import zipfile

from conftest import upload_txt


def test_upload_extract_and_read_text_with_location(client):
    response = upload_txt(client, contents="PaperGraph AI connects ideas to evidence.\n\nIt preserves source context.")

    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "queued"  # the 202 response is serialized before its background task runs
    assert created["job_id"]

    detail = client.get(f"/api/documents/{created['id']}").json()
    assert detail["status"] == "completed"
    assert detail["chunk_count"] == 2
    assert detail["chunks"][0]["text"] == "PaperGraph AI connects ideas to evidence."
    assert detail["chunks"][0]["paragraph_number"] == 1
    assert detail["chunks"][1]["paragraph_number"] == 2
    assert client.get(f"/api/jobs/{created['job_id']}").json()["stage"] == "extraction_complete"


def test_duplicate_upload_reuses_document(client):
    first = upload_txt(client, "first.txt", "Same exact source text.").json()
    second = upload_txt(client, "renamed.txt", "Same exact source text.").json()

    assert second["id"] == first["id"]
    assert second["duplicate_upload"] is True
    assert client.get("/api/documents").json()["total"] == 1


def test_rejects_extension_mime_and_content_mismatch(client):
    unsupported = client.post("/api/documents", files={"file": ("notes.csv", b"a,b\n1,2", "text/csv")})
    assert unsupported.status_code == 415

    wrong_mime = client.post("/api/documents", files={"file": ("notes.txt", b"valid text", "application/pdf")})
    assert wrong_mime.status_code == 415

    invalid_pdf = client.post("/api/documents", files={"file": ("paper.pdf", b"not a PDF", "application/pdf")})
    assert invalid_pdf.status_code == 415


def test_failed_job_can_be_retried_and_deleted(client):
    queued = upload_txt(client, contents="   \n\n  ").json()
    job_id = queued["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"

    retry = client.post(f"/api/jobs/{job_id}/retry")
    assert retry.status_code == 202
    assert retry.json()["retry_count"] == 1
    assert retry.json()["status"] == "queued"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"

    deleted = client.delete(f"/api/documents/{queued['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_upload_size_limit_is_enforced(client):
    too_large = b"x" * (2 * 1024 * 1024 + 1)
    response = client.post("/api/documents", files={"file": ("large.txt", too_large, "text/plain")})
    assert response.status_code == 413


def test_docx_extracts_paragraphs_and_table_rows(client):
    from docx import Document

    buffer = BytesIO()
    document = Document()
    document.add_heading("Methods", level=1)
    document.add_paragraph("The system preserves evidence.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Dataset"
    table.cell(0, 1).text = "PaperGraph sample"
    document.save(buffer)

    response = client.post("/api/documents", files={"file": ("note.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert response.status_code == 202
    detail = client.get(f"/api/documents/{response.json()['id']}").json()
    assert [chunk["text"] for chunk in detail["chunks"]] == ["Methods", "The system preserves evidence.", "Dataset | PaperGraph sample"]
    assert detail["chunks"][1]["section"] == "Methods"


def test_pdf_and_pptx_keep_page_and_slide_locations(client):
    import pymupdf
    from pptx import Presentation

    pdf_buffer = BytesIO()
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Evidence appears on page one.")
    pdf.save(pdf_buffer)
    pdf.close()
    pdf_response = client.post("/api/documents", files={"file": ("paper.pdf", pdf_buffer.getvalue(), "application/pdf")})
    assert pdf_response.status_code == 202
    pdf_detail = client.get(f"/api/documents/{pdf_response.json()['id']}").json()
    assert pdf_detail["page_count"] == 1
    assert pdf_detail["chunks"][0]["page_number"] == 1

    pptx_buffer = BytesIO()
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Key method"
    slide.placeholders[1].text = "Evidence in source."
    presentation.save(pptx_buffer)
    pptx_response = client.post("/api/documents", files={
        "file": ("slides.pptx", pptx_buffer.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    })
    assert pptx_response.status_code == 202
    pptx_detail = client.get(f"/api/documents/{pptx_response.json()['id']}").json()
    assert pptx_detail["page_count"] == 1
    assert pptx_detail["chunks"][0]["slide_number"] == 1
    assert "Key method" in pptx_detail["chunks"][0]["text"]


def test_seed_annotation_text_and_offsets_match_the_synthetic_source():
    import json
    import re
    from pathlib import Path

    from app.services.extraction import _clean_text

    project_root = Path(__file__).resolve().parents[2]
    source = (project_root / "data" / "samples" / "demo_research_note.txt").read_text(encoding="utf-8")
    paragraphs = [_clean_text(part) for part in re.split(r"\n\s*\n", source)]
    extracted_text = "\n".join(part for part in paragraphs if part)
    annotation_path = project_root / "data" / "annotations" / "seed.jsonl"
    annotation = json.loads(annotation_path.read_text(encoding="utf-8").splitlines()[0])

    assert annotation["text"] == extracted_text
    for entity in annotation["entities"]:
        assert extracted_text[entity["start_offset"]:entity["end_offset"]] == entity["text"]
    for relation in annotation["relations"]:
        assert extracted_text[relation["start_offset"]:relation["end_offset"]] == relation["evidence"]
