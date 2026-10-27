from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from app.core.config import Settings
from app.db.database import connect
from app.services.extraction import ExtractionError, extract_document


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _document_row(row: sqlite3.Row, job_id: str | None = None, duplicate: bool = False) -> dict:
    return {
        "id": row["id"], "filename": row["original_filename"], "extension": row["extension"],
        "content_type": row["content_type"], "size_bytes": row["size_bytes"], "sha256": row["sha256"],
        "status": row["status"], "error_message": row["error_message"], "created_at": row["created_at"],
        "processed_at": row["processed_at"], "page_count": row["page_count"], "chunk_count": row["chunk_count"],
        "entity_count": row["entity_count"], "fact_count": row["fact_count"],
        "job_id": job_id, "duplicate_upload": duplicate,
    }


def get_document(settings: Settings, document_id: str) -> dict | None:
    with connect(settings) as connection:
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not row:
            return None
        job = connection.execute("SELECT id FROM jobs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1", (document_id,)).fetchone()
        return _document_row(row, job["id"] if job else None)


def list_documents(settings: Settings, limit: int, offset: int) -> tuple[list[dict], int]:
    with connect(settings) as connection:
        total = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()["count"]
        rows = connection.execute("SELECT * FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        result = []
        for row in rows:
            job = connection.execute("SELECT id FROM jobs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1", (row["id"],)).fetchone()
            result.append(_document_row(row, job["id"] if job else None))
        return result, total


def get_job(settings: Settings, job_id: str) -> dict | None:
    with connect(settings) as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(settings: Settings, limit: int) -> list[dict]:
    with connect(settings) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]


def save_uploaded_document(settings: Settings, temporary_path: Path, filename: str, extension: str, content_type: str, size_bytes: int, sha256: str) -> tuple[dict, bool]:
    with connect(settings) as connection:
        existing = connection.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
        if existing:
            job = connection.execute("SELECT id FROM jobs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1", (existing["id"],)).fetchone()
            temporary_path.unlink(missing_ok=True)
            return _document_row(existing, job["id"] if job else None, True), True

        document_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
        safe_path = settings.upload_dir / f"{document_id}{extension}"
        temporary_path.replace(safe_path)
        timestamp = now_iso()
        try:
            connection.execute(
                "INSERT INTO documents(id,original_filename,extension,content_type,size_bytes,sha256,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (document_id, filename, extension, content_type, size_bytes, sha256, "queued", timestamp),
            )
            connection.execute(
                "INSERT INTO jobs(id,document_id,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (job_id, document_id, "queued", "queued", timestamp, timestamp),
            )
        except Exception:
            safe_path.unlink(missing_ok=True)
            raise
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return _document_row(row, job_id), False


def process_document(settings: Settings, document_id: str) -> None:
    started = now_iso()
    with connect(settings) as connection:
        job = connection.execute("SELECT * FROM jobs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1", (document_id,)).fetchone()
        document = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not job or not document:
            return
        connection.execute("UPDATE jobs SET status='processing',stage='extracting_text',started_at=?,updated_at=?,error_message=NULL WHERE id=?", (started, started, job["id"]))
        connection.execute("UPDATE documents SET status='processing',error_message=NULL WHERE id=?", (document_id,))

    try:
        source_path = settings.upload_dir / f"{document_id}{document['extension']}"
        chunks, page_count = extract_document(source_path, document["extension"], settings)
        result_path = settings.extracted_dir / f"{document_id}.json"
        temporary_result = result_path.with_suffix(".json.tmp")
        temporary_result.write_text(json.dumps([chunk.to_dict() for chunk in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_result.replace(result_path)
        with connect(settings) as connection:
            connection.execute("UPDATE jobs SET stage='extracting_entities',updated_at=? WHERE id=?", (now_iso(), job["id"]))
        from app.nlp.service import extract_and_persist
        entity_count, fact_count = extract_and_persist(settings, document_id, [chunk.to_dict() for chunk in chunks])
        from app.services.graph_store import sync_document
        try:
            sync_document(settings, document_id)
        except Exception as exc:
            raise ExtractionError(f"Graph persistence failed: {exc}") from exc
        completed = now_iso()
        with connect(settings) as connection:
            connection.execute("UPDATE documents SET status='completed',error_message=NULL,processed_at=?,page_count=?,chunk_count=?,entity_count=?,fact_count=? WHERE id=?", (completed, page_count, len(chunks), entity_count, fact_count, document_id))
            connection.execute("UPDATE jobs SET status='completed',stage='extraction_complete',updated_at=?,completed_at=?,error_message=NULL WHERE id=?", (completed, completed, job["id"]))
    except (ExtractionError, OSError) as exc:
        _mark_failed(settings, document_id, job["id"], str(exc))
    except Exception:
        _mark_failed(settings, document_id, job["id"], "An unexpected processing error occurred. Check the backend log for details.")
        raise


def _mark_failed(settings: Settings, document_id: str, job_id: str, message: str) -> None:
    timestamp = now_iso()
    with connect(settings) as connection:
        connection.execute("UPDATE documents SET status='failed',error_message=? WHERE id=?", (message, document_id))
        connection.execute("UPDATE jobs SET status='failed',stage='failed',updated_at=?,completed_at=?,error_message=? WHERE id=?", (timestamp, timestamp, message, job_id))


def retry_job(settings: Settings, job_id: str) -> dict | None:
    timestamp = now_iso()
    with connect(settings) as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        if row["status"] != "failed":
            raise ValueError("Only failed jobs can be retried.")
        connection.execute("UPDATE jobs SET status='queued',stage='queued',retry_count=retry_count+1,error_message=NULL,updated_at=?,started_at=NULL,completed_at=NULL WHERE id=?", (timestamp, job_id))
        connection.execute("UPDATE documents SET status='queued',error_message=NULL,processed_at=NULL,chunk_count=0,page_count=0,entity_count=0,fact_count=0 WHERE id=?", (row["document_id"],))
        return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def queue_extraction(settings: Settings, document_id: str) -> dict | None:
    timestamp = now_iso()
    with connect(settings) as connection:
        document = connection.execute("SELECT status FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            return None
        active = connection.execute(
            "SELECT id FROM jobs WHERE document_id=? AND status IN ('queued','processing') LIMIT 1", (document_id,),
        ).fetchone()
        if active:
            raise ValueError("This document already has an active extraction job.")
        job_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO jobs(id,document_id,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (job_id, document_id, "queued", "queued", timestamp, timestamp),
        )
        connection.execute("UPDATE documents SET status='queued',error_message=NULL,processed_at=NULL WHERE id=?", (document_id,))
        return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def read_document_detail(settings: Settings, document_id: str) -> dict | None:
    summary = get_document(settings, document_id)
    if not summary:
        return None
    result_path = settings.extracted_dir / f"{document_id}.json"
    if result_path.exists():
        try:
            summary["chunks"] = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary["chunks"] = []
    else:
        summary["chunks"] = []
    return summary


def delete_document(settings: Settings, document_id: str) -> bool:
    from app.services.graph_store import delete_document_graph
    try:
        delete_document_graph(settings, document_id)
    except Exception as exc:
        raise RuntimeError(f"Could not remove this document from Neo4j: {exc}") from exc
    with connect(settings) as connection:
        row = connection.execute("SELECT extension FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            return False
        connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
        connection.execute("DELETE FROM facts WHERE id NOT IN (SELECT DISTINCT fact_id FROM fact_evidence)")
        connection.execute(
            """DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM entity_mentions)
                AND id NOT IN (SELECT subject_entity_id FROM facts)
                AND id NOT IN (SELECT object_entity_id FROM facts)"""
        )
    (settings.upload_dir / f"{document_id}{row['extension']}").unlink(missing_ok=True)
    (settings.extracted_dir / f"{document_id}.json").unlink(missing_ok=True)
    return True
