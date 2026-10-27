# Architecture and data model

## Runtime flow

```text
React/Vite UI → FastAPI → validation and local source storage
                       → persistent SQLite metadata/job/provenance store
                       → PDF/DOCX/PPTX/text adapters → normalized source chunks
                       → rule baseline → optional structured LLM extraction
                       → evidence validation → facts awaiting human review
                       → SQLite graph projection → optional idempotent Neo4j mirror
                       → search, citations, graph UI, JSON/CSV export, analytics
```

SQLite is the application source of truth. The graph explorer, search, fact review, and exports read its evidence-preserving graph projection. When `PAPERGRAPH_GRAPH_BACKEND=neo4j`, the service mirrors each document, chunks, mentions, canonical entities, facts, and chunk-to-fact evidence links to Neo4j with unique constraints and parameterized Cypher. Startup syncs stored documents to the graph. This keeps local development runnable without a Neo4j service while Docker Compose exercises the graph adapter.

## Tables and nodes

SQLite tables:

| Table | Purpose |
|---|---|
| `documents` | Safe document ID, original display name, type/size/hash, lifecycle, extraction counts |
| `jobs` | Status, stage, retry count, timestamps, and failure message |
| `entities` | Canonical display name, normalized name, type, aliases |
| `entity_mentions` | Surface form, entity/document/chunk link, offsets and source location |
| `facts` | Subject, predicate, object, polarity, extraction method/model, heuristic score, review status |
| `fact_evidence` | One source passage with document/chunk offsets and location for a fact |
| `fact_corrections` | Before/after snapshots and note for human fact corrections or decisions |
| `entity_merges` | Reversible source/target merge snapshots and note |

The Neo4j mirror uses `Document`, `Chunk`, `Mention`, `Entity`, and `Fact` nodes. It connects them with `HAS_CHUNK`, `CONTAINS_MENTION`, `REFERS_TO`, `SUPPORTS`, `SUBJECT`, and `OBJECT`. `SUPPORTS` carries evidence IDs, text spans, location, extractor/model, and score. Repeated facts can have multiple supporting evidence links; negative and positive claims remain distinct.

## Processing and trust boundaries

- The API hashes and validates uploads before assigning a UUID path. Duplicate content returns the existing source.
- Format adapters keep paragraph/page/slide location and normalized source offsets. Text and Markdown use UTF-8; tables and bullets are extracted as text.
- Baseline extraction only emits relations linked to extracted entity mentions and a configured pattern. The optional LLM adapter emits strict JSON Schema output, then validates labels, entity spans, exact evidence quotes, and references before persistence. Instructions contained in uploaded text are treated as data and ignored.
- Model output never auto-approves facts. The graph and assistant preserve review state; Q&A answers from approved facts when possible, otherwise shows relevant source text and states when support is insufficient.
- No caller-supplied Cypher is executed. The graph query endpoint accepts typed, bounded filters only.
- Secret values are read from `.env` at process start and never included in the settings response. The app is unauthenticated and intended for localhost development.

## API surface

See the generated OpenAPI document at `/docs`. Main route groups are `/api/documents`, `/api/jobs`, `/api/entities`, `/api/facts`, `/api/graphs/current`, `/api/search`, `/api/qa`, `/api/analytics`, `/api/evaluation`, `/api/settings`, and `/api/entity-merges`.

## Reliability boundaries

Processing runs in FastAPI `BackgroundTasks` and is not crash-resumable. SQLite job records remain available for inspection and retry. Neo4j sync uses stable IDs and `MERGE`; syncing a document replaces document-local chunks/mentions, retains shared graph facts with other evidence, and removes orphan graph nodes. If Neo4j is unavailable, the graph operation is reported instead of silently claiming it succeeded.
