# PaperGraph AI

PaperGraph AI turns document collections and pasted notes into an evidence-backed knowledge graph. The local rule-based pipeline works without model credentials. Optional Sentence Transformers add semantic similarity/search, optional Gemini or OpenAI-compatible structured output adds model-assisted extraction and answer generation, and Docker Compose starts Neo4j for graph persistence.

## What works

- Upload one or many PDF, DOCX, PPTX, TXT, and Markdown files, or paste text. Upload validation checks extension, declared MIME, content, size, UTF-8, and duplicate SHA-256 hashes.
- Extract selectable text with page/slide/paragraph/table context, source offsets, and configurable chunks.
- Extract schema-configurable entities, relations, polarity, evidence spans, and heuristic confidence. Every candidate fact stays pending until a person reviews it.
- Resolve entity names with exact normalization and explicit aliases. Similarity suggestions use string matching and can optionally use embeddings. Merges require a human choice and can be undone.
- Browse, search, and filter the graph; expand neighbors; find shortest paths; inspect evidence; export JSON or CSV.
- Search collection text and graph facts. Ask questions and get approved-fact answers or relevant cited text with an explicit insufficient-evidence response. Optional LLM answers can only cite retrieved passages supplied to the model.
- Correct, approve, reject, or delete facts. Corrections keep an audit record and the original source evidence.
- View collection distributions, processing durations, alias counts, review outcomes, and evaluation metrics when a labeled test set exists.
- Retry failed jobs and re-run extraction. SQLite metadata, jobs, mentions, facts, corrections, and evidence persist under `data/`.

The Neo4j adapter mirrors `Document`, `Chunk`, `Mention`, `Entity`, and `Fact` nodes with provenance relationships. SQLite remains the application metadata and provenance source of truth; graph queries are served from its evidence-preserving projection. Neo4j sync is idempotent and runs on API startup in Docker mode.

## Configure `.env`

Copy [`.env.example`](.env.example) to `.env` in the project root and fill the settings you want. The defaults use local SQLite, the deterministic baseline, lexical search, and no external model provider; those defaults need no API key and no Neo4j password.

Use the **Settings** page to see which options are active. It never returns secret values. Restart the API after changing `.env`.

### Optional settings

- **Neo4j:** For Aura or another external server, set `PAPERGRAPH_GRAPH_BACKEND=neo4j` and copy the URI, username, password, and database exactly from its connection details. For Docker Compose, the stack selects its own local Neo4j service and credentials; it does not use the Aura URI in `.env`.
- **Semantic search and similarity suggestions:** set `PAPERGRAPH_ENABLE_EMBEDDINGS=true`, install `backend/requirements-ml.txt` (or build with `PAPERGRAPH_ENABLE_EMBEDDINGS=true`), and allow the configured model to download once. Set the two entity thresholds in `.env`; the system only suggests matches and never merges automatically.
- **Model-assisted extraction and answers:** for Gemini, set `PAPERGRAPH_LLM_PROVIDER=gemini`, put your Google AI Studio key in `PAPERGRAPH_LLM_API_KEY`, and set `PAPERGRAPH_LLM_MODEL` (the template uses `gemini-3.8-flash`). Gemini uses its native `generateContent` API and JSON-schema response format. `PAPERGRAPH_LLM_BASE_URL` is only used with `openai_compatible`; that provider uses the Responses API with strict JSON Schema output. Both providers use request timeouts, bounded input, chunk limits, retries, evidence checks, and citation validation. Documents and questions sent to a configured provider leave the local machine; leave the provider as `none` for offline processing. See Google's [Gemini structured output guide](https://ai.google.dev/gemini-api/docs/generate-content/structured-output) and [model list](https://ai.google.dev/gemini-api/docs/models), and OpenAI's [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
- **Custom labels and rules:** copy `backend/app/nlp/schema.json`, edit its entity/relation labels and patterns, and set `PAPERGRAPH_SCHEMA_PATH` to the file.
- **Quality evaluation:** add a permitted annotated test set following `data/annotations/README.md`, then point `PAPERGRAPH_ANNOTATIONS_PATH` at it. The included seed example is synthetic and is excluded from metrics.

## Run on Windows (SQLite, no model credentials)

In PowerShell from the project root, create `.env` from the template only if you do not already have one, then edit its values:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Set-Location backend
python -m uvicorn app.main:app --reload
```

Open a second terminal in the project root:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The API and interactive API docs are at `http://127.0.0.1:8000` and `http://127.0.0.1:8000/docs`.

## Run with Docker and Neo4j

1. Copy `.env.example` to `.env`.
2. Set `NEO4J_PASSWORD` to a strong local password. For the optional hosted model, fill its provider fields too.
3. Run `docker compose up --build` from the project root.
4. Open `http://localhost:8080`. Neo4j Browser is available at `http://localhost:7474`.

The Neo4j data volume is named `neo4j_data`; document files, extracted text, and SQLite stay under the ignored project `data/` folder. To persist data, keep those locations and the Docker volume. To stop the stack, press Ctrl+C or run `docker compose down`; this keeps data. `docker compose down -v` removes the Neo4j volume.

## Linux/macOS

```bash
test -f .env || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend && python -m uvicorn app.main:app --reload
```

In another terminal: `cd frontend && npm install && npm run dev`.

## Main API

FastAPI exposes OpenAPI at `/docs`. Core endpoints include:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/documents` | Validate, store, and queue an uploaded document |
| `POST` | `/api/documents/text` | Add pasted text as a source document |
| `GET` | `/api/documents`, `/api/jobs` | Browse source files and processing jobs |
| `POST` | `/api/documents/{id}/extract`, `/api/jobs/{id}/retry` | Reprocess or retry a source |
| `GET` | `/api/entities`, `/api/facts` | Filter and page through extracted data |
| `PATCH` | `/api/facts/{id}` | Review or correct a fact with an audit record |
| `GET` | `/api/graphs/current` | Read the filtered graph projection |
| `GET` | `/api/graphs/current/neighbors/{entity_id}` | Expand a bounded neighborhood |
| `GET` | `/api/graphs/current/shortest-path` | Find a bounded shortest path |
| `GET` | `/api/graphs/current/export?format=json\|csv` | Export graph nodes, facts, and evidence |
| `POST` | `/api/search`, `/api/qa` | Search and ask evidence-grounded questions |
| `GET` | `/api/analytics`, `/api/evaluation` | Collection analytics and optional labeled-set metrics |
| `GET` | `/api/settings`, `/api/schema` | Inspect safe configuration and the extraction schema |
| `GET`, `POST` | `/api/entity-resolution/candidates`, `/api/entity-merges` | Review possible duplicate entities and merge manually |
| `POST` | `/api/entity-merges/{id}/undo` | Restore an audited entity merge |

## Testing

```powershell
Set-Location backend
python -m pytest
Set-Location ..\frontend
npm test
npm run build
```

The tests cover document parsing and validation, pasted text, extraction provenance, cross-document evidence, review/correction, entity merge/undo, graph filtering/export, search, grounded QA, and frontend navigation. They do not call external model services or verify a live Neo4j server. Use `docker compose up --build` to run the optional database integration locally.

## Scope and limitations

- Baseline extraction is rule-based and has limited recall. Scores are heuristic, not calibrated probabilities. The optional provider can improve extraction but still requires human review.
- OCR, scanned-PDF processing, CSV/XLSX ingestion, authentication, and multi-user access controls are not implemented. Do not expose this unauthenticated development app to a public network.
- Background processing uses FastAPI in-process tasks. Job records persist, but active work does not resume automatically after a process crash; inspect and retry failed/stale jobs after restart.
- Semantic model downloads require network access and disk space. The selected LLM provider may bill by usage and receives document text only when explicitly enabled.
- The PDF parser PyMuPDF is offered under AGPL or a commercial license. Review its [license terms](https://pymupdf.readthedocs.io/en/latest/about.html) before redistribution. This repository does not select a license for PaperGraph AI.
