"""Optional Neo4j mirror for the provenance-preserving SQLite graph."""

import json

from app.core.config import Settings
from app.db.database import connect


def _driver(settings: Settings):
    if not settings.neo4j_password:
        raise RuntimeError("Neo4j is enabled but NEO4J_PASSWORD is empty. Fill it in .env and restart the API.")
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError("Neo4j mode requires the 'neo4j' Python package. Install backend/requirements.txt.") from exc
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))


def initialize_graph_store(settings: Settings) -> None:
    if settings.graph_backend != "neo4j":
        return
    with _driver(settings) as driver:
        driver.verify_connectivity()
        with driver.session(database=settings.neo4j_database) as session:
            session.run("CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT mention_id IF NOT EXISTS FOR (n:Mention) REQUIRE n.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (n:Fact) REQUIRE n.id IS UNIQUE").consume()


def sync_document(settings: Settings, document_id: str) -> None:
    if settings.graph_backend != "neo4j":
        return
    with connect(settings) as connection:
        document = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            delete_document_graph(settings, document_id)
            return
        chunks_path = settings.extracted_dir / f"{document_id}.json"
        try:
            chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            chunks = []
        entity_rows = connection.execute(
            "SELECT DISTINCT e.* FROM entities e JOIN entity_mentions m ON m.entity_id=e.id WHERE m.document_id=?",
            (document_id,),
        ).fetchall()
        mentions = connection.execute("SELECT * FROM entity_mentions WHERE document_id=?", (document_id,)).fetchall()
        fact_rows = connection.execute(
            """SELECT DISTINCT f.* FROM facts f JOIN fact_evidence ev ON ev.fact_id=f.id WHERE ev.document_id=?""",
            (document_id,),
        ).fetchall()
        evidence = connection.execute("SELECT * FROM fact_evidence WHERE document_id=?", (document_id,)).fetchall()
        entities = [dict(row) for row in entity_rows]
        mention_records = [dict(row) for row in mentions]
        facts = [dict(row) for row in fact_rows]
        evidence_records = [dict(row) for row in evidence]
        document_record = dict(document)

    with _driver(settings) as driver, driver.session(database=settings.neo4j_database) as session:
        def write(tx):
            tx.run("""MERGE (d:Document {id:$id}) SET d.filename=$filename,d.sha256=$sha256,d.status=$status,d.createdAt=$created_at""",
                   id=document_id, filename=document_record["original_filename"], sha256=document_record["sha256"], status=document_record["status"], created_at=document_record["created_at"])
            # Remove old document-local material first so reprocessing is idempotent and can remove stale chunks.
            tx.run("MATCH (d:Document {id:$id})-[:HAS_CHUNK]->(c:Chunk) DETACH DELETE c", id=document_id)
            tx.run("MATCH (m:Mention {documentId:$id}) DETACH DELETE m", id=document_id)
            tx.run("MATCH (f:Fact) WHERE NOT EXISTS { MATCH (:Chunk)-[:SUPPORTS]->(f) } DETACH DELETE f").consume()
            tx.run("MATCH (e:Entity) WHERE NOT EXISTS { MATCH (:Mention)-[:REFERS_TO]->(e) } AND NOT EXISTS { MATCH (f:Fact)-[:SUBJECT|OBJECT]->(e) } DETACH DELETE e").consume()
            for index, chunk in enumerate(chunks):
                chunk_id = f"{document_id}:{index}"
                tx.run("""MATCH (d:Document {id:$document_id}) MERGE (c:Chunk {id:$id})
                    SET c.ordinal=$ordinal,c.text=$text,c.locationType=$location_type,c.locationLabel=$location_label,
                    c.pageNumber=$page,c.slideNumber=$slide,c.section=$section,c.startOffset=$start,c.endOffset=$end
                    MERGE (d)-[:HAS_CHUNK]->(c)""", document_id=document_id, id=chunk_id, ordinal=index, text=chunk.get("text", ""),
                    location_type=chunk.get("location_type", "paragraph"), location_label=chunk.get("location_label", "Source"),
                    page=chunk.get("page_number"), slide=chunk.get("slide_number"), section=chunk.get("section"),
                    start=chunk.get("start_offset", 0), end=chunk.get("end_offset", 0))
            for entity in entities:
                tx.run("""MERGE (e:Entity {id:$id}) SET e.canonicalName=$name,e.normalizedName=$normalized,
                    e.type=$type,e.aliases=$aliases""", id=entity["id"], name=entity["canonical_name"], normalized=entity["normalized_name"],
                    type=entity["entity_type"], aliases=json.loads(entity["aliases_json"]))
            for mention in mention_records:
                tx.run("""MATCH (c:Chunk {id:$chunk_id}) MATCH (e:Entity {id:$entity_id})
                    MERGE (m:Mention {id:$id}) SET m.documentId=$document_id,m.surfaceForm=$surface,m.entityType=$type,
                    m.startOffset=$start,m.endOffset=$end,m.extractor=$extractor,m.modelVersion=$model
                    MERGE (c)-[:CONTAINS_MENTION]->(m) MERGE (m)-[:REFERS_TO]->(e)""",
                    chunk_id=f"{document_id}:{mention['chunk_index']}", entity_id=mention["entity_id"], id=mention["id"], document_id=document_id,
                    surface=mention["surface_form"], type=next((item["entity_type"] for item in entities if item["id"] == mention["entity_id"]), "Concept"),
                    start=mention["start_offset"], end=mention["end_offset"], extractor=mention["extraction_method"], model=mention["model_version"])
            for fact in facts:
                tx.run("""MATCH (s:Entity {id:$subject}) MATCH (o:Entity {id:$object})
                    MERGE (f:Fact {id:$id}) SET f.predicate=$predicate,f.polarity=$polarity,f.confidence=$confidence,
                    f.extractionMethod=$method,f.modelVersion=$model,f.reviewStatus=$review,f.createdAt=$created
                    MERGE (f)-[:SUBJECT]->(s) MERGE (f)-[:OBJECT]->(o)""",
                    subject=fact["subject_entity_id"], object=fact["object_entity_id"], id=fact["id"], predicate=fact["predicate"],
                    polarity=fact["polarity"], confidence=fact["confidence"], method=fact["extraction_method"], model=fact["model_version"],
                    review=fact["review_status"], created=fact["created_at"])
            for item in evidence_records:
                tx.run("""MATCH (c:Chunk {id:$chunk_id}) MATCH (f:Fact {id:$fact_id})
                    MERGE (c)-[s:SUPPORTS {evidenceId:$evidence_id}]->(f)
                    SET s.text=$text,s.startOffset=$start,s.endOffset=$end,s.locationLabel=$location,
                    s.documentId=$document_id,s.extractionMethod=$method,s.modelVersion=$model,s.confidence=$confidence""",
                    chunk_id=f"{document_id}:{item['chunk_index']}", fact_id=item["fact_id"], evidence_id=item["id"], text=item["evidence_text"],
                    start=item["start_offset"], end=item["end_offset"], location=item["location_label"], document_id=document_id,
                    method=item["extraction_method"], model=item["model_version"], confidence=item["confidence"])
        session.execute_write(write)


def delete_document_graph(settings: Settings, document_id: str) -> None:
    if settings.graph_backend != "neo4j":
        return
    with _driver(settings) as driver, driver.session(database=settings.neo4j_database) as session:
        session.run("MATCH (d:Document {id:$id})-[:HAS_CHUNK]->(c:Chunk) DETACH DELETE c", id=document_id).consume()
        session.run("MATCH (m:Mention {documentId:$id}) DETACH DELETE m", id=document_id).consume()
        session.run("MATCH (d:Document {id:$id}) DETACH DELETE d", id=document_id).consume()
        session.run("MATCH (f:Fact) WHERE NOT EXISTS { MATCH (:Chunk)-[:SUPPORTS]->(f) } DETACH DELETE f").consume()
        session.run("MATCH (e:Entity) WHERE NOT EXISTS { MATCH (:Mention)-[:REFERS_TO]->(e) } AND NOT EXISTS { MATCH (f:Fact)-[:SUBJECT|OBJECT]->(e) } DETACH DELETE e").consume()


def update_fact_status(settings: Settings, fact_id: str, review_status: str) -> None:
    if settings.graph_backend != "neo4j":
        return
    with connect(settings) as connection:
        fact = connection.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    if not fact:
        return
    with _driver(settings) as driver, driver.session(database=settings.neo4j_database) as session:
        session.run("""MATCH (f:Fact {id:$id}) OPTIONAL MATCH (f)-[r:SUBJECT|OBJECT]->()
            WITH f,collect(r) AS rels FOREACH (rel IN rels | DELETE rel)
            WITH f MATCH (s:Entity {id:$subject}) MATCH (o:Entity {id:$object})
            SET f.predicate=$predicate,f.polarity=$polarity,f.reviewStatus=$status
            MERGE (f)-[:SUBJECT]->(s) MERGE (f)-[:OBJECT]->(o)""",
            id=fact_id, subject=fact["subject_entity_id"], object=fact["object_entity_id"], predicate=fact["predicate"], polarity=fact["polarity"], status=review_status).consume()


def delete_fact_graph(settings: Settings, fact_id: str) -> None:
    if settings.graph_backend != "neo4j":
        return
    with _driver(settings) as driver, driver.session(database=settings.neo4j_database) as session:
        session.run("MATCH (f:Fact {id:$id}) DETACH DELETE f", id=fact_id).consume()


def sync_all_documents(settings: Settings) -> None:
    if settings.graph_backend != "neo4j":
        return
    with connect(settings) as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM documents ORDER BY created_at").fetchall()]
    for document_id in ids:
        sync_document(settings, document_id)
