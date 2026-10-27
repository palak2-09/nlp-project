from app.core.config import Settings
from app.nlp.baseline import extract_baseline
from app.nlp.repository import persist_extraction
from app.nlp.schema import load_schema


def extract_and_persist(settings: Settings, document_id: str, chunks: list[dict]) -> tuple[int, int]:
    """Run the deterministic baseline and persist evidence-linked mentions and facts."""
    schema = load_schema()
    mentions, facts = extract_baseline(chunks, schema)
    if settings.llm_provider != "none":
        from app.nlp.llm_adapter import extract_llm
        llm_mentions, llm_facts = extract_llm(settings, chunks, schema)
        mentions.extend(llm_mentions)
        facts.extend(llm_facts)
    mention_map = {}
    for mention in mentions:
        key = (mention.chunk_index, mention.start_offset, mention.end_offset, mention.canonical_name.casefold(), mention.entity_type.casefold())
        mention_map[key] = mention
    fact_map = {}
    for fact in facts:
        key = (fact.chunk_index, fact.start_offset, fact.end_offset, fact.subject_name.casefold(), fact.predicate, fact.object_name.casefold(), fact.polarity)
        fact_map.setdefault(key, fact)
    mentions, facts = list(mention_map.values()), list(fact_map.values())
    threshold = min(max(settings.minimum_extraction_confidence, 0.0), 1.0)
    mentions = [mention for mention in mentions if mention.confidence >= threshold]
    facts = [fact for fact in facts if fact.confidence >= threshold]
    return persist_extraction(settings, document_id, mentions, facts)
