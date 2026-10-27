import sqlite3

from app.core.config import Settings
from app.db.database import initialize_database


def test_legacy_facts_migrate_to_polarity_without_losing_evidence(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024,
        chunk_size_chars=500,
        chunk_overlap_chars=50,
        cors_origins=("http://localhost:5173",),
    )
    settings.data_dir.mkdir(parents=True)
    connection = sqlite3.connect(settings.database_path)
    connection.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE documents (
            id TEXT PRIMARY KEY, original_filename TEXT NOT NULL, extension TEXT NOT NULL,
            content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL, error_message TEXT, created_at TEXT NOT NULL, processed_at TEXT,
            page_count INTEGER NOT NULL DEFAULT 0, chunk_count INTEGER NOT NULL DEFAULT 0,
            entity_count INTEGER NOT NULL DEFAULT 0, fact_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE entities (
            id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
            entity_type TEXT NOT NULL, aliases_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
            UNIQUE(normalized_name, entity_type)
        );
        CREATE TABLE facts (
            id TEXT PRIMARY KEY, subject_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            predicate TEXT NOT NULL, object_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            extraction_method TEXT NOT NULL, model_version TEXT NOT NULL, confidence REAL NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
            UNIQUE(subject_entity_id, predicate, object_entity_id)
        );
        CREATE TABLE fact_evidence (
            id TEXT PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE, chunk_index INTEGER NOT NULL,
            evidence_text TEXT NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
            location_type TEXT NOT NULL, location_label TEXT NOT NULL, page_number INTEGER,
            slide_number INTEGER, paragraph_number INTEGER, section TEXT, extraction_method TEXT NOT NULL,
            model_version TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(fact_id, document_id, chunk_index, start_offset, end_offset)
        );
        INSERT INTO documents(id,original_filename,extension,content_type,size_bytes,sha256,status,created_at)
            VALUES('doc','source.pdf','.pdf','application/pdf',1,'sha','completed','now');
        INSERT INTO entities(id,canonical_name,normalized_name,entity_type,created_at)
            VALUES('subject','Alpha','alpha','Method','now'),('object','Beta','beta','Method','now');
        INSERT INTO facts(id,subject_entity_id,predicate,object_entity_id,extraction_method,model_version,confidence,created_at)
            VALUES('fact','subject','USES','object','baseline','rules',0.8,'now');
        INSERT INTO fact_evidence(id,fact_id,document_id,chunk_index,evidence_text,start_offset,end_offset,
            location_type,location_label,extraction_method,model_version,confidence,created_at)
            VALUES('evidence','fact','doc',0,'Alpha uses Beta.',0,16,'page','Page 1','baseline','rules',0.8,'now');
    """)
    connection.commit()
    connection.close()

    initialize_database(settings)

    connection = sqlite3.connect(settings.database_path)
    assert connection.execute("SELECT polarity FROM facts WHERE id='fact'").fetchone() == ("affirmed",)
    assert connection.execute("SELECT fact_id FROM fact_evidence WHERE id='evidence'").fetchone() == ("fact",)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.execute("""INSERT INTO facts(
        id,subject_entity_id,predicate,object_entity_id,polarity,extraction_method,model_version,confidence,created_at
    ) VALUES('negated','subject','USES','object','negated','baseline','rules',0.8,'now')""")
    assert connection.execute("SELECT COUNT(*) FROM facts").fetchone() == (2,)
    connection.close()
