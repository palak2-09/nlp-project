from conftest import upload_txt
from app.nlp.baseline import extract_baseline
from app.nlp.schema import ExtractionSchema


def test_upload_persists_evidence_linked_entities_and_facts(client):
    source = "The method Evidence Linking connects a Claim to a Source Passage."
    uploaded = upload_txt(client, contents=source).json()
    document = client.get(f"/api/documents/{uploaded['id']}").json()

    assert document["entity_count"] == 3
    assert document["fact_count"] == 2
    entity_response = client.get("/api/entities", params={"document_id": document["id"]})
    assert entity_response.status_code == 200
    entities = entity_response.json()["items"]
    assert {item["label"] for item in entities} == {"Evidence Linking", "Claim", "Source Passage"}

    fact_response = client.get("/api/facts", params={"document_id": document["id"], "review_status": "pending"})
    assert fact_response.status_code == 200
    facts = fact_response.json()["items"]
    assert len(facts) == 2
    fact = next(item for item in facts if item["object"]["label"] == "Claim")
    assert fact["subject"]["label"] == "Evidence Linking"
    assert fact["predicate"] == "RELATED_TO"
    assert fact["review_status"] == "pending"
    assert fact["extraction_method"] == "rule_based"
    assert fact["model_version"] == "rule-baseline/0.1"
    evidence = fact["evidence"][0]
    assert evidence["evidence_text"] == source
    assert source[evidence["start_offset"]:evidence["end_offset"]] == evidence["evidence_text"]
    assert evidence["location_label"] == "Paragraph 1"

    stats = client.get("/api/stats").json()
    assert stats["entity_count"] == 3
    assert stats["fact_count"] == 2
    assert stats["pending_fact_count"] == 2


def test_fact_review_and_document_cleanup_preserve_other_evidence(client):
    first_text = "The method Evidence Linking connects a Claim to a Source Passage."
    second_text = "The method Evidence Linking connects a Claim to a Source Passage. This second source adds context."
    first = upload_txt(client, "source-one.txt", first_text).json()
    second = upload_txt(client, "source-two.txt", second_text).json()

    response = client.get("/api/facts", params={"review_status": "pending"}).json()
    claim_fact = next(item for item in response["items"] if item["object"]["label"] == "Claim")
    assert len(claim_fact["evidence"]) == 2

    approved = client.patch(f"/api/facts/{claim_fact['id']}", json={"review_status": "approved"})
    assert approved.status_code == 200
    assert approved.json()["review_status"] == "approved"
    assert client.get("/api/facts", params={"review_status": "approved"}).json()["total"] == 1

    assert client.delete(f"/api/documents/{first['id']}").status_code == 204
    retained = client.get(f"/api/facts/{claim_fact['id']}").json()
    assert retained["review_status"] == "approved"
    assert [evidence["document_id"] for evidence in retained["evidence"]] == [second["id"]]


def test_explicit_acronym_alias_resolves_across_documents(client):
    upload_txt(client, "definition.txt", "Natural Language Processing (NLP) is a broad field.")
    other = upload_txt(client, "abbreviation.txt", "NLP matters in modern research.").json()

    result = client.get("/api/entities", params={"document_id": other["id"], "q": "NLP"}).json()
    assert result["total"] == 1
    assert result["items"][0]["label"] == "Natural Language Processing"
    assert "NLP" in result["items"][0]["aliases"]
    entity = client.get(f"/api/entities/{result['items'][0]['id']}").json()
    assert entity["mention_count"] == 3


def test_negative_relation_is_preserved_as_negated(client):
    positive = upload_txt(client, "positive.txt", "Evidence Linking connects a Claim to a Source Passage.").json()
    negative = upload_txt(client, "negative.txt", "Evidence Linking does not connect a Claim to a Source Passage.").json()

    related = client.get("/api/facts", params={"predicate": "RELATED_TO"}).json()["items"]
    claim_facts = [item for item in related if item["object"]["label"] == "Claim"]
    assert {(item["polarity"], tuple(ev["document_id"] for ev in item["evidence"])) for item in claim_facts} == {
        ("affirmed", (positive["id"],)),
        ("negated", (negative["id"],)),
    }


def test_extraction_schema_can_add_domain_labels_and_patterns():
    schema = ExtractionSchema(
        name="custom-domain",
        entity_types=["Concept", "Technique"],
        relationship_types=["USES"],
        entity_cues={"tool": "Technique"},
        relationship_patterns={"USES": [r"\buses?\b"]},
    )
    text = "The tool Alpha uses Beta."
    chunks = [{
        "text": text, "start_offset": 0, "end_offset": len(text), "location_type": "paragraph",
        "location_label": "Paragraph 1", "page_number": None, "slide_number": None,
        "paragraph_number": 1, "section": None,
    }]

    mentions, facts = extract_baseline(chunks, schema)
    assert {(mention.canonical_name, mention.entity_type) for mention in mentions} == {
        ("Alpha", "Technique"), ("Beta", "Concept"),
    }
    assert [(fact.subject_name, fact.predicate, fact.object_name) for fact in facts] == [("Alpha", "USES", "Beta")]


def test_reprocessing_is_idempotent(client):
    source = "The method Evidence Linking connects a Claim to a Source Passage."
    uploaded = upload_txt(client, contents=source).json()
    first = client.get(f"/api/documents/{uploaded['id']}").json()
    first_entity_ids = {item["id"] for item in client.get("/api/entities", params={"document_id": uploaded["id"]}).json()["items"]}
    first_facts = client.get("/api/facts", params={"document_id": uploaded["id"]}).json()["items"]

    queued = client.post(f"/api/documents/{uploaded['id']}/extract")
    assert queued.status_code == 202
    second = client.get(f"/api/documents/{uploaded['id']}").json()
    second_entity_ids = {item["id"] for item in client.get("/api/entities", params={"document_id": uploaded["id"]}).json()["items"]}
    second_facts = client.get("/api/facts", params={"document_id": uploaded["id"]}).json()["items"]

    assert second["entity_count"] == first["entity_count"] == 3
    assert second["fact_count"] == first["fact_count"] == 2
    assert second_entity_ids == first_entity_ids
    assert {fact["id"] for fact in second_facts} == {fact["id"] for fact in first_facts}
    assert all(len(fact["evidence"]) == 1 for fact in second_facts)
