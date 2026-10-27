# Evaluation plan and report

## Current report

No generalizable extraction metrics are published by default. `data/annotations/seed.jsonl` is a synthetic instructional row, and the evaluator intentionally does not use it. The dashboard shows precision/recall/F1 only when `PAPERGRAPH_ANNOTATIONS_PATH` points to a user-created JSONL test set.

The `/api/evaluation` service computes exact span-and-label micro precision, recall, and F1 for entities and relations. It groups predictions by extraction method, reports a source-evidence span grounding rate, and warns when an annotated document ID is not in the local processed collection. These results depend on the selected documents, annotation process, split, and exact matching rule.

Entity-resolution clustering accuracy is not computed yet. The report says so instead of fabricating a number. Add canonical cluster IDs and a pairwise matching evaluation before claiming resolution quality. Processing-time and human review statistics in `/api/analytics` are descriptive values from the local database, not model-quality metrics.

## Annotation format

Follow `data/annotations/README.md`. Use permitted representative sources, create train/development/test splits before tuning, and make offsets refer to the normalized extracted text from document preview. The evaluator uses the `test` split only.

## Study design

For a defensible project report:

1. Select and document source-use permissions and domain coverage.
2. Have a second annotator review a portion of entity, relation, polarity, evidence, and canonical-identity labels; report agreement and adjudication rules.
3. Freeze test documents before changing extraction patterns or model settings.
4. Compare the deterministic baseline and optional LLM structured extractor on the same test documents and exact matching rules.
5. Report precision, recall, F1, evidence grounding, invalid/unsupported facts, duplicate entities/facts, processing time, and human approval/correction rates.
6. Include qualitative examples of correct extraction, missed relations, wrong entity merges, and unsupported outputs.

The current service does not calculate inter-annotator agreement, invalid-fact rate, or pairwise cluster metrics. Add these only when a suitable annotated set exists; do not treat the current synthetic seed row as benchmark evidence.
