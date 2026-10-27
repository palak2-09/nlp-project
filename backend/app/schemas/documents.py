from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DocumentStatus = Literal["queued", "processing", "completed", "failed"]


class DocumentSummary(BaseModel):
    id: str
    filename: str
    extension: str
    content_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    error_message: str | None = None
    created_at: str
    processed_at: str | None = None
    page_count: int
    chunk_count: int
    entity_count: int = 0
    fact_count: int = 0
    job_id: str | None = None
    duplicate_upload: bool = False

    model_config = ConfigDict(from_attributes=True)


class TextChunk(BaseModel):
    text: str
    location_type: Literal["page", "slide", "paragraph"]
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    paragraph_number: int | None = None
    section: str | None = None
    start_offset: int
    end_offset: int


class DocumentDetail(DocumentSummary):
    chunks: list[TextChunk] = Field(default_factory=list)


class DocumentListResponse(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class JobSummary(BaseModel):
    id: str
    document_id: str
    status: DocumentStatus
    stage: str
    retry_count: int
    error_message: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None


class EntityMention(BaseModel):
    document_id: str
    filename: str
    chunk_index: int
    surface_form: str
    start_offset: int
    end_offset: int
    location_type: str
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    paragraph_number: int | None = None
    section: str | None = None
    extraction_method: str
    model_version: str
    confidence: float


class EntitySummary(BaseModel):
    id: str
    label: str
    normalized_name: str
    entity_type: str
    aliases: list[str]
    mention_count: int
    created_at: str


class EntityDetail(EntitySummary):
    mentions: list[EntityMention] = Field(default_factory=list)


class EntityListResponse(BaseModel):
    items: list[EntitySummary]
    total: int
    limit: int
    offset: int


class FactEvidence(BaseModel):
    document_id: str
    filename: str
    chunk_index: int
    evidence_text: str
    start_offset: int
    end_offset: int
    location_type: str
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    paragraph_number: int | None = None
    section: str | None = None
    extraction_method: str
    model_version: str
    confidence: float
    created_at: str


class FactCorrectionRecord(BaseModel):
    id: str
    before: dict
    after: dict
    note: str
    created_at: str


class FactEntity(BaseModel):
    id: str
    label: str
    entity_type: str


class Fact(BaseModel):
    id: str
    subject: FactEntity
    predicate: str
    polarity: Literal["affirmed", "negated"] = "affirmed"
    object: FactEntity
    extraction_method: str
    model_version: str
    confidence: float
    review_status: Literal["pending", "approved", "rejected"]
    created_at: str
    evidence: list[FactEvidence] = Field(default_factory=list)
    corrections: list[FactCorrectionRecord] = Field(default_factory=list)


class FactListResponse(BaseModel):
    items: list[Fact]
    total: int
    limit: int
    offset: int


class FactReviewUpdate(BaseModel):
    review_status: Literal["pending", "approved", "rejected"] | None = None
    subject_entity_id: str | None = Field(default=None, min_length=1, max_length=80)
    predicate: str | None = Field(default=None, min_length=1, max_length=80)
    object_entity_id: str | None = Field(default=None, min_length=1, max_length=80)
    polarity: Literal["affirmed", "negated"] | None = None
    correction_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_update(self):
        if not any(getattr(self, key) is not None for key in ("review_status", "subject_entity_id", "predicate", "object_entity_id", "polarity")):
            raise ValueError("Provide a review status or corrected fact field.")
        return self


class TextDocumentCreate(BaseModel):
    filename: str = Field(default="pasted-text.txt", min_length=1, max_length=240)
    text: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=8, ge=1, le=20)


class SafeGraphQuery(BaseModel):
    document_id: str | None = None
    entity_type: str | None = Field(default=None, max_length=80)
    q: str | None = Field(default=None, max_length=120)
    review_status: Literal["pending", "approved", "rejected"] | None = None
    limit: int = Field(default=250, ge=1, le=1000)


class EntityMergeRequest(BaseModel):
    source_entity_id: str = Field(min_length=1, max_length=80)
    target_entity_id: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=1000)


class ExtractionStats(BaseModel):
    document_count: int
    entity_count: int
    fact_count: int
    pending_fact_count: int
