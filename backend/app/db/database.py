import sqlite3
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator

from app.core.config import Settings


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('queued','processing','completed','failed')),
    error_message TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    page_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    entity_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('queued','processing','completed','failed')),
    stage TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_document_id ON jobs(document_id);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(normalized_name, entity_type)
);
CREATE TABLE IF NOT EXISTS entity_mentions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    surface_form TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    location_type TEXT NOT NULL,
    location_label TEXT NOT NULL,
    page_number INTEGER,
    slide_number INTEGER,
    paragraph_number INTEGER,
    section TEXT,
    extraction_method TEXT NOT NULL,
    model_version TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, chunk_index, start_offset, end_offset, entity_id)
);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    polarity TEXT NOT NULL DEFAULT 'affirmed' CHECK(polarity IN ('affirmed','negated')),
    extraction_method TEXT NOT NULL,
    model_version TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL,
    UNIQUE(subject_entity_id, predicate, object_entity_id, polarity)
);
CREATE TABLE IF NOT EXISTS fact_evidence (
    id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    evidence_text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    location_type TEXT NOT NULL,
    location_label TEXT NOT NULL,
    page_number INTEGER,
    slide_number INTEGER,
    paragraph_number INTEGER,
    section TEXT,
    extraction_method TEXT NOT NULL,
    model_version TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(fact_id, document_id, chunk_index, start_offset, end_offset)
);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_document ON entity_mentions(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_document ON fact_evidence(document_id, fact_id);
CREATE INDEX IF NOT EXISTS idx_facts_review_status ON facts(review_status, created_at DESC);
CREATE TABLE IF NOT EXISTS fact_corrections (
    id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fact_corrections_fact ON fact_corrections(fact_id, created_at DESC);
CREATE TABLE IF NOT EXISTS entity_merges (
    id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source_label TEXT NOT NULL,
    target_label TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    undone_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_merges_active ON entity_merges(undone_at, created_at DESC);
"""


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(settings: Settings) -> None:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings) as connection:
        connection.executescript(SCHEMA)
        document_columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
        if "entity_count" not in document_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN entity_count INTEGER NOT NULL DEFAULT 0")
        if "fact_count" not in document_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN fact_count INTEGER NOT NULL DEFAULT 0")
        fact_columns = {row["name"] for row in connection.execute("PRAGMA table_info(facts)").fetchall()}
        if "polarity" not in fact_columns:
            # Upgrade databases created before affirmed/negated facts were introduced.
            # Rebuild the table so the unique key includes polarity, while preserving
            # existing fact IDs and rows referenced by evidence/correction tables.
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE facts_new (
                    id TEXT PRIMARY KEY,
                    subject_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    predicate TEXT NOT NULL,
                    object_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    polarity TEXT NOT NULL DEFAULT 'affirmed' CHECK(polarity IN ('affirmed','negated')),
                    extraction_method TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','approved','rejected')),
                    created_at TEXT NOT NULL,
                    UNIQUE(subject_entity_id, predicate, object_entity_id, polarity)
                )""")
                connection.execute("""INSERT INTO facts_new(
                    id,subject_entity_id,predicate,object_entity_id,polarity,extraction_method,
                    model_version,confidence,review_status,created_at
                ) SELECT id,subject_entity_id,predicate,object_entity_id,'affirmed',extraction_method,
                    model_version,confidence,review_status,created_at FROM facts""")
                connection.execute("DROP TABLE facts")
                connection.execute("ALTER TABLE facts_new RENAME TO facts")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_facts_review_status ON facts(review_status, created_at DESC)")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("PRAGMA foreign_keys = ON")
