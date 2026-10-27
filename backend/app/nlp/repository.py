import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.core.config import Settings
from app.db.database import connect
from app.nlp.baseline import EntityMention, FactCandidate, normalize_name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def persist_extraction(
    settings: Settings,
    document_id: str,
    mentions: list[EntityMention],
    candidates: list[FactCandidate],
) -> tuple[int, int]:
    timestamp = _now_iso()
    entity_ids: dict[tuple[str, str], str] = {}
    with connect(settings) as connection:
        document = connection.execute("SELECT id FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise ValueError("Document was deleted before extraction could be stored.")

        for mention in mentions:
            normalized = normalize_name(mention.canonical_name)
            key = (normalized, mention.entity_type)
            if key not in entity_ids:
                row = connection.execute(
                    "SELECT id,canonical_name,aliases_json FROM entities WHERE normalized_name=? AND entity_type=?", key,
                ).fetchone()
                if not row:
                    alias_matches = []
                    for candidate_row in connection.execute(
                        "SELECT id,canonical_name,aliases_json FROM entities WHERE entity_type=?", (mention.entity_type,),
                    ).fetchall():
                        existing_aliases = json.loads(candidate_row["aliases_json"])
                        if any(normalize_name(alias) == normalized for alias in existing_aliases):
                            alias_matches.append(candidate_row)
                    if len(alias_matches) == 1:
                        row = alias_matches[0]
                if not row:
                    proposed_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT OR IGNORE INTO entities(id,canonical_name,normalized_name,entity_type,aliases_json,created_at) VALUES(?,?,?,?,?,?)",
                        (proposed_id, mention.canonical_name, normalized, mention.entity_type, "[]", timestamp),
                    )
                    row = connection.execute(
                        "SELECT id,canonical_name,aliases_json FROM entities WHERE normalized_name=? AND entity_type=?", key,
                    ).fetchone()
                entity_ids[key] = row["id"]

            entity_id = entity_ids[key]
            entity = connection.execute("SELECT canonical_name,aliases_json FROM entities WHERE id=?", (entity_id,)).fetchone()
            aliases = json.loads(entity["aliases_json"])
            if normalize_name(mention.surface_form) != normalize_name(entity["canonical_name"]) and mention.surface_form not in aliases:
                aliases.append(mention.surface_form)
                connection.execute("UPDATE entities SET aliases_json=? WHERE id=?", (json.dumps(aliases, ensure_ascii=False), entity_id))
            connection.execute(
                """INSERT OR IGNORE INTO entity_mentions(
                    id,entity_id,document_id,chunk_index,surface_form,start_offset,end_offset,
                    location_type,location_label,page_number,slide_number,paragraph_number,section,
                    extraction_method,model_version,confidence,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), entity_id, document_id, mention.chunk_index, mention.surface_form,
                    mention.start_offset, mention.end_offset, mention.location_type, mention.location_label,
                    mention.page_number, mention.slide_number, mention.paragraph_number, mention.section,
                    mention.extraction_method, mention.model_version, mention.confidence, timestamp,
                ),
            )

        def resolve_entity(name: str, entity_type: str) -> str:
            key = (normalize_name(name), entity_type)
            if key not in entity_ids:
                row = connection.execute("SELECT id FROM entities WHERE normalized_name=? AND entity_type=?", key).fetchone()
                if not row:
                    raise ValueError(f"Fact endpoint '{name}' was not created as an entity.")
                entity_ids[key] = row["id"]
            return entity_ids[key]

        for candidate in candidates:
            subject_id = resolve_entity(candidate.subject_name, candidate.subject_type)
            object_id = resolve_entity(candidate.object_name, candidate.object_type)
            proposed_fact_id = str(uuid.uuid4())
            connection.execute(
                """INSERT OR IGNORE INTO facts(
                    id,subject_entity_id,predicate,object_entity_id,polarity,extraction_method,model_version,confidence,review_status,created_at
                ) VALUES(?,?,?,?,?,?,?,?, 'pending',?)""",
                (
                    proposed_fact_id, subject_id, candidate.predicate, object_id, candidate.polarity,
                    candidate.extraction_method, candidate.model_version, candidate.confidence, timestamp,
                ),
            )
            fact_row = connection.execute(
                "SELECT id FROM facts WHERE subject_entity_id=? AND predicate=? AND object_entity_id=? AND polarity=?",
                (subject_id, candidate.predicate, object_id, candidate.polarity),
            ).fetchone()
            connection.execute(
                """INSERT OR IGNORE INTO fact_evidence(
                    id,fact_id,document_id,chunk_index,evidence_text,start_offset,end_offset,
                    location_type,location_label,page_number,slide_number,paragraph_number,section,
                    extraction_method,model_version,confidence,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), fact_row["id"], document_id, candidate.chunk_index,
                    candidate.evidence_text, candidate.start_offset, candidate.end_offset,
                    candidate.location_type, candidate.location_label, candidate.page_number,
                    candidate.slide_number, candidate.paragraph_number, candidate.section,
                    candidate.extraction_method, candidate.model_version, candidate.confidence, timestamp,
                ),
            )

        entity_count = connection.execute("SELECT COUNT(DISTINCT entity_id) FROM entity_mentions WHERE document_id=?", (document_id,)).fetchone()[0]
        fact_count = connection.execute("SELECT COUNT(DISTINCT fact_id) FROM fact_evidence WHERE document_id=?", (document_id,)).fetchone()[0]
        return entity_count, fact_count


def get_stats(settings: Settings) -> dict[str, int]:
    with connect(settings) as connection:
        return {
            "document_count": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "entity_count": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "fact_count": connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
            "pending_fact_count": connection.execute("SELECT COUNT(*) FROM facts WHERE review_status='pending'").fetchone()[0],
        }


def list_entities(
    settings: Settings,
    document_id: str | None = None,
    entity_type: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    where: list[str] = []
    values: list[object] = []
    if document_id:
        where.append("m.document_id=?")
        values.append(document_id)
    if entity_type:
        where.append("lower(e.entity_type)=lower(?)")
        values.append(entity_type)
    if query:
        where.append("(e.canonical_name LIKE ? OR e.aliases_json LIKE ?)")
        needle = f"%{query}%"
        values.extend((needle, needle))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect(settings) as connection:
        total = connection.execute(
            f"SELECT COUNT(DISTINCT e.id) FROM entities e LEFT JOIN entity_mentions m ON m.entity_id=e.id {clause}", values,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT e.id,e.canonical_name,e.normalized_name,e.entity_type,e.aliases_json,e.created_at,
                COUNT(DISTINCT m.id) AS mention_count
                FROM entities e LEFT JOIN entity_mentions m ON m.entity_id=e.id {clause}
                GROUP BY e.id ORDER BY e.canonical_name COLLATE NOCASE LIMIT ? OFFSET ?""",
            [*values, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            items.append({
                "id": row["id"], "label": row["canonical_name"], "normalized_name": row["normalized_name"],
                "entity_type": row["entity_type"], "aliases": json.loads(row["aliases_json"]),
                "mention_count": row["mention_count"], "created_at": row["created_at"],
            })
        return items, total


def get_entity(settings: Settings, entity_id: str) -> dict | None:
    with connect(settings) as connection:
        entity = connection.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            return None
        mentions = connection.execute(
            """SELECT m.*,d.original_filename FROM entity_mentions m
                JOIN documents d ON d.id=m.document_id WHERE m.entity_id=?
                ORDER BY m.created_at DESC,m.start_offset LIMIT 200""",
            (entity_id,),
        ).fetchall()
        mention_count = connection.execute("SELECT COUNT(*) FROM entity_mentions WHERE entity_id=?", (entity_id,)).fetchone()[0]
        result = {
            "id": entity["id"], "label": entity["canonical_name"], "normalized_name": entity["normalized_name"],
            "entity_type": entity["entity_type"], "aliases": json.loads(entity["aliases_json"]),
            "mention_count": mention_count, "created_at": entity["created_at"],
            "mentions": [{
                "document_id": row["document_id"], "filename": row["original_filename"],
                "chunk_index": row["chunk_index"], "surface_form": row["surface_form"],
                "start_offset": row["start_offset"], "end_offset": row["end_offset"],
                "location_type": row["location_type"], "location_label": row["location_label"],
                "page_number": row["page_number"], "slide_number": row["slide_number"],
                "paragraph_number": row["paragraph_number"], "section": row["section"],
                "extraction_method": row["extraction_method"], "model_version": row["model_version"],
                "confidence": row["confidence"],
            } for row in mentions],
        }
        return result


def merge_entities(settings: Settings, source_entity_id: str, target_entity_id: str, note: str = "") -> dict | None:
    if source_entity_id == target_entity_id:
        raise ValueError("Choose two different entities to merge.")
    merge_id = str(uuid.uuid4())
    timestamp = _now_iso()
    with connect(settings) as connection:
        source = connection.execute("SELECT * FROM entities WHERE id=?", (source_entity_id,)).fetchone()
        target = connection.execute("SELECT * FROM entities WHERE id=?", (target_entity_id,)).fetchone()
        if not source or not target:
            return None
        if source["entity_type"] != target["entity_type"]:
            raise ValueError("Entities must have the same type before they can be merged.")
        if connection.execute("SELECT 1 FROM entity_merges WHERE target_entity_id=? AND undone_at IS NULL", (source_entity_id,)).fetchone():
            raise ValueError("Undo earlier merges into this entity before merging it as the source.")
        mentions = [dict(row) for row in connection.execute("SELECT * FROM entity_mentions WHERE entity_id=?", (source_entity_id,)).fetchall()]
        source_facts = [dict(row) for row in connection.execute("SELECT * FROM facts WHERE subject_entity_id=? OR object_entity_id=?", (source_entity_id, source_entity_id)).fetchall()]
        fact_snapshots = []
        for fact in source_facts:
            evidence = [dict(row) for row in connection.execute("SELECT * FROM fact_evidence WHERE fact_id=?", (fact["id"],)).fetchall()]
            corrections = [dict(row) for row in connection.execute("SELECT * FROM fact_corrections WHERE fact_id=?", (fact["id"],)).fetchall()]
            changed_subject = target_entity_id if fact["subject_entity_id"] == source_entity_id else fact["subject_entity_id"]
            changed_object = target_entity_id if fact["object_entity_id"] == source_entity_id else fact["object_entity_id"]
            duplicate = connection.execute(
                "SELECT id FROM facts WHERE subject_entity_id=? AND predicate=? AND object_entity_id=? AND polarity=? AND id<>?",
                (changed_subject, fact["predicate"], changed_object, fact["polarity"], fact["id"]),
            ).fetchone()
            fact_snapshots.append({"fact": fact, "evidence": evidence, "corrections": corrections, "duplicate_id": duplicate["id"] if duplicate else None})
        previous_target_aliases = json.loads(target["aliases_json"])
        merge_aliases = [source["canonical_name"], *json.loads(source["aliases_json"])]
        merged_aliases = list(dict.fromkeys([*previous_target_aliases, *(alias for alias in merge_aliases if normalize_name(alias) != target["normalized_name"])]))
        payload = {"source": dict(source), "target_aliases_before": previous_target_aliases, "merge_aliases": merge_aliases,
                   "mentions": mentions, "facts": fact_snapshots}
        for snapshot in fact_snapshots:
            fact = snapshot["fact"]
            changed_subject = target_entity_id if fact["subject_entity_id"] == source_entity_id else fact["subject_entity_id"]
            changed_object = target_entity_id if fact["object_entity_id"] == source_entity_id else fact["object_entity_id"]
            duplicate_id = snapshot["duplicate_id"]
            if duplicate_id:
                for evidence in snapshot["evidence"]:
                    duplicate_evidence = connection.execute(
                        "SELECT id FROM fact_evidence WHERE fact_id=? AND document_id=? AND chunk_index=? AND start_offset=? AND end_offset=?",
                        (duplicate_id, evidence["document_id"], evidence["chunk_index"], evidence["start_offset"], evidence["end_offset"]),
                    ).fetchone()
                    if duplicate_evidence:
                        evidence["dropped_on_merge"] = True
                    else:
                        connection.execute("UPDATE fact_evidence SET fact_id=? WHERE id=?", (duplicate_id, evidence["id"]))
                connection.execute("DELETE FROM facts WHERE id=?", (fact["id"],))
            else:
                connection.execute("UPDATE facts SET subject_entity_id=?,object_entity_id=? WHERE id=?", (changed_subject, changed_object, fact["id"]))
        connection.execute("UPDATE entity_mentions SET entity_id=? WHERE entity_id=?", (target_entity_id, source_entity_id))
        connection.execute("UPDATE entities SET aliases_json=? WHERE id=?", (json.dumps(merged_aliases, ensure_ascii=False), target_entity_id))
        connection.execute("DELETE FROM entities WHERE id=?", (source_entity_id,))
        connection.execute("""UPDATE documents SET
            entity_count=(SELECT COUNT(DISTINCT entity_id) FROM entity_mentions WHERE document_id=documents.id),
            fact_count=(SELECT COUNT(DISTINCT fact_id) FROM fact_evidence WHERE document_id=documents.id)""")
        connection.execute(
            "INSERT INTO entity_merges(id,source_entity_id,target_entity_id,source_label,target_label,payload_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (merge_id, source_entity_id, target_entity_id, source["canonical_name"], target["canonical_name"], json.dumps(payload, ensure_ascii=False), note.strip(), timestamp),
        )
    return {"id": merge_id, "source_entity_id": source_entity_id, "target_entity_id": target_entity_id,
            "source_label": source["canonical_name"], "target_label": target["canonical_name"], "created_at": timestamp, "undone_at": None}


def undo_entity_merge(settings: Settings, merge_id: str) -> bool:
    with connect(settings) as connection:
        merge = connection.execute("SELECT * FROM entity_merges WHERE id=? AND undone_at IS NULL", (merge_id,)).fetchone()
        if not merge:
            return False
        payload = json.loads(merge["payload_json"])
        source = payload["source"]
        try:
            connection.execute(
                "INSERT INTO entities(id,canonical_name,normalized_name,entity_type,aliases_json,created_at) VALUES(?,?,?,?,?,?)",
                (source["id"], source["canonical_name"], source["normalized_name"], source["entity_type"], source["aliases_json"], source["created_at"]),
            )
            for mention in payload["mentions"]:
                connection.execute("UPDATE entity_mentions SET entity_id=? WHERE id=?", (source["id"], mention["id"]))
            current_aliases = json.loads(connection.execute("SELECT aliases_json FROM entities WHERE id=?", (merge["target_entity_id"],)).fetchone()[0])
            original_aliases = set(payload["target_aliases_before"])
            remove_aliases = set(payload["merge_aliases"])
            restored_aliases = [alias for alias in current_aliases if alias in original_aliases or alias not in remove_aliases]
            connection.execute("UPDATE entities SET aliases_json=? WHERE id=?", (json.dumps(restored_aliases, ensure_ascii=False), merge["target_entity_id"]))
            for snapshot in payload["facts"]:
                fact = snapshot["fact"]
                if connection.execute("SELECT 1 FROM facts WHERE id=?", (fact["id"],)).fetchone():
                    connection.execute("UPDATE facts SET subject_entity_id=?,predicate=?,object_entity_id=?,polarity=?,extraction_method=?,model_version=?,confidence=?,review_status=?,created_at=? WHERE id=?",
                        (fact["subject_entity_id"], fact["predicate"], fact["object_entity_id"], fact["polarity"], fact["extraction_method"], fact["model_version"], fact["confidence"], fact["review_status"], fact["created_at"], fact["id"]))
                else:
                    connection.execute("INSERT INTO facts(id,subject_entity_id,predicate,object_entity_id,polarity,extraction_method,model_version,confidence,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (fact["id"], fact["subject_entity_id"], fact["predicate"], fact["object_entity_id"], fact["polarity"], fact["extraction_method"], fact["model_version"], fact["confidence"], fact["review_status"], fact["created_at"]))
                for evidence in snapshot["evidence"]:
                    existing = connection.execute("SELECT id FROM fact_evidence WHERE id=?", (evidence["id"],)).fetchone()
                    if existing:
                        connection.execute("UPDATE fact_evidence SET fact_id=? WHERE id=?", (fact["id"], evidence["id"]))
                    else:
                        values = [evidence[key] for key in ("id", "fact_id", "document_id", "chunk_index", "evidence_text", "start_offset", "end_offset", "location_type", "location_label", "page_number", "slide_number", "paragraph_number", "section", "extraction_method", "model_version", "confidence", "created_at")]
                        connection.execute("INSERT INTO fact_evidence(id,fact_id,document_id,chunk_index,evidence_text,start_offset,end_offset,location_type,location_label,page_number,slide_number,paragraph_number,section,extraction_method,model_version,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
                for correction in snapshot.get("corrections", []):
                    connection.execute("INSERT OR IGNORE INTO fact_corrections(id,fact_id,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?)",
                        (correction["id"], fact["id"], correction["before_json"], correction["after_json"], correction["note"], correction["created_at"]))
            connection.execute("UPDATE entity_merges SET undone_at=? WHERE id=?", (_now_iso(), merge_id))
            connection.execute("""UPDATE documents SET
                entity_count=(SELECT COUNT(DISTINCT entity_id) FROM entity_mentions WHERE document_id=documents.id),
                fact_count=(SELECT COUNT(DISTINCT fact_id) FROM fact_evidence WHERE document_id=documents.id)""")
        except sqlite3.IntegrityError as exc:
            raise ValueError("This merge can no longer be undone cleanly because conflicting entities or facts were added. Resolve the conflict first.") from exc
    return True


def list_entity_merges(settings: Settings, limit: int = 50) -> list[dict]:
    with connect(settings) as connection:
        rows = connection.execute("SELECT id,source_entity_id,target_entity_id,source_label,target_label,note,created_at,undone_at FROM entity_merges ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


def active_merge_log(settings: Settings, limit: int = 50) -> list[dict]:
    return [item for item in list_entity_merges(settings, limit) if item["undone_at"] is None]


def _fact_evidence(connection: sqlite3.Connection, fact_id: str, document_id: str | None = None) -> list[dict]:
    where = "AND ev.document_id=?" if document_id else ""
    values: tuple[object, ...] = (fact_id, document_id) if document_id else (fact_id,)
    rows = connection.execute(
        f"""SELECT ev.*,d.original_filename FROM fact_evidence ev
            JOIN documents d ON d.id=ev.document_id
            WHERE ev.fact_id=? {where} ORDER BY ev.created_at,ev.start_offset""",
        values,
    ).fetchall()
    return [{
        "document_id": row["document_id"], "filename": row["original_filename"],
        "chunk_index": row["chunk_index"], "evidence_text": row["evidence_text"],
        "start_offset": row["start_offset"], "end_offset": row["end_offset"],
        "location_type": row["location_type"], "location_label": row["location_label"],
        "page_number": row["page_number"], "slide_number": row["slide_number"],
        "paragraph_number": row["paragraph_number"], "section": row["section"],
        "extraction_method": row["extraction_method"], "model_version": row["model_version"],
        "confidence": row["confidence"], "created_at": row["created_at"],
    } for row in rows]


def list_facts(
    settings: Settings,
    document_id: str | None = None,
    predicate: str | None = None,
    review_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    where: list[str] = []
    values: list[object] = []
    if document_id:
        where.append("ev.document_id=?")
        values.append(document_id)
    if predicate:
        where.append("f.predicate=?")
        values.append(predicate)
    if review_status:
        where.append("f.review_status=?")
        values.append(review_status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect(settings) as connection:
        total = connection.execute(
            f"""SELECT COUNT(DISTINCT f.id) FROM facts f
                JOIN fact_evidence ev ON ev.fact_id=f.id {clause}""", values,
        ).fetchone()[0]
        rows = connection.execute(
                f"""SELECT DISTINCT f.id,f.subject_entity_id,f.predicate,f.object_entity_id,f.polarity,
                f.extraction_method,f.model_version,f.confidence,f.review_status,f.created_at,
                s.canonical_name AS subject_label,s.entity_type AS subject_type,
                o.canonical_name AS object_label,o.entity_type AS object_type
                FROM facts f JOIN fact_evidence ev ON ev.fact_id=f.id
                JOIN entities s ON s.id=f.subject_entity_id JOIN entities o ON o.id=f.object_entity_id
                {clause} ORDER BY f.created_at DESC LIMIT ? OFFSET ?""",
            [*values, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            corrections = connection.execute(
                "SELECT id,before_json,after_json,note,created_at FROM fact_corrections WHERE fact_id=? ORDER BY created_at DESC",
                (row["id"],),
            ).fetchall()
            items.append({
                "id": row["id"],
                "subject": {"id": row["subject_entity_id"], "label": row["subject_label"], "entity_type": row["subject_type"]},
                "predicate": row["predicate"],
                "polarity": row["polarity"],
                "object": {"id": row["object_entity_id"], "label": row["object_label"], "entity_type": row["object_type"]},
                "extraction_method": row["extraction_method"], "model_version": row["model_version"],
                "confidence": row["confidence"], "review_status": row["review_status"],
                "created_at": row["created_at"],
                "evidence": _fact_evidence(connection, row["id"], document_id),
                "corrections": [{"id": item["id"], "before": json.loads(item["before_json"]), "after": json.loads(item["after_json"]), "note": item["note"], "created_at": item["created_at"]} for item in corrections],
            })
        return items, total


def get_fact(settings: Settings, fact_id: str) -> dict | None:
    with connect(settings) as connection:
        row = connection.execute(
            """SELECT f.id,f.subject_entity_id,f.predicate,f.object_entity_id,f.polarity,f.extraction_method,f.model_version,
                f.confidence,f.review_status,f.created_at,s.canonical_name AS subject_label,s.entity_type AS subject_type,
                o.canonical_name AS object_label,o.entity_type AS object_type
                FROM facts f JOIN entities s ON s.id=f.subject_entity_id JOIN entities o ON o.id=f.object_entity_id
                WHERE f.id=?""", (fact_id,),
        ).fetchone()
        if not row:
            return None
        corrections = connection.execute(
            "SELECT id,before_json,after_json,note,created_at FROM fact_corrections WHERE fact_id=? ORDER BY created_at DESC",
            (fact_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "subject": {"id": row["subject_entity_id"], "label": row["subject_label"], "entity_type": row["subject_type"]},
            "predicate": row["predicate"],
            "polarity": row["polarity"],
            "object": {"id": row["object_entity_id"], "label": row["object_label"], "entity_type": row["object_type"]},
            "extraction_method": row["extraction_method"], "model_version": row["model_version"],
            "confidence": row["confidence"], "review_status": row["review_status"],
            "created_at": row["created_at"], "evidence": _fact_evidence(connection, fact_id),
            "corrections": [{"id": item["id"], "before": json.loads(item["before_json"]), "after": json.loads(item["after_json"]), "note": item["note"], "created_at": item["created_at"]} for item in corrections],
        }


def correct_fact(settings: Settings, fact_id: str, update: dict) -> dict | None:
    from app.nlp.schema import load_schema
    allowed_predicates = set(load_schema().relationship_types)
    with connect(settings) as connection:
        current = connection.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if not current:
            return None
        changes = {key: value for key, value in update.items() if key in {"subject_entity_id", "predicate", "object_entity_id", "polarity", "review_status"} and value is not None}
        note = (update.get("correction_note") or "").strip()
        if not changes:
            raise ValueError("Provide a corrected fact field or a review status.")
        if "predicate" in changes and changes["predicate"] not in allowed_predicates:
            raise ValueError("The corrected predicate is not present in the configured relation schema.")
        for endpoint in ("subject_entity_id", "object_entity_id"):
            if endpoint in changes and not connection.execute("SELECT 1 FROM entities WHERE id=?", (changes[endpoint],)).fetchone():
                raise ValueError(f"The selected {endpoint.replace('_entity_id', '')} entity does not exist.")
        before = {key: current[key] for key in ("subject_entity_id", "predicate", "object_entity_id", "polarity", "review_status")}
        after = {**before, **changes}
        try:
            connection.execute(
                "UPDATE facts SET subject_entity_id=?,predicate=?,object_entity_id=?,polarity=?,review_status=? WHERE id=?",
                (after["subject_entity_id"], after["predicate"], after["object_entity_id"], after["polarity"], after["review_status"], fact_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That correction duplicates an existing subject, predicate, object, and polarity assertion.") from exc
        if any(before[key] != after[key] for key in before):
            connection.execute(
                "INSERT INTO fact_corrections(id,fact_id,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), fact_id, json.dumps(before), json.dumps(after), note or "Fact corrected during review.", _now_iso()),
            )
    return get_fact(settings, fact_id)


def update_fact_review(settings: Settings, fact_id: str, review_status: str) -> dict | None:
    if review_status not in {"pending", "approved", "rejected"}:
        raise ValueError("Review status must be pending, approved, or rejected.")
    with connect(settings) as connection:
        cursor = connection.execute("UPDATE facts SET review_status=? WHERE id=?", (review_status, fact_id))
        if cursor.rowcount == 0:
            return None
    return get_fact(settings, fact_id)


def delete_fact(settings: Settings, fact_id: str) -> bool:
    with connect(settings) as connection:
        cursor = connection.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        return cursor.rowcount > 0
