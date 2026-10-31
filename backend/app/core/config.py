from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent if (BACKEND_ROOT.parent / "frontend").exists() else BACKEND_ROOT
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int
    chunk_size_chars: int
    chunk_overlap_chars: int
    cors_origins: tuple[str, ...]
    graph_backend: str = "sqlite"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    enable_embeddings: bool = False
    entity_match_threshold: float = 0.93
    entity_review_threshold: float = 0.78
    llm_provider: str = "gemini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: int = 45
    llm_max_chunks_per_document: int = 20
    llm_max_input_chars: int = 12000
    llm_max_output_tokens: int = 1200
    graph_max_nodes: int = 250
    minimum_extraction_confidence: float = 0.0
    annotations_path: Path | None = PROJECT_ROOT / "data" / "annotations" / "gold.jsonl"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "papergraph.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"


def get_settings() -> Settings:
    data_dir = Path(os.getenv("PAPERGRAPH_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    annotations_path = Path(os.getenv("PAPERGRAPH_ANNOTATIONS_PATH", str(PROJECT_ROOT / "data" / "annotations" / "gold.jsonl"))).expanduser()
    if not annotations_path.is_absolute():
        annotations_path = PROJECT_ROOT / annotations_path
    origins = os.getenv("PAPERGRAPH_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    graph_backend = os.getenv("PAPERGRAPH_GRAPH_BACKEND", "sqlite").strip().lower()
    if graph_backend not in {"sqlite", "neo4j"}:
        raise ValueError("PAPERGRAPH_GRAPH_BACKEND must be 'sqlite' or 'neo4j'.")
    llm_provider = os.getenv("PAPERGRAPH_LLM_PROVIDER", "gemini").strip().lower()
    if llm_provider not in {"none", "gemini", "openai_compatible"}:
        raise ValueError("PAPERGRAPH_LLM_PROVIDER must be 'none', 'gemini', or 'openai_compatible'.")
    minimum_confidence = float(os.getenv("PAPERGRAPH_MIN_EXTRACTION_CONFIDENCE", "0.0"))
    match_threshold = float(os.getenv("PAPERGRAPH_ENTITY_MATCH_THRESHOLD", "0.93"))
    review_threshold = float(os.getenv("PAPERGRAPH_ENTITY_REVIEW_THRESHOLD", "0.78"))
    if not 0 <= minimum_confidence <= 1:
        raise ValueError("PAPERGRAPH_MIN_EXTRACTION_CONFIDENCE must be between 0 and 1.")
    if not 0 <= review_threshold <= match_threshold <= 1:
        raise ValueError("Entity resolution thresholds must satisfy 0 <= review <= match <= 1.")
    return Settings(
        data_dir=data_dir,
        max_upload_bytes=int(os.getenv("PAPERGRAPH_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
        chunk_size_chars=int(os.getenv("PAPERGRAPH_CHUNK_SIZE_CHARS", "1200")),
        chunk_overlap_chars=int(os.getenv("PAPERGRAPH_CHUNK_OVERLAP_CHARS", "120")),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        entity_match_threshold=match_threshold,
        entity_review_threshold=review_threshold,
        graph_backend=graph_backend,
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687").strip(),
        neo4j_username=os.getenv("NEO4J_USERNAME", "neo4j").strip(),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j").strip(),
        embedding_model=os.getenv("PAPERGRAPH_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip(),
        enable_embeddings=os.getenv("PAPERGRAPH_ENABLE_EMBEDDINGS", "false").strip().lower() in {"1", "true", "yes", "on"},
        llm_provider=llm_provider,
        llm_base_url=os.getenv("PAPERGRAPH_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        llm_api_key=os.getenv("PAPERGRAPH_LLM_API_KEY", ""),
        llm_model=os.getenv("PAPERGRAPH_LLM_MODEL", "gemini-2.5-flash").strip(),
        llm_timeout_seconds=int(os.getenv("PAPERGRAPH_LLM_TIMEOUT_SECONDS", "45")),
        llm_max_chunks_per_document=int(os.getenv("PAPERGRAPH_LLM_MAX_CHUNKS_PER_DOCUMENT", "20")),
        llm_max_input_chars=int(os.getenv("PAPERGRAPH_LLM_MAX_INPUT_CHARS", "12000")),
        llm_max_output_tokens=int(os.getenv("PAPERGRAPH_LLM_MAX_OUTPUT_TOKENS", "1200")),
        graph_max_nodes=int(os.getenv("PAPERGRAPH_GRAPH_MAX_NODES", "250")),
        minimum_extraction_confidence=minimum_confidence,
        annotations_path=annotations_path,
    )
