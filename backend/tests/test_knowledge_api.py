import json
from dataclasses import replace

from app.db.database import connect
from app.nlp.llm_adapter import extract_llm
from app.nlp.schema import load_schema


def test_pasted_text_graph_search_grounded_qa_export_and_safe_query(client):
    source = "The method Evidence Linking connects a Claim to a Source Passage."
    response = client.post("/api/documents/text", json={"filename": "pasted-note.txt", "text": source})
    assert response.status_code == 202
    document = client.get(f"/api/documents/{response.json()['id']}").json()
    assert document["status"] == "completed"
    assert document["filename"] == "pasted-note.txt"

    graph = client.get("/api/graphs/current").json()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2
    assert all(edge["evidence"] for edge in graph["edges"])
    method_filter = client.get("/api/graphs/current", params={"entity_type": "Method"}).json()
    assert len(method_filter["nodes"]) == 3
    assert len(method_filter["edges"]) == 2
    with connect(client.app.state.settings) as connection:
        connection.execute(
            "INSERT INTO entities(id,canonical_name,normalized_name,entity_type,aliases_json,created_at) VALUES(?,?,?,?,?,?)",
            ("isolated-test-entity", "Aardvark Isolated Entity", "aardvark isolated entity", "Method", "[]", "now"),
        )
    bounded_graph = client.get("/api/graphs/current", params={"limit": 2}).json()
    assert len(bounded_graph["nodes"]) == 2
    assert len(bounded_graph["edges"]) == 1
    assert "isolated-test-entity" not in {node["id"] for node in bounded_graph["nodes"]}

    searched = client.post("/api/search", json={"query": "Evidence Linking Claim"}).json()
    assert searched["results"]
    assert searched["retrieval_mode"] == "lexical"

    answer = client.post("/api/qa", json={"question": "What does Evidence Linking connect?"}).json()
    assert answer["insufficient_evidence"] is True
    assert answer["citations"]

    fact_id = graph["edges"][0]["id"]
    assert client.patch(f"/api/facts/{fact_id}", json={"review_status": "approved"}).status_code == 200
    answer = client.post("/api/qa", json={"question": "Evidence Linking Claim"}).json()
    assert answer["insufficient_evidence"] is False
    assert answer["citations"]

    csv_response = client.get("/api/graphs/current/export?format=csv")
    json_response = client.get("/api/graphs/current/export?format=json")
    assert csv_response.status_code == 200 and "record_type" in csv_response.text
    assert json_response.status_code == 200 and len(json_response.json()["edges"]) == 2

    filtered = client.post("/api/graphs/current/query", json={"review_status": "approved", "limit": 50})
    assert filtered.status_code == 200
    assert all(edge["review_status"] == "approved" for edge in filtered.json()["edges"])
    assert client.get("/api/analytics").status_code == 200


def test_fact_correction_keeps_audit_and_entity_merge_can_be_undone(client):
    source = "The method Evidence Linking uses the method Source Linking. The method Evidence Linker uses the method Claim Linking."
    document = client.post("/api/documents/text", json={"filename": "methods.txt", "text": source}).json()
    facts = client.get("/api/facts", params={"document_id": document["id"], "limit": 100}).json()["items"]
    assert facts
    fact = facts[0]
    corrected = client.patch(f"/api/facts/{fact['id']}", json={
        "predicate": "RELATED_TO", "polarity": "affirmed", "review_status": "approved", "correction_note": "Reviewed against the cited source.",
    })
    assert corrected.status_code == 200
    assert corrected.json()["predicate"] == "RELATED_TO"
    assert corrected.json()["corrections"][0]["note"] == "Reviewed against the cited source."

    entities = client.get("/api/entities", params={"document_id": document["id"], "limit": 100}).json()["items"]
    left = next(item for item in entities if item["label"] == "Evidence Linking")
    right = next(item for item in entities if item["label"] == "Evidence Linker")
    merge = client.post("/api/entity-merges", json={"source_entity_id": right["id"], "target_entity_id": left["id"], "note": "Same method name."})
    assert merge.status_code == 201
    merged = client.get("/api/entities", params={"document_id": document["id"]}).json()["items"]
    assert not any(item["id"] == right["id"] for item in merged)
    assert "Evidence Linker" in next(item for item in merged if item["id"] == left["id"])["aliases"]

    undone = client.post(f"/api/entity-merges/{merge.json()['id']}/undo")
    assert undone.status_code == 200
    restored = client.get("/api/entities", params={"document_id": document["id"]}).json()["items"]
    assert any(item["id"] == right["id"] for item in restored)


def test_settings_hide_secrets_and_evaluation_does_not_use_synthetic_seed(client):
    settings = client.get("/api/settings").json()
    assert settings["graph_backend"] == "sqlite"
    assert "neo4j_password" not in json.dumps(settings).lower()
    assert "llm_api_key\":" not in json.dumps(settings).lower()
    evaluation = client.get("/api/evaluation").json()
    assert evaluation["available"] is False
    assert "Synthetic examples are not used" in evaluation["message"]


def test_annotation_evaluator_reports_metrics_for_a_real_test_split(client, tmp_path):
    source = "The method Alpha uses the dataset Beta."
    document = client.post("/api/documents/text", json={"filename": "eval.txt", "text": source}).json()
    start_alpha = source.index("Alpha")
    start_beta = source.index("Beta")
    annotation = {
        "document_id": document["id"], "split": "test",
        "entities": [
            {"text": "Alpha", "type": "Method", "start_offset": start_alpha, "end_offset": start_alpha + len("Alpha")},
            {"text": "Beta", "type": "Dataset", "start_offset": start_beta, "end_offset": start_beta + len("Beta")},
        ],
        "relations": [{"subject": "Alpha", "predicate": "USES", "object": "Beta", "evidence": source, "start_offset": 0, "end_offset": len(source)}],
    }
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(annotation) + "\n", encoding="utf-8")
    client.app.state.settings = replace(client.app.state.settings, annotations_path=path)
    report = client.get("/api/evaluation").json()
    assert report["available"] is True
    assert report["entity_metrics"]["all"]["f1"] == 1.0
    assert report["relation_metrics"]["all"]["f1"] == 1.0
    assert report["evidence_grounding_rate"] == 1.0


def test_optional_llm_output_requires_configured_entities_and_exact_evidence(client, monkeypatch):
    import app.nlp.llm_adapter as adapter

    settings = replace(client.app.state.settings, llm_provider="openai_compatible", llm_api_key="test-key")
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: {
        "entities": [
            {"text": "Alpha Method", "entity_type": "Method", "confidence": 0.8},
            {"text": "Beta Method", "entity_type": "Method", "confidence": 0.8},
        ],
        "facts": [
            {"subject": "Alpha Method", "predicate": "USES", "object": "Beta Method", "polarity": "affirmed", "evidence": "Alpha Method uses Beta Method.", "confidence": 0.75},
            {"subject": "Alpha Method", "predicate": "USES", "object": "Beta Method", "polarity": "affirmed", "evidence": "invented evidence", "confidence": 0.99},
        ],
    })
    chunks = [{"text": "Alpha Method uses Beta Method.", "start_offset": 100, "end_offset": 130,
               "location_type": "paragraph", "location_label": "Paragraph 4", "paragraph_number": 4}]
    mentions, facts = extract_llm(settings, chunks, load_schema())
    assert [mention.canonical_name for mention in mentions] == ["Alpha Method", "Beta Method"]
    assert len(facts) == 1
    assert facts[0].evidence_text == "Alpha Method uses Beta Method."
    assert facts[0].start_offset == 100


def test_gemini_native_json_request_and_settings_hide_api_key(client, monkeypatch):
    import app.nlp.llm_adapter as adapter

    secret = "gemini-test-secret"
    settings = replace(
        client.app.state.settings,
        llm_provider="gemini",
        llm_api_key=secret,
        llm_model="gemini-3.8-flash",
    )
    client.app.state.settings = settings
    expected = {"answer": "Grounded answer", "insufficient_evidence": False, "citation_indices": [0]}
    response_data = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(expected)}]}}]}
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(response_data).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(adapter, "urlopen", fake_urlopen)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    result = adapter._request(settings, "System guidance", "User content", "test_schema", schema)

    assert result == expected
    request, timeout = calls[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert request.get_header("X-goog-api-key") == secret
    assert secret not in request.full_url
    assert timeout == settings.llm_timeout_seconds
    assert payload["systemInstruction"]["parts"][0]["text"] == "System guidance"
    assert payload["generationConfig"]["responseFormat"]["text"]["mimeType"] == "APPLICATION_JSON"
    assert payload["generationConfig"]["responseFormat"]["text"]["schema"] == schema
    assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"

    public_settings = client.get("/api/settings").json()
    assert public_settings["llm_provider"] == "gemini"
    assert public_settings["llm_base_url"] == "Google Gemini API"
    assert public_settings["llm_api_key_configured"] is True
    assert secret not in json.dumps(public_settings)
