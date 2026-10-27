from collections import defaultdict
from dataclasses import dataclass
import re
import unicodedata

from app.nlp.schema import ExtractionSchema


EXTRACTION_METHOD = "rule_based"
MODEL_VERSION = "rule-baseline/0.1"

TOKEN = r"(?:[A-Z][a-z0-9]+|[A-Z]{2,})"
PROPER_PHRASE = re.compile(rf"\b{TOKEN}(?:[ \-]+{TOKEN}){{1,5}}\b")
ACRONYM = re.compile(r"\b[A-Z][A-Z0-9-]{1,9}\b")
SINGLE_AFTER_ARTICLE = re.compile(r"\b(?:a|an|the)\s+(?P<name>[A-Z][a-zA-Z0-9-]{2,})\b")
SINGLE_PROPER = re.compile(r"\b[A-Z][a-zA-Z0-9-]{2,}\b")
ACRONYM_DEFINITION = re.compile(
    rf"(?P<full>{TOKEN}(?:\s+{TOKEN}){{1,5}})\s*\((?P<short>[A-Z][A-Z0-9-]{{1,9}})\)"
)
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)|[^\n]+$")
STOP_NAMES = {"this", "that", "these", "those", "the", "in", "we", "it", "they", "our", "their"}

CONNECT_CUE = re.compile(r"\b(?:connects?|links?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class EntityMention:
    canonical_name: str
    entity_type: str
    surface_form: str
    chunk_index: int
    start_offset: int
    end_offset: int
    location_type: str
    location_label: str
    page_number: int | None
    slide_number: int | None
    paragraph_number: int | None
    section: str | None
    extraction_method: str = EXTRACTION_METHOD
    model_version: str = MODEL_VERSION
    confidence: float = 0.65


@dataclass(frozen=True)
class FactCandidate:
    subject_name: str
    subject_type: str
    predicate: str
    object_name: str
    object_type: str
    polarity: str
    evidence_text: str
    chunk_index: int
    start_offset: int
    end_offset: int
    location_type: str
    location_label: str
    page_number: int | None
    slide_number: int | None
    paragraph_number: int | None
    section: str | None
    extraction_method: str = EXTRACTION_METHOD
    model_version: str = MODEL_VERSION
    confidence: float = 0.70


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _infer_type(text: str, start: int, schema: ExtractionSchema) -> str:
    available = {label.casefold(): label for label in schema.entity_types}
    prefix = text[max(0, start - 80):start].casefold()
    cue_names = sorted(schema.entity_cues, key=len, reverse=True)
    for cue in cue_names:
        if re.search(rf"\b{re.escape(cue)}\s+(?:(?:called|named|known as)\s+)?$", prefix):
            label = schema.entity_cues[cue]
            if label.casefold() in available:
                return available[label.casefold()]
    return available.get("concept", schema.entity_types[0])


def _sentence_spans(text: str):
    for match in SENTENCE.finditer(text):
        raw = match.group(0)
        stripped = raw.strip()
        if not stripped:
            continue
        left_trim = len(raw) - len(raw.lstrip())
        yield match.start() + left_trim, match.start() + left_trim + len(stripped), stripped


def _definitions(chunks: list[dict]) -> dict[str, tuple[str, str]]:
    found: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for chunk in chunks:
        text = chunk["text"]
        for match in ACRONYM_DEFINITION.finditer(text):
            full = match.group("full").strip()
            short = match.group("short").strip()
            found[normalize_name(short)].add((normalize_name(full), full))
    # An ambiguous short form is deliberately left unresolved.
    return {alias: next(iter(values)) for alias, values in found.items() if len(values) == 1}


def _candidate_spans(text: str, schema: ExtractionSchema, aliases: dict[str, tuple[str, str]]):
    candidates: dict[tuple[int, int], tuple[str, str, float]] = {}
    explicit_aliases: set[tuple[int, int]] = set()

    for match in ACRONYM_DEFINITION.finditer(text):
        full_start, full_end = match.span("full")
        short_start, short_end = match.span("short")
        full = match.group("full").strip()
        full_type = _infer_type(text, full_start, schema)
        candidates[(full_start, full_end)] = (full, full_type, 0.9)
        candidates[(short_start, short_end)] = (match.group("short"), full_type, 0.9)
        explicit_aliases.add((short_start, short_end))

    for cue in sorted(schema.entity_cues, key=len, reverse=True):
        contextual_name = re.compile(
            rf"(?i:\b{re.escape(cue)}\b)\s+(?:(?i:called|named|known as)\s+)?(?P<name>{TOKEN}(?:\s+{TOKEN}){{0,4}})"
        )
        for match in contextual_name.finditer(text):
            start, end = match.span("name")
            value = match.group("name")
            candidates[(start, end)] = (value, _infer_type(text, start, schema), 0.8)

    for matcher in (PROPER_PHRASE, ACRONYM, SINGLE_AFTER_ARTICLE, SINGLE_PROPER):
        for match in matcher.finditer(text):
            if matcher is SINGLE_AFTER_ARTICLE:
                start, end = match.span("name")
                value = match.group("name")
            else:
                start, end = match.span()
                value = match.group(0)
            first_word = value.split()[0].casefold()
            if first_word in STOP_NAMES:
                continue
            candidates.setdefault((start, end), (value, _infer_type(text, start, schema), 0.65 if matcher is not ACRONYM else 0.55))

    # Do not emit embedded acronym fragments such as "AI" from "PaperGraph AI" as separate entities,
    # except when the abbreviation is explicitly defined in parentheses.
    spans = list(candidates)
    for span in spans:
        if span in explicit_aliases:
            continue
        if any(other != span and other[0] <= span[0] and other[1] >= span[1] for other in spans):
            candidates.pop(span, None)

    result = []
    for (start, end), (surface, entity_type, score) in sorted(candidates.items()):
        alias = aliases.get(normalize_name(surface))
        canonical = alias[1] if alias else surface
        if alias:
            # The definition's full form controls the canonical entity type when present in this chunk.
            for (full_start, full_end), (full_surface, full_type, _) in candidates.items():
                if normalize_name(full_surface) == alias[0]:
                    entity_type = full_type
                    break
        result.append((start, end, surface, canonical, entity_type, score))
    return result


def _mentions_for_chunk(chunk: dict, chunk_index: int, schema: ExtractionSchema, aliases: dict[str, tuple[str, str]]) -> list[EntityMention]:
    text = chunk["text"]
    mentions = []
    for start, end, surface, canonical, entity_type, score in _candidate_spans(text, schema, aliases):
        mentions.append(EntityMention(
            canonical_name=canonical,
            entity_type=entity_type,
            surface_form=surface,
            chunk_index=chunk_index,
            start_offset=chunk["start_offset"] + start,
            end_offset=chunk["start_offset"] + end,
            location_type=chunk["location_type"],
            location_label=chunk["location_label"],
            page_number=chunk.get("page_number"),
            slide_number=chunk.get("slide_number"),
            paragraph_number=chunk.get("paragraph_number"),
            section=chunk.get("section"),
            confidence=score,
        ))
    return mentions


def _fact_from_mentions(subject: EntityMention, obj: EntityMention, predicate: str, polarity: str, evidence: str, chunk_index: int, evidence_start: int, evidence_end: int) -> FactCandidate | None:
    if normalize_name(subject.canonical_name) == normalize_name(obj.canonical_name):
        return None
    return FactCandidate(
        subject_name=subject.canonical_name,
        subject_type=subject.entity_type,
        predicate=predicate,
        object_name=obj.canonical_name,
        object_type=obj.entity_type,
        polarity=polarity,
        evidence_text=evidence,
        chunk_index=chunk_index,
        start_offset=evidence_start,
        end_offset=evidence_end,
        location_type=subject.location_type,
        location_label=subject.location_label,
        page_number=subject.page_number,
        slide_number=subject.slide_number,
        paragraph_number=subject.paragraph_number,
        section=subject.section,
    )


def _facts_for_sentence(
    sentence: str,
    sentence_start: int,
    sentence_end: int,
    chunk: dict,
    chunk_index: int,
    mentions: list[EntityMention],
    relation_cues: list[tuple[str, re.Pattern]],
) -> list[FactCandidate]:
    chunk_start = chunk["start_offset"]
    sentence_mentions = [
        (mention, mention.start_offset - chunk_start - sentence_start, mention.end_offset - chunk_start - sentence_start)
        for mention in mentions
        if mention.start_offset >= chunk_start + sentence_start and mention.end_offset <= chunk_start + sentence_end
    ]
    sentence_mentions.sort(key=lambda item: item[1])
    facts = []
    base_evidence_start = chunk_start + sentence_start
    base_evidence_end = chunk_start + sentence_end

    for cue in CONNECT_CUE.finditer(sentence):
        before = [item for item in sentence_mentions if item[2] <= cue.start()]
        after = [item for item in sentence_mentions if item[1] >= cue.end()]
        if not before or not after:
            continue
        subject = before[-1][0]
        prefix = sentence[max(0, cue.start() - 24):cue.start()].casefold()
        polarity = "negated" if re.search(r"\b(?:not|never|no|cannot|can't|doesn't|didn't|isn't|aren't|wasn't|weren't)\b", prefix) and not re.search(r"\bnot\s+only\b", prefix) else "affirmed"
        first_obj = after[0][0]
        first = _fact_from_mentions(subject, first_obj, "RELATED_TO", polarity, sentence, chunk_index, base_evidence_start, base_evidence_end)
        if first:
            facts.append(first)
        if len(after) > 1:
            second_obj = after[1][0]
            between = sentence[first_obj.end_offset - chunk_start - sentence_start:second_obj.start_offset - chunk_start - sentence_start]
            if re.search(r"\bto\b", between, re.IGNORECASE):
                second = _fact_from_mentions(subject, second_obj, "RELATED_TO", polarity, sentence, chunk_index, base_evidence_start, base_evidence_end)
                if second:
                    facts.append(second)

    for predicate, pattern in relation_cues:
        for cue in pattern.finditer(sentence):
            before = [item for item in sentence_mentions if item[2] <= cue.start()]
            after = [item for item in sentence_mentions if item[1] >= cue.end()]
            if not before or not after:
                continue
            prefix = sentence[max(0, cue.start() - 24):cue.start()].casefold()
            polarity = "negated" if re.search(r"\b(?:not|never|no|cannot|can't|doesn't|didn't|isn't|aren't|wasn't|weren't)\b", prefix) and not re.search(r"\bnot\s+only\b", prefix) else "affirmed"
            fact = _fact_from_mentions(before[-1][0], after[0][0], predicate, polarity, sentence, chunk_index, base_evidence_start, base_evidence_end)
            if fact:
                facts.append(fact)

    unique = {}
    for fact in facts:
        key = (normalize_name(fact.subject_name), fact.predicate, normalize_name(fact.object_name), fact.polarity, fact.start_offset, fact.end_offset)
        unique[key] = fact
    return list(unique.values())


def extract_baseline(chunks: list[dict], schema: ExtractionSchema) -> tuple[list[EntityMention], list[FactCandidate]]:
    aliases = _definitions(chunks)
    all_mentions: list[EntityMention] = []
    all_facts: list[FactCandidate] = []
    valid_entity_types = {label.casefold(): label for label in schema.entity_types}
    valid_predicates = {label for label in schema.relationship_types}
    relation_cues = [
        (predicate, re.compile(pattern, re.IGNORECASE))
        for predicate, patterns in schema.relationship_patterns.items()
        for pattern in patterns
    ]

    for chunk_index, chunk in enumerate(chunks):
        mentions = _mentions_for_chunk(chunk, chunk_index, schema, aliases)
        all_mentions.extend(mentions)
        for sentence_start, sentence_end, sentence in _sentence_spans(chunk["text"]):
            for fact in _facts_for_sentence(sentence, sentence_start, sentence_end, chunk, chunk_index, mentions, relation_cues):
                if fact.predicate in valid_predicates and fact.subject_type.casefold() in valid_entity_types and fact.object_type.casefold() in valid_entity_types:
                    all_facts.append(fact)

    unique_mentions = {}
    for mention in all_mentions:
        key = (mention.chunk_index, mention.start_offset, mention.end_offset, normalize_name(mention.canonical_name), mention.entity_type)
        unique_mentions[key] = mention
    unique_facts = {}
    for fact in all_facts:
        key = (normalize_name(fact.subject_name), fact.predicate, normalize_name(fact.object_name), fact.polarity, fact.start_offset, fact.end_offset)
        unique_facts[key] = fact
    return list(unique_mentions.values()), list(unique_facts.values())
