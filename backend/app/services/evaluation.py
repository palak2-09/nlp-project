"""Exact-span evaluation against a user-provided JSONL annotation set."""

import json
from collections import defaultdict

from app.core.config import Settings
from app.db.database import connect


def _scores(gold: set, predicted: set) -> dict:
    true_positive = len(gold & predicted)
    precision = true_positive / len(predicted) if predicted else None
    recall = true_positive / len(gold) if gold else None
    f1 = (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and precision + recall else (0.0 if precision == 0 or recall == 0 else None)
    return {"precision": precision, "recall": recall, "f1": f1, "gold_count": len(gold), "prediction_count": len(predicted), "true_positive": true_positive}


def evaluate_annotations(settings: Settings) -> dict:
    path = settings.annotations_path
    if not path or not path.exists():
        return {"available": False, "annotation_file": str(path) if path else None, "message": "No labeled evaluation file found. Copy the format in data/annotations/README.md and label permitted documents. Synthetic examples are not used for metrics."}
    gold_entities = defaultdict(set)
    gold_relations = defaultdict(set)
    warnings = []
    rows = 0
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                document_id = str(item["document_id"])
                split = item.get("split", "test")
                if split != "test":
                    continue
                rows += 1
                for entity in item.get("entities", []):
                    text, entity_type = entity["text"], entity["type"]
                    start, end = int(entity["start_offset"]), int(entity["end_offset"])
                    gold_entities["all"].add((document_id, text.casefold(), entity_type.casefold(), start, end))
                for relation in item.get("relations", []):
                    evidence = relation.get("evidence", "")
                    start, end = int(relation["start_offset"]), int(relation["end_offset"])
                    gold_relations["all"].add((document_id, relation["subject"].casefold(), relation["predicate"], relation["object"].casefold(), start, end, evidence))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(f"Line {line_number} was skipped: {exc}")
    except OSError as exc:
        return {"available": False, "annotation_file": str(path), "message": f"Could not read the annotation file: {exc}"}
    if not rows:
        return {"available": False, "annotation_file": str(path), "message": "No test-split annotation rows were found."}

    entity_predictions = defaultdict(set)
    relation_predictions = defaultdict(set)
    grounded = []
    known_docs = set()
    with connect(settings) as connection:
        for document_id in {item[0] for item in gold_entities["all"]} | {item[0] for item in gold_relations["all"]}:
            if connection.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone():
                known_docs.add(document_id)
            else:
                warnings.append(f"Document {document_id} is not in the local collection.")
        mentions = connection.execute("""SELECT m.document_id,m.surface_form,e.entity_type,m.start_offset,m.end_offset,m.extraction_method
            FROM entity_mentions m JOIN entities e ON e.id=m.entity_id""").fetchall()
        for row in mentions:
            if row["document_id"] in known_docs:
                record = (row["document_id"], row["surface_form"].casefold(), row["entity_type"].casefold(), row["start_offset"], row["end_offset"])
                entity_predictions["all"].add(record)
                entity_predictions[row["extraction_method"]].add(record)
        relations = connection.execute("""SELECT ev.document_id,s.canonical_name AS subject_name,f.predicate,o.canonical_name AS object_name,
            ev.start_offset,ev.end_offset,ev.evidence_text,ev.extraction_method FROM fact_evidence ev
            JOIN facts f ON f.id=ev.fact_id JOIN entities s ON s.id=f.subject_entity_id JOIN entities o ON o.id=f.object_entity_id""").fetchall()
        for row in relations:
            if row["document_id"] not in known_docs:
                continue
            record = (row["document_id"], row["subject_name"].casefold(), row["predicate"], row["object_name"].casefold(), row["start_offset"], row["end_offset"], row["evidence_text"])
            relation_predictions["all"].add(record)
            relation_predictions[row["extraction_method"]].add(record)
            chunks_path = settings.extracted_dir / f"{row['document_id']}.json"
            try:
                chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
                source_text = next((chunk["text"] for chunk in chunks if chunk.get("start_offset", 0) <= row["start_offset"] and chunk.get("end_offset", 0) >= row["end_offset"]), "")
                local_start = row["start_offset"] - next((chunk.get("start_offset", 0) for chunk in chunks if chunk.get("start_offset", 0) <= row["start_offset"] and chunk.get("end_offset", 0) >= row["end_offset"]), 0)
                is_grounded = source_text[local_start:local_start + len(row["evidence_text"])] == row["evidence_text"]
            except (OSError, json.JSONDecodeError):
                is_grounded = False
            grounded.append(is_grounded)

    entity_metrics = {"all": _scores(gold_entities["all"], entity_predictions["all"])}
    relation_metrics = {"all": _scores(gold_relations["all"], relation_predictions["all"])}
    for method in sorted(set(entity_predictions) | set(relation_predictions)):
        if method != "all":
            entity_metrics[method] = _scores(gold_entities["all"], entity_predictions[method])
            relation_metrics[method] = _scores(gold_relations["all"], relation_predictions[method])
    return {"available": True, "annotation_file": str(path), "split": "test", "annotation_rows": rows,
            "documents_matched": len(known_docs), "entity_metrics": entity_metrics, "relation_metrics": relation_metrics,
            "evidence_grounding_rate": sum(grounded) / len(grounded) if grounded else None,
            "resolution_accuracy": None, "resolution_note": "Add canonical_cluster_id labels to an entity-resolution annotation set to measure pairwise resolution accuracy.",
            "warnings": warnings}
