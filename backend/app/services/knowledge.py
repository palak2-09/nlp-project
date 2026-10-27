"""Knowledge graph projection, lexical retrieval, and evidence-grounded answers."""

from collections import Counter, deque
import csv
from difflib import SequenceMatcher
import io
import json
import math
import re

from app.core.config import Settings
from app.db.database import connect
from app.nlp.repository import get_entity, get_fact, list_entities, list_facts


STOP_WORDS = {"the", "and", "for", "with", "from", "that", "this", "what", "which", "who", "where", "when", "does", "did", "are", "was", "were", "how", "why", "can", "could", "about", "into", "using", "use", "used", "tell", "me", "explain"}


def _terms(value: str) -> list[str]:
    return [term for term in re.findall(r"[\w-]+", value.casefold()) if len(term) > 1 and term not in STOP_WORDS]


def get_graph(settings: Settings, *, document_id: str | None = None, entity_type: str | None = None, q: str | None = None, review_status: str | None = None, limit: int | None = None) -> dict:
    node_limit = min(max(limit or settings.graph_max_nodes, 1), 1000)
    entities, total_all = list_entities(settings, document_id=document_id, limit=1000, offset=0)
    facts, total_facts = list_facts(settings, document_id=document_id, review_status=review_status, limit=2000, offset=0)
    matching_entities = []
    if entity_type or q:
        matches, total_entities = list_entities(settings, document_id=document_id, entity_type=entity_type, query=q, limit=node_limit, offset=0)
        matched_ids = {entity["id"] for entity in matches}
        matching_entities = matches
        relevant_facts = [
            fact for fact in facts
            if fact["subject"]["id"] in matched_ids or fact["object"]["id"] in matched_ids
        ]
    else:
        total_entities = total_all
        relevant_facts = facts

    entity_by_id = {entity["id"]: entity for entity in entities}
    # A useful graph view must retain both endpoints of its facts. Previously it
    # alphabetically took the first N entities, which could fill the limit with
    # isolated nodes and leave a graph with zero visible relations.
    ordered_ids = []
    seen_ids = set()
    for fact in relevant_facts:
        endpoints = [fact["subject"]["id"], fact["object"]["id"]]
        new_endpoints = list(dict.fromkeys(entity_id for entity_id in endpoints if entity_id not in seen_ids))
        if len(ordered_ids) + len(new_endpoints) <= node_limit:
            ordered_ids.extend(new_endpoints)
            seen_ids.update(new_endpoints)
    for entity in matching_entities:
        if entity["id"] not in seen_ids and len(ordered_ids) < node_limit:
            ordered_ids.append(entity["id"])
            seen_ids.add(entity["id"])
    # If there are no facts to draw, keep mention-only entities available. With
    # any fact data, isolated entities remain discoverable via the graph search
    # and type filters instead of displacing connected nodes from the canvas.
    if not entity_type and not q and not relevant_facts and not review_status:
        for entity in entities:
            if entity["id"] not in seen_ids and len(ordered_ids) < node_limit:
                ordered_ids.append(entity["id"])
                seen_ids.add(entity["id"])

    for entity_id in ordered_ids:
        if entity_id in entity_by_id:
            continue
        detail = get_entity(settings, entity_id)
        if detail:
            entity_by_id[entity_id] = {"id": entity_id, "label": detail["label"], "normalized_name": detail["normalized_name"],
                                       "entity_type": detail["entity_type"], "aliases": detail["aliases"], "mention_count": detail["mention_count"], "created_at": detail["created_at"]}
    selected_ids = {entity_id for entity_id in ordered_ids if entity_id in entity_by_id}
    entities = [entity_by_id[entity_id] for entity_id in ordered_ids if entity_id in entity_by_id]
    node_ids = {entity["id"] for entity in entities}
    edges = [fact for fact in facts if fact["subject"]["id"] in node_ids and fact["object"]["id"] in node_ids]
    connected_ids = {endpoint for fact in edges for endpoint in (fact["subject"]["id"], fact["object"]["id"])}
    nodes = [{
        "id": item["id"], "label": item["label"], "entity_type": item["entity_type"],
        "aliases": item["aliases"], "mention_count": item["mention_count"],
        "isolated": item["id"] not in connected_ids,
    } for item in entities]
    return {"nodes": nodes, "edges": edges, "total_entities": total_entities, "total_facts": total_facts,
            "truncated": total_entities > len(nodes), "filters": {"document_id": document_id, "entity_type": entity_type, "q": q, "review_status": review_status}}


def shortest_path(settings: Settings, source_id: str, target_id: str, max_depth: int = 5) -> dict | None:
    if source_id == target_id:
        entity = get_entity(settings, source_id)
        return {"nodes": [{"id": source_id, "label": entity["label"], "entity_type": entity["entity_type"]}], "edges": []} if entity else None
    facts, _ = list_facts(settings, limit=2000, offset=0)
    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for fact in facts:
        adjacency.setdefault(fact["subject"]["id"], []).append((fact["object"]["id"], fact))
        adjacency.setdefault(fact["object"]["id"], []).append((fact["subject"]["id"], fact))
    queue = deque([(source_id, [])])
    visited = {source_id}
    path = None
    while queue:
        current, chain = queue.popleft()
        if len(chain) >= max_depth:
            continue
        for neighbor, fact in adjacency.get(current, []):
            if neighbor in visited:
                continue
            next_chain = chain + [(neighbor, fact)]
            if neighbor == target_id:
                path = next_chain
                queue.clear()
                break
            visited.add(neighbor)
            queue.append((neighbor, next_chain))
    if path is None:
        return None
    ids = [source_id] + [item[0] for item in path]
    node_records = []
    for entity_id in ids:
        entity = get_entity(settings, entity_id)
        if not entity:
            return None
        node_records.append({"id": entity_id, "label": entity["label"], "entity_type": entity["entity_type"]})
    return {"nodes": node_records, "edges": [item[1] for item in path]}


def graph_neighborhood(settings: Settings, entity_id: str, document_id: str | None = None, review_status: str | None = None, depth: int = 1) -> dict | None:
    center = get_entity(settings, entity_id)
    if not center:
        return None
    facts, _ = list_facts(settings, document_id=document_id, review_status=review_status, limit=2000, offset=0)
    discovered = {entity_id}
    frontier = {entity_id}
    selected_edges = {}
    for _ in range(depth):
        next_frontier = set()
        for fact in facts:
            subject, obj = fact["subject"]["id"], fact["object"]["id"]
            if subject in frontier or obj in frontier:
                selected_edges[fact["id"]] = fact
                if subject not in discovered:
                    discovered.add(subject); next_frontier.add(subject)
                if obj not in discovered:
                    discovered.add(obj); next_frontier.add(obj)
        frontier = next_frontier
        if not frontier:
            break
    selected_nodes = []
    for item_id in discovered:
        entity = get_entity(settings, item_id)
        if entity:
            selected_nodes.append({"id": item_id, "label": entity["label"], "entity_type": entity["entity_type"], "aliases": entity["aliases"], "mention_count": entity["mention_count"], "isolated": not any(item_id in (fact["subject"]["id"], fact["object"]["id"]) for fact in selected_edges.values())})
    return {"nodes": selected_nodes, "edges": list(selected_edges.values()), "center_entity_id": entity_id,
            "depth": depth, "truncated": len(selected_nodes) >= settings.graph_max_nodes}


def search(settings: Settings, query: str, limit: int = 10) -> dict:
    terms = list(dict.fromkeys(_terms(query)))
    if not terms:
        return {"query": query, "results": [], "message": "Enter at least one meaningful search term."}
    passages = []
    with connect(settings) as connection:
        docs = connection.execute("SELECT id,original_filename FROM documents WHERE status='completed' ORDER BY created_at DESC").fetchall()
    for document in docs:
        path = settings.extracted_dir / f"{document['id']}.json"
        try:
            chunks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for chunk in chunks:
            text = chunk.get("text", "")
            folded = text.casefold()
            matches = [term for term in terms if term in folded]
            if not matches:
                continue
            sentence_rows = []
            for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text):
                sentence = match.group(0).strip()
                if not sentence:
                    continue
                sentence_terms = [term for term in terms if term in sentence.casefold()]
                if sentence_terms:
                    sentence_rows.append((len(sentence_terms), match.start(), sentence))
            if sentence_rows:
                score, local_start, excerpt = max(sentence_rows, key=lambda row: (row[0], len(row[2])))
                offset = int(chunk.get("start_offset", 0)) + local_start
            else:
                score, excerpt, offset = len(matches), text[:500], int(chunk.get("start_offset", 0))
            passages.append({"kind": "passage", "document_id": document["id"], "filename": document["original_filename"],
                             "location_label": chunk.get("location_label", "Source"), "start_offset": offset,
                             "end_offset": offset + len(excerpt), "text": excerpt, "score": 0.0, "_tokens": _terms(text)})
    if passages:
        corpus_terms = [Counter(item["_tokens"]) for item in passages]
        document_frequency = Counter(term for counts in corpus_terms for term in counts)
        average_length = sum(sum(counts.values()) for counts in corpus_terms) / len(corpus_terms) or 1
        raw_scores = []
        for counts in corpus_terms:
            document_length = sum(counts.values())
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                frequency_docs = document_frequency[term]
                inverse_frequency = math.log(1 + (len(passages) - frequency_docs + 0.5) / (frequency_docs + 0.5))
                denominator = frequency + 1.2 * (0.25 + 0.75 * document_length / average_length)
                score += inverse_frequency * frequency * 2.2 / denominator
            raw_scores.append(score)
        highest = max(raw_scores, default=0.0) or 1.0
        for item, score in zip(passages, raw_scores):
            item["score"] = score / highest
            del item["_tokens"]
    entity_items, _ = list_entities(settings, query=query, limit=limit, offset=0)
    entity_results = [{"kind": "entity", "id": item["id"], "title": item["label"], "subtitle": item["entity_type"], "score": 1.0, "entity": item} for item in entity_items]
    fact_items, _ = list_facts(settings, limit=1000, offset=0)
    fact_results = []
    for fact in fact_items:
        searchable = " ".join([fact["subject"]["label"], fact["predicate"], fact["object"]["label"], *(e["evidence_text"] for e in fact["evidence"]) ]).casefold()
        hits = sum(1 for term in terms if term in searchable)
        if hits:
            evidence = fact["evidence"][0] if fact["evidence"] else None
            fact_results.append({"kind": "fact", "id": fact["id"], "title": f"{fact['subject']['label']} {fact['predicate']} {fact['object']['label']}",
                                "subtitle": fact["review_status"], "score": hits / len(terms), "fact": fact,
                                "filename": evidence["filename"] if evidence else None,
                                "location_label": evidence["location_label"] if evidence else None})
    retrieval_mode = "lexical"
    embedding_note = None
    if settings.enable_embeddings and passages:
        try:
            from app.nlp.embeddings import encode_texts
            candidates = sorted(passages, key=lambda result: result["score"], reverse=True)[:1000]
            vectors = encode_texts(settings.embedding_model, [query, *(item["text"] for item in candidates)])
            for index, item in enumerate(candidates, start=1):
                semantic_score = max(0.0, float(vectors[0] @ vectors[index]))
                item["lexical_score"] = item["score"]
                item["semantic_score"] = semantic_score
                item["score"] = 0.4 * item["lexical_score"] + 0.6 * semantic_score
            passages = candidates
            retrieval_mode = "lexical+semantic"
        except Exception as exc:
            embedding_note = str(exc)
    ordered = sorted(passages, key=lambda result: (result["score"], result["start_offset"]), reverse=True)[:limit]
    ordered += sorted(entity_results + fact_results, key=lambda result: result["score"], reverse=True)[:limit]
    ordered.sort(key=lambda result: result["score"], reverse=True)
    return {"query": query, "results": ordered[:limit], "message": None, "retrieval_mode": retrieval_mode, "embedding_note": embedding_note}


def entity_resolution_candidates(settings: Settings, limit: int = 100) -> dict:
    entities, total = list_entities(settings, limit=500, offset=0)
    labels = [item["label"] for item in entities]
    embedded = None
    embedding_note = None
    if settings.enable_embeddings and labels:
        try:
            from app.nlp.embeddings import encode_texts
            embedded = encode_texts(settings.embedding_model, labels)
        except Exception as exc:
            embedding_note = str(exc)
    candidates = []
    for left_index, left in enumerate(entities):
        for right_index in range(left_index + 1, len(entities)):
            right = entities[right_index]
            if left["entity_type"] != right["entity_type"]:
                continue
            left_tokens, right_tokens = set(left["normalized_name"].split()), set(right["normalized_name"].split())
            union = left_tokens | right_tokens
            token_score = len(left_tokens & right_tokens) / len(union) if union else 0.0
            lexical_score = 0.65 * SequenceMatcher(None, left["normalized_name"], right["normalized_name"]).ratio() + 0.35 * token_score
            alias_match = any(alias.casefold() in {right["label"].casefold(), *(value.casefold() for value in right["aliases"])} for alias in left["aliases"])
            semantic_score = float(embedded[left_index] @ embedded[right_index]) if embedded is not None else None
            score = 1.0 if alias_match else (0.65 * semantic_score + 0.35 * lexical_score if semantic_score is not None else lexical_score)
            if score >= settings.entity_review_threshold:
                candidates.append({"left": left, "right": right, "similarity": round(score, 4),
                                   "similarity_method": "sentence_transformer+lexical" if semantic_score is not None else "lexical_string_similarity",
                                   "review_level": "high_similarity" if score >= settings.entity_match_threshold else "ambiguous",
                                   "action": "Human review is required; entities are never merged automatically."})
    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    return {"items": candidates[:limit], "total_entities_scanned": min(total, 500), "candidate_count": len(candidates),
            "embedding_note": embedding_note, "truncated": total > 500}


def answer_question(settings: Settings, question: str, limit: int = 8) -> dict:
    results = search(settings, question, limit=max(limit, 12))["results"]
    approved = [result["fact"] for result in results if result["kind"] == "fact" and result["fact"]["review_status"] == "approved"]
    if approved:
        citations = []
        statements = []
        seen_facts = set()
        for fact in approved:
            if fact["id"] in seen_facts:
                continue
            seen_facts.add(fact["id"])
            verb = {
                "USES": "uses", "PROPOSES": "proposes", "EVALUATES_ON": "evaluates on", "MEASURED_BY": "is measured by",
                "ADDRESSES": "addresses", "RELATED_TO": "is related to", "PART_OF": "is part of",
                "DEPENDS_ON": "depends on", "REPORTS": "reports", "COMPARES_WITH": "compares with",
                "AUTHORED_BY": "is authored by",
            }.get(fact["predicate"], fact["predicate"].replace("_", " ").lower())
            negation = "does not " if fact["polarity"] == "negated" else ""
            statements.append(f"{fact['subject']['label']} {negation}{verb} {fact['object']['label']}.")
            for evidence in fact["evidence"]:
                citations.append({"document_id": evidence["document_id"], "filename": evidence["filename"], "location_label": evidence["location_label"], "start_offset": evidence["start_offset"], "end_offset": evidence["end_offset"], "quote": evidence["evidence_text"], "fact_id": fact["id"]})
        citations = citations[:limit]
        if settings.llm_provider != "none" and citations:
            try:
                from app.nlp.llm_adapter import generate_grounded_answer
                generated = generate_grounded_answer(settings, question, citations)
                return {"question": question, **generated}
            except Exception:
                pass
        return {"question": question, "answer": " ".join(statements[:limit]), "insufficient_evidence": False, "basis": "approved_graph_facts", "citations": citations,
                "model_fallback": settings.llm_provider != "none"}
    passages = [result for result in results if result["kind"] == "passage"][:limit]
    citations = [{"document_id": item["document_id"], "filename": item["filename"], "location_label": item["location_label"], "start_offset": item["start_offset"], "end_offset": item["end_offset"], "quote": item["text"]} for item in passages]
    if settings.llm_provider != "none" and citations:
        try:
            from app.nlp.llm_adapter import generate_grounded_answer
            generated = generate_grounded_answer(settings, question, citations)
            return {"question": question, **generated}
        except Exception:
            pass
    if not citations:
        answer = "I could not find supporting evidence in the processed documents, so I can't answer this from your collection."
    else:
        answer = "I found these relevant source passages, but no approved fact supports a direct answer. Please review the cited text or approve a supported fact."
    return {"question": question, "answer": answer, "insufficient_evidence": True, "basis": "source_passage_retrieval" if citations else "no_matching_evidence", "citations": citations,
            "model_fallback": settings.llm_provider != "none" and bool(citations)}


def export_graph(settings: Settings, file_format: str, **filters) -> tuple[str, str]:
    graph = get_graph(settings, **filters, limit=1000)
    if file_format == "json":
        return json.dumps(graph, ensure_ascii=False, indent=2), "application/json"
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["record_type", "id", "source", "predicate", "target", "polarity", "review_status", "source_document", "location", "evidence"])
    for node in graph["nodes"]:
        writer.writerow(["entity", node["id"], node["label"], node["entity_type"], "", "", "", "", "", ""])
    for edge in graph["edges"]:
        evidence = edge["evidence"][0] if edge["evidence"] else {}
        writer.writerow(["fact", edge["id"], edge["subject"]["label"], edge["predicate"], edge["object"]["label"], edge["polarity"], edge["review_status"], evidence.get("filename", ""), evidence.get("location_label", ""), evidence.get("evidence_text", "")])
    return output.getvalue(), "text/csv; charset=utf-8"


def analytics(settings: Settings) -> dict:
    with connect(settings) as connection:
        entity_types = [dict(row) for row in connection.execute("SELECT entity_type AS label,COUNT(*) AS count FROM entities GROUP BY entity_type ORDER BY count DESC").fetchall()]
        predicates = [dict(row) for row in connection.execute("SELECT predicate AS label,COUNT(*) AS count FROM facts GROUP BY predicate ORDER BY count DESC").fetchall()]
        reviews = {row["review_status"]: row["count"] for row in connection.execute("SELECT review_status,COUNT(*) AS count FROM facts GROUP BY review_status").fetchall()}
        durations = [row[0] for row in connection.execute("SELECT (julianday(completed_at)-julianday(started_at))*86400 FROM jobs WHERE started_at IS NOT NULL AND completed_at IS NOT NULL AND status='completed'").fetchall() if row[0] is not None]
        duplicate_alias_count = connection.execute("SELECT COUNT(*) FROM entities WHERE aliases_json != '[]'").fetchone()[0]
        job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        completed_jobs = connection.execute("SELECT COUNT(*) FROM jobs WHERE status='completed'").fetchone()[0]
        total_reviews = sum(reviews.values())
        return {"entity_types": entity_types, "predicates": predicates, "review_counts": reviews,
                "review_acceptance_rate": round(reviews.get("approved", 0) / total_reviews, 4) if total_reviews else None,
                "review_rejection_rate": round(reviews.get("rejected", 0) / total_reviews, 4) if total_reviews else None,
                "average_processing_seconds": round(sum(durations) / len(durations), 3) if durations else None,
                "documents_completed": completed_jobs, "processing_jobs": job_count,
                "entities_with_aliases": duplicate_alias_count, "quality_metrics": None,
                "quality_metrics_note": "Precision, recall, and F1 are available only after you add a representative labeled evaluation set; no values are fabricated."}
