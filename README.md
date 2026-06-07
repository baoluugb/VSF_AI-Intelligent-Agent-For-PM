# VSF AI — Intelligent Agent for Project Management

An end-to-end AI agent that ingests Jira, Confluence, and meeting-notes data, detects project risks, and generates a daily cited report — all in one command.

> **Live demo:** `./run_agent.sh` rebuilds the dual knowledge store, runs the Concern Engine (242 concerns across 4 risk types), and calls a grounded ReAct agent that produces `output/report.md` — a cited report that **leads with a "Priority Actions Today" block** (the highest-severity, decision-ready items) and a one-line risk-count summary. Output language follows `REPORT_LANG` (default Vietnamese).

---

## Project Layout

```
VSF_AI-Intelligent-Agent-For-PM/
├── AI_Project_Intelligence_Agent_Plan.md   # Master plan (v3.0)
├── TECH_REPORT.md                          # Architecture, benchmarks, decisions
├── project_status_audit.md                 # Audit of plan vs. implementation
├── config.py                               # Thresholds + OpenAI settings
├── pyproject.toml                          # Poetry deps + black/flake8 config
├── run_agent.sh                            # ← One-command demo entry point
├── .flake8
├── data/
│   ├── confluence/
│   │   └── confluence_synthetic.json       # 217 synthetic pages
│   ├── jira/
│   │   ├── jira_synthetic_AIP.json         # 1 000 issues (144 labelled anomalies)
│   │   ├── jira_field_information.json
│   │   ├── jira_issuetype_information.json
│   │   └── ...                             # additional Jira metadata
│   └── meeting_notes/
│       └── meeting_notes.json              # 5 meetings with action items
├── src/
│   ├── run_agent.py                        # Week-6 orchestrator (called by run_agent.sh)
│   ├── agents/
│   │   ├── concern_engine.py               # 4 risk rules + severity scoring
│   │   ├── report_agent.py                 # ReAct loop + citation enforcement
│   │   ├── report_pipeline.py              # Shared grounded-report helper (CLI + MCP server)
│   │   └── tools.py                        # OpenAI tool schemas + dispatch
│   ├── guardrail/
│   │   └── sanitizer.py                    # Input injection filter + output secret redaction
│   ├── mcp/
│   │   └── server.py                       # FastAPI server: /ingest /report /concerns (X-API-Key auth)
│   ├── ingestion/
│   │   ├── run_pipeline.py                 # Ingestion orchestrator (CLI)
│   │   ├── confluence_connector.py
│   │   ├── jira_connector.py
│   │   ├── meeting_notes_connector.py
│   │   └── entity_extractor.py
│   └── storage/
│       ├── init_db.py                      # SQLite schema (5 tables + 3 indexes)
│       ├── sqlite_store.py                 # Entity upsert, snapshots, diff, audit log
│       └── chroma_store.py                 # ChromaDB (3 collections)
├── tests/
│   ├── conftest.py
│   ├── test_concern_engine.py              # Precision 0.92 / Recall 1.00
│   ├── test_mcp_server.py                  # FastAPI auth + /ingest /report /concerns (mocked)
│   ├── test_guardrail.py
│   ├── test_run_pipeline.py
│   ├── test_report_agent.py
│   ├── test_chunking.py
│   ├── test_confluence_connector.py
│   ├── test_jira_connector.py
│   ├── test_entity_extractor.py
│   ├── test_ingestion_integration.py
│   └── test_meeting_notes_connector.py
└── output/                                 # Generated at runtime; gitignored
    ├── report.md                           # Daily cited report
    └── concerns.json                       # Structured risk objects
```

> **Generated at runtime (gitignored):** `data/vault.db`, `data/chroma/`, `output/report.md`, `output/concerns.json`.  
> A fresh clone rebuilds everything from scratch with `./run_agent.sh`.

---

## Quick Start

### Prerequisites

- Python ≥ 3.10
- An OpenAI-compatible API key (set in `.env`)

### 1. Install dependencies

Use a virtualenv or conda env, then install with **pip** (recommended — matches
how the project is run and developed):

```bash
python -m venv .venv && source .venv/bin/activate   # or: conda create -n vsf python=3.11 && conda activate vsf
pip install -r requirements.txt
```

> **Poetry (optional):** a `pyproject.toml` is also provided, so `poetry install`
> works if you prefer Poetry. In that case prefix the commands below with
> `poetry run`. With a pip/conda env activated, run them directly (no prefix).

### 2. Configure environment

Copy the example and fill in your credentials:

```bash
cp .env.example .env   # then edit .env with your key
```

Minimum required variables (see [.env.example](.env.example) for the full list):

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini             # or any chat model your key supports
# OPENAI_BASE_URL=https://ckey.vn/v1 # optional; omit to use api.openai.com
MCP_API_KEY=...                      # required only for the MCP server
```

### 3. Run the full demo (one command)

```bash
./run_agent.sh
```

This will:
1. Reset and re-initialise the SQLite + ChromaDB stores
2. Ingest all 1 222 documents (Jira / Confluence / Meeting Notes)
3. Run the Concern Engine and write `output/concerns.json`
4. Run the grounded ReAct Report Agent and write `output/report.md`

> **Windows users:** run `poetry run python src/run_agent.py` directly (the shell script targets bash/WSL).

### 4. Ingestion only

```bash
poetry run python src/ingestion/run_pipeline.py
```

### 5. Concern Engine only

```bash
poetry run python -m src.agents.concern_engine --date 2025-05-30
```

> Use `--date YYYY-MM-DD` because the synthetic data is mid-2025.

### 6. Report Agent only

```bash
poetry run python -m src.agents.report_agent --date 2025-05-30 --query "daily status"
```

### 7. MCP server (FastAPI front-end)

Set `MCP_API_KEY` in `.env` (any random string — e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`), then:

```bash
poetry run python src/mcp/server.py
# Swagger UI: http://localhost:8000/docs
```

Every endpoint requires an `X-API-Key` header matching `MCP_API_KEY`:

```bash
curl -X POST "http://localhost:8000/ingest"            -H "X-API-Key: $MCP_API_KEY"
curl    "http://localhost:8000/report?date=2025-05-30" -H "X-API-Key: $MCP_API_KEY"
curl    "http://localhost:8000/concerns?min_sev=3"      -H "X-API-Key: $MCP_API_KEY"
```

`/ingest` reuses the guardrail-wired ingestion pipeline (`InputSanitizer` screens text before indexing); `/report` and `/concerns` ground their output in the same Concern Engine + `OutputSanitizer`-redacted Report Agent used by `run_agent.sh`.

---

## Running Tests

```bash
pytest          # or: poetry run pytest
```

Expected result: **95 passed** (the MCP-server tests self-skip if `fastapi`
isn't installed, rather than failing collection).

To also run the 17 in-file guardrail tests:

```bash
pytest src/guardrail/sanitizer.py
```

---

## Architecture Overview

```
Data Sources (JSON files)
        │
        ▼
┌─────────────────────────────────────────┐
│          Ingestion Pipeline             │
│  JiraConnector → ─────────────────────┐ │
│  ConfluenceConnector → EntityExtractor│ │
│  MeetingNotesConnector → ─────────────┘ │
└──────────────┬──────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
  SQLiteStore       ChromaDB
  (entities,       (3 collections:
  snapshots,        confluence_chunks,
  backlinks,        meeting_chunks,
  audit_log)        jira_descriptions)
       │                │
       └───────┬────────┘
               ▼
  ┌────────────────────────┐
  │     Concern Engine     │  4 rules: stalled · deadline_risk
  │  (rule-based, SQL)     │           blocker · cross_source_conflict
  └────────────┬───────────┘
               │  concerns.json
               ▼
  ┌────────────────────────┐
  │     Report Agent       │  ReAct loop (OpenAI SDK)
  │  (ReAct + citations)   │  → grounded, cited Markdown report
  └────────────┬───────────┘
               │
   ┌───────────┴──────────┐
   ▼                      ▼
output/report.md    output/concerns.json
```

Guardrails ([sanitizer.py](src/guardrail/sanitizer.py)) wrap the agent boundary: `InputSanitizer` strips prompt-injection attempts and writes to the `audit_log` table; `OutputSanitizer` redacts secrets (`sk-…`, `Bearer …`, PEM keys).

---

## Configuration Reference

All tunables live in [config.py](config.py) and are overridable via `.env`:

| Variable              | Default        | Description                                       |
| --------------------- | -------------- | ------------------------------------------------- |
| `STALLED_DAYS`        | 3              | Days without update before a task is "stalled"     |
| `DEADLINE_RISK_DAYS`  | 2              | Window (± days) around due-date for deadline risk  |
| `BLOCKER_OPEN_DAYS`   | 2              | Days a blocker must be open to trigger an alert    |
| `CONFLICT_WINDOW_H`   | 48             | Hours for the cross-source conflict window         |
| `CHRONIC_STALLED_DAYS`| 30             | Idle days above which an unlabelled stalled task is "chronic" (kept but de-prioritised) |
| `MAX_AGENT_ITERATIONS`| 5              | ReAct loop iteration cap                           |
| `REPORT_LANG`         | `vi`           | Report + risk-explanation language (`vi` / `en`)   |
| `JIRA_BASE_URL`       | _(empty)_      | If set, Jira citations like `[FLINK-40]` become clickable links |
| `OPENAI_MODEL`        | `gpt-4o-mini`  | Model name (overridable in `.env`)                 |
| `OPENAI_BASE_URL`     | _(empty)_      | API base URL; empty → `api.openai.com` (set for a proxy) |
| `OPENAI_API_KEY`      | —              | API key (set in `.env`, **never commit**)          |

---

## Synthetic Data

All data lives under `data/` and is version-controlled (JSON only; `vault.db` and `data/chroma/` are gitignored):

| Source        | File                                    | Size                                      |
| ------------- | --------------------------------------- | ----------------------------------------- |
| Jira          | `data/jira/jira_synthetic_AIP.json`     | 1 000 issues (144 anomalies, 4 types × 36)|
| Confluence    | `data/confluence/confluence_synthetic.json` | 217 pages with linked Jira epics      |
| Meeting notes | `data/meeting_notes/meeting_notes.json` | 5 meetings with action items + ground truth|

---

## Project Status

| Phase                          | Completion |
| ------------------------------ | ---------- |
| Week 1 — Design & Data         | **100%**   |
| Week 2 — Ingestion & KB        | **100%**   |
| Week 3 — Report Agent          | **~100%**  |
| Week 4 — Concern Engine        | **~90%**   |
| Week 5 — MCP & Guardrails      | **100%**   |
| Week 6 — Packaging & Demo      | **~85%**   |

The **MCP server** (`src/mcp/server.py`) is implemented and live-verified — a FastAPI front-end exposing `/ingest`, `/report`, and `/concerns`, gated by `X-API-Key` auth and wired into the existing guardrails (see [Quick Start §7](#7-mcp-server-fastapi-front-end)). See [project_status_audit.md](project_status_audit.md) for the full task-level breakdown.

---

## Linting

```bash
poetry run flake8 src/
poetry run black src/
```

Configuration: `.flake8` + `[tool.black]` in `pyproject.toml` (line-length 88, `E501` noqa).
