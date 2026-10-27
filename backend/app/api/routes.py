import hashlib
from pathlib import Path
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from app.schemas.documents import (
    DocumentDetail, DocumentListResponse, DocumentSummary, EntityDetail, EntityListResponse,
    ExtractionStats, Fact, FactListResponse, FactReviewUpdate, JobSummary,
    EntityMergeRequest, QuestionRequest, SafeGraphQuery, SearchRequest, TextDocumentCreate,
)
from app.nlp.repository import (
    active_merge_log, correct_fact, delete_fact, get_entity, get_fact, get_stats, list_entities,
    list_facts, merge_entities, undo_entity_merge,
)
from app.services.graph_store import delete_fact_graph, sync_all_documents, update_fact_status
from app.services.knowledge import analytics, answer_question, entity_resolution_candidates, export_graph, get_graph, graph_neighborhood, search, shortest_path
from app.services.evaluation import evaluate_annotations
from app.nlp.schema import load_schema
from app.services.ingestion import (
    delete_document, get_job, list_documents, list_jobs, process_document,
    queue_extraction, read_document_detail, retry_job, save_uploaded_document,
)


router = APIRouter(prefix="/api")
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown"}
ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream"},
}


def _content_is_valid(path: Path, extension: str) -> bool:
    if extension == ".pdf":
        with path.open("rb") as stream:
            return stream.read(1024).lstrip().startswith(b"%PDF-")
    if extension in {".docx", ".pptx"}:
        import zipfile
        if not zipfile.is_zipfile(path):
            return False
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                required = {"[Content_Types].xml", "word/document.xml"} if extension == ".docx" else {"[Content_Types].xml", "ppt/presentation.xml"}
                return required.issubset(names)
        except (OSError, zipfile.BadZipFile):
            return False
    try:
        content = path.read_bytes()
        content.decode("utf-8-sig")
        return b"\x00" not in content
    except (OSError, UnicodeDecodeError):
        return False


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "papergraph-api"}


@router.get("/stats", response_model=ExtractionStats)
def stats(request: Request):
    return get_stats(request.app.state.settings)


@router.get("/schema")
def extraction_schema():
    return load_schema().model_dump()


@router.post("/documents", response_model=DocumentSummary, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(request: Request, background_tasks: BackgroundTasks, file: UploadFile):
    settings = request.app.state.settings
    original_name = Path(file.filename or "upload").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Supported formats: PDF, DOCX, PPTX, TXT, and Markdown.")
    supplied_mime = (file.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if supplied_mime not in ALLOWED_MIME_TYPES[extension]:
        raise HTTPException(status_code=415, detail="The declared MIME type is not valid for this file extension.")
    temporary_path = settings.upload_dir / f"incoming-{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary_path.open("wb") as stream:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MiB upload limit.")
                digest.update(block)
                stream.write(block)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if total == 0:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if not _content_is_valid(temporary_path, extension):
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="The file content does not match its extension or is not readable.")

    try:
        result, duplicate = save_uploaded_document(
            settings, temporary_path, original_name, extension,
            supplied_mime, total, digest.hexdigest(),
        )
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The document could not be saved.") from exc
    if not duplicate:
        background_tasks.add_task(process_document, settings, result["id"])
    return result


@router.post("/documents/text", response_model=DocumentSummary, status_code=status.HTTP_202_ACCEPTED)
async def create_text_document(request: Request, background_tasks: BackgroundTasks, payload: TextDocumentCreate):
    settings = request.app.state.settings
    name = Path(payload.filename).name.strip() or "pasted-text.txt"
    extension = Path(name).suffix.lower()
    if extension not in {".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=422, detail="Pasted text must use a .txt, .md, or .markdown filename.")
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="Pasted text cannot be blank.")
    content = payload.text.encode("utf-8")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"Text exceeds the {settings.max_upload_bytes // (1024 * 1024)} MiB upload limit.")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = settings.upload_dir / f"incoming-{uuid.uuid4().hex}.tmp"
    temporary_path.write_bytes(content)
    result, duplicate = save_uploaded_document(settings, temporary_path, name, extension, "text/plain", len(content), hashlib.sha256(content).hexdigest())
    if not duplicate:
        background_tasks.add_task(process_document, settings, result["id"])
    return result


@router.get("/documents", response_model=DocumentListResponse)
def documents(request: Request, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    items, total = list_documents(request.app.state.settings, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def document_detail(request: Request, document_id: str):
    result = read_document_detail(request.app.state.settings, document_id)
    if not result:
        raise HTTPException(status_code=404, detail="Document not found.")
    return result


@router.post("/documents/{document_id}/extract", response_model=JobSummary, status_code=status.HTTP_202_ACCEPTED)
def extract_document(request: Request, background_tasks: BackgroundTasks, document_id: str):
    try:
        result = queue_extraction(request.app.state.settings, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Document not found.")
    background_tasks.add_task(process_document, request.app.state.settings, document_id)
    return result


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document(request: Request, document_id: str):
    if not delete_document(request.app.state.settings, document_id):
        raise HTTPException(status_code=404, detail="Document not found.")


@router.get("/jobs", response_model=list[JobSummary])
def jobs(request: Request, limit: int = Query(25, ge=1, le=100)):
    return list_jobs(request.app.state.settings, limit)


@router.get("/jobs/{job_id}", response_model=JobSummary)
def job_detail(request: Request, job_id: str):
    result = get_job(request.app.state.settings, job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found.")
    return result


@router.post("/jobs/{job_id}/retry", response_model=JobSummary, status_code=status.HTTP_202_ACCEPTED)
def retry_processing(request: Request, background_tasks: BackgroundTasks, job_id: str):
    try:
        result = retry_job(request.app.state.settings, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Job not found.")
    background_tasks.add_task(process_document, request.app.state.settings, result["document_id"])
    return result


@router.get("/entities", response_model=EntityListResponse)
def entities(
    request: Request,
    document_id: str | None = None,
    entity_type: str | None = None,
    q: str | None = Query(None, max_length=120),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    items, total = list_entities(request.app.state.settings, document_id, entity_type, q, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/entities/{entity_id}", response_model=EntityDetail)
def entity_detail(request: Request, entity_id: str):
    result = get_entity(request.app.state.settings, entity_id)
    if not result:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return result


@router.get("/facts", response_model=FactListResponse)
def facts(
    request: Request,
    document_id: str | None = None,
    predicate: str | None = None,
    review_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    items, total = list_facts(request.app.state.settings, document_id, predicate, review_status, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/facts/{fact_id}", response_model=Fact)
def fact_detail(request: Request, fact_id: str):
    result = get_fact(request.app.state.settings, fact_id)
    if not result:
        raise HTTPException(status_code=404, detail="Fact not found.")
    return result


@router.patch("/facts/{fact_id}", response_model=Fact)
def review_fact(request: Request, fact_id: str, update: FactReviewUpdate):
    settings = request.app.state.settings
    try:
        result = correct_fact(settings, fact_id, update.model_dump(exclude_unset=True))
        if result:
            update_fact_status(settings, fact_id, result["review_status"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Fact not found.")
    return result


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_fact(request: Request, fact_id: str):
    settings = request.app.state.settings
    delete_fact_graph(settings, fact_id)
    if not delete_fact(settings, fact_id):
        raise HTTPException(status_code=404, detail="Fact not found.")


@router.get("/graphs/current")
def graph(
    request: Request,
    document_id: str | None = None,
    entity_type: str | None = None,
    q: str | None = Query(None, max_length=120),
    review_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(250, ge=1, le=1000),
):
    return get_graph(request.app.state.settings, document_id=document_id, entity_type=entity_type, q=q, review_status=review_status, limit=limit)


@router.post("/graphs/current/query")
def safe_graph_query(request: Request, query: SafeGraphQuery):
    # Accept structured filters only; caller supplied Cypher is never executed.
    return get_graph(request.app.state.settings, **query.model_dump())


@router.get("/graphs/current/shortest-path")
def graph_shortest_path(request: Request, source_entity_id: str, target_entity_id: str, max_depth: int = Query(5, ge=1, le=8)):
    path = shortest_path(request.app.state.settings, source_entity_id, target_entity_id, max_depth)
    if path is None:
        raise HTTPException(status_code=404, detail="No path was found within the requested depth.")
    return path


@router.get("/graphs/current/neighbors/{entity_id}")
def graph_neighbors(request: Request, entity_id: str, document_id: str | None = None, review_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"), depth: int = Query(1, ge=1, le=2)):
    result = graph_neighborhood(request.app.state.settings, entity_id, document_id, review_status, depth)
    if result is None:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return result


@router.get("/graphs/current/export")
def graph_export(request: Request, format: str = Query("json", pattern="^(json|csv)$"), document_id: str | None = None):
    body, media_type = export_graph(request.app.state.settings, format, document_id=document_id)
    return Response(content=body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="papergraph-export.{format}"'})


@router.post("/search")
def search_documents(request: Request, payload: SearchRequest):
    return search(request.app.state.settings, payload.query, payload.limit)


@router.post("/qa")
def ask_question(request: Request, payload: QuestionRequest):
    return answer_question(request.app.state.settings, payload.question, payload.limit)


@router.get("/analytics")
def get_analytics(request: Request):
    return analytics(request.app.state.settings)


@router.get("/evaluation")
def get_evaluation(request: Request):
    return evaluate_annotations(request.app.state.settings)


@router.get("/settings")
def get_application_settings(request: Request):
    configured = request.app.state.settings
    return {
        "data_dir": str(configured.data_dir), "max_upload_bytes": configured.max_upload_bytes,
        "chunk_size_chars": configured.chunk_size_chars, "chunk_overlap_chars": configured.chunk_overlap_chars,
        "cors_origins": list(configured.cors_origins), "graph_backend": configured.graph_backend,
        "neo4j_uri": configured.neo4j_uri if configured.graph_backend == "neo4j" else None,
        "neo4j_configured": bool(configured.neo4j_password), "llm_provider": configured.llm_provider,
        "llm_base_url": (configured.llm_base_url if configured.llm_provider == "openai_compatible"
                         else "Google Gemini API" if configured.llm_provider == "gemini" else None),
        "llm_model": configured.llm_model if configured.llm_provider != "none" else None,
        "llm_api_key_configured": bool(configured.llm_api_key), "llm_timeout_seconds": configured.llm_timeout_seconds,
        "llm_max_chunks_per_document": configured.llm_max_chunks_per_document,
        "llm_max_input_chars": configured.llm_max_input_chars, "llm_max_output_tokens": configured.llm_max_output_tokens,
        "embeddings_enabled": configured.enable_embeddings,
        "embedding_model": configured.embedding_model if configured.enable_embeddings else None,
        "minimum_extraction_confidence": configured.minimum_extraction_confidence,
        "entity_match_threshold": configured.entity_match_threshold,
        "entity_review_threshold": configured.entity_review_threshold,
        "graph_max_nodes": configured.graph_max_nodes,
        "annotations_path": str(configured.annotations_path) if configured.annotations_path else None,
        "notes": "Values load from the project .env file when the API starts. Secret values are never returned.",
    }


@router.get("/entity-resolution/candidates")
def entity_resolution_review(request: Request, limit: int = Query(100, ge=1, le=500)):
    return entity_resolution_candidates(request.app.state.settings, limit)


@router.get("/entity-merges")
def entity_merge_history(request: Request, limit: int = Query(50, ge=1, le=200)):
    return active_merge_log(request.app.state.settings, limit)


@router.post("/entity-merges", status_code=status.HTTP_201_CREATED)
def resolve_entities(request: Request, payload: EntityMergeRequest):
    settings = request.app.state.settings
    try:
        result = merge_entities(settings, payload.source_entity_id, payload.target_entity_id, payload.note)
        if not result:
            raise HTTPException(status_code=404, detail="One of the selected entities was not found.")
        sync_all_documents(settings)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Entity merge was saved locally, but Neo4j synchronization failed: {exc}") from exc


@router.post("/entity-merges/{merge_id}/undo")
def undo_entity_resolution(request: Request, merge_id: str):
    settings = request.app.state.settings
    try:
        if not undo_entity_merge(settings, merge_id):
            raise HTTPException(status_code=404, detail="Active entity merge not found.")
        sync_all_documents(settings)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Merge undo was saved locally, but Neo4j synchronization failed: {exc}") from exc
    return {"status": "undone", "merge_id": merge_id}


@router.post("/graphs/sync")
def synchronize_graph(request: Request):
    try:
        sync_all_documents(request.app.state.settings)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Graph synchronization failed: {exc}") from exc
    return {"status": "synchronized", "backend": request.app.state.settings.graph_backend}
