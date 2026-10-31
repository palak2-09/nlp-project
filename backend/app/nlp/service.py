from app.core.config import Settings
from app.nlp.baseline import extract_baseline
from app.nlp.repository import persist_extraction
from app.nlp.schema import load_schema


def extract_and_persist(
    settings: Settings,
    document_id: str,
    chunks: list[dict]
) -> tuple[int, int]:

    schema = load_schema()

    # 1. Run baseline extraction
    mentions, facts = extract_baseline(chunks, schema)

    print("\n========== EXTRACTION DIAGNOSTICS ==========")
    print("Chunks:", len(chunks))
    print("Baseline entities:", len(mentions))
    print("Baseline facts:", len(facts))
    print("LLM provider:", settings.llm_provider)
    print("LLM model:", settings.llm_model)

    # 2. Run Gemini extraction
    if settings.llm_provider != "none":
        from app.nlp.llm_adapter import extract_llm

        llm_mentions, llm_facts = extract_llm(
            settings, chunks, schema
        )

        print("Gemini entities:", len(llm_mentions))
        print("Gemini facts:", len(llm_facts))

        mentions.extend(llm_mentions)
        facts.extend(llm_facts)

    print("Combined entities before deduplication:", len(mentions))
    print("Combined facts before deduplication:", len(facts))

    # 3. Deduplicate mentions
    mention_map = {}

    for mention in mentions:
        key = (
            mention.chunk_index,
            mention.start_offset,
            mention.end_offset,
            mention.canonical_name.casefold(),
            mention.entity_type.casefold()
        )
        mention_map[key] = mention

    # 4. Deduplicate facts
    fact_map = {}

    for fact in facts:
        key = (
            fact.chunk_index,
            fact.start_offset,
            fact.end_offset,
            fact.subject_name.casefold(),
            fact.predicate,
            fact.object_name.casefold(),
            fact.polarity
        )
        fact_map.setdefault(key, fact)

    mentions = list(mention_map.values())
    facts = list(fact_map.values())

    # 5. Apply confidence threshold
    threshold = min(
        max(settings.minimum_extraction_confidence, 0.0),
        1.0
    )

    print("Confidence threshold:", threshold)

    mentions = [
        mention for mention in mentions
        if mention.confidence >= threshold
    ]

    facts = [
        fact for fact in facts
        if fact.confidence >= threshold
    ]

    print("Entities after filtering:", len(mentions))
    print("Facts after filtering:", len(facts))

    # 6. Persist
    result = persist_extraction(
        settings,
        document_id,
        mentions,
        facts
    )

    print("Persisted entities:", result[0])
    print("Persisted facts:", result[1])
    print("============================================\n")

    return result