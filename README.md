# VSF AI — Intelligent Agent for Project Management

An end-to-end AI agent that ingests Jira, Confluence, and meeting-notes data, detects project risks, and generates a daily cited report — all in one command.

> **Live demo:** `./run_agent.sh` rebuilds the dual knowledge store, runs the Concern Engine (242 concerns across 4 risk types), and calls a grounded ReAct agent that produces `output/report.md` with 24+ source citations.

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
│   │   └── tools.py                        # OpenAI tool schemas + dispatch
│   ├── guardrail/
│   │   └── sanitizer.py                    # Input injection filter + output secret redaction
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
- [Poetry](https://python-poetry.org/) for dependency management
- An OpenAI-compatible API key (set in `.env`)

### 1. Install dependencies

```bash
poetry install
```

### 2. Configure environment

Copy the example and fill in your credentials:

```bash
cp .env.example .env   # or create .env manually
```

Minimum required variables:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://ckey.vn/v1   # or https://api.openai.com/v1
OPENAI_MODEL=gpt-5.5
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

---

## Running Tests

```bash
poetry run pytest
```

Expected result: **77 passed, 1 failed** (one stale assertion in `test_meeting_notes_connector.py` that expects 4 meetings; the data file has 5).

To also run the 17 in-file guardrail tests:

```bash
poetry run pytest src/guardrail/sanitizer.py
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

| Variable              | Default | Description                                   |
| --------------------- | ------- | --------------------------------------------- |
| `STALLED_DAYS`        | 7       | Days without update before a task is "stalled" |
| `DEADLINE_RISK_DAYS`  | 5       | Window around due-date for deadline-risk rule  |
| `BLOCKER_OPEN_DAYS`   | 3       | Days a blocker must be open to trigger alert   |
| `CONFLICT_WINDOW_H`   | 48      | Hours for cross-source conflict window         |
| `MAX_AGENT_ITERATIONS`| 6       | ReAct loop iteration cap                       |
| `OPENAI_MODEL`        | —       | Model name (set in `.env`)                     |
| `OPENAI_BASE_URL`     | —       | API base URL (set in `.env`)                   |
| `OPENAI_API_KEY`      | —       | API key (set in `.env`, **never commit**)       |

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
| Week 5 — MCP & Guardrails      | **~55%**   |
| Week 6 — Packaging & Demo      | **~85%**   |

The only major remaining piece is the **MCP server** (`src/mcp/server.py`) — a FastAPI front-end with `/ingest`, `/report`, and `/concerns` endpoints. See [project_status_audit.md](project_status_audit.md) for the full task-level breakdown.

---

## Linting

```bash
poetry run flake8 src/
poetry run black src/
```

Configuration: `.flake8` + `[tool.black]` in `pyproject.toml` (line-length 88, `E501` noqa).
