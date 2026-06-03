# Project Status Audit

Audit of repository state against [AI_Project_Intelligence_Agent_Plan.md](AI_Project_Intelligence_Agent_Plan.md) (v3.0).

**Audit date:** 2026-06-03 · **Branch:** `main` · **HEAD:** `5d82511` · **Working tree:** clean

---

> [!NOTE]
>
> ## Current state (clean)
>
> The earlier "broken working tree" situation is **resolved** — HEAD and the working tree now match, and the repo describes one coherent state. Recent changes:
>
> - **Agent layer rewritten.** `src/agents/tools.py` (new) replaces the removed `src/tools/registry.py`; `src/agents/report_agent.py` was rewritten with the signature `run_report_agent(user_query, date, sqlite_store, chroma_store)`. Tools now return a uniform `{"result", "source_ids"}` envelope.
> - **Model is configurable via `.env`.** `config.OPENAI_MODEL` drives the agent; it currently targets **`gpt-5.5` via the ckey.vn OpenAI-compatible proxy** (`OPENAI_BASE_URL=https://ckey.vn/v1`). A **live smoke test passed** (a real `chat.completions` call returned successfully).
> - **Legacy/dead files removed** (committed): `src/main.py`, `src/ingestion/run_pipeline.py`, `src/tools/registry.py`, `src/agent/core.py`, `src/memory/store.py`, `tests/test_agent.py`, `tests/test_ingestion_pipeline.py`.
> - **Repo hygiene:** a real `.gitignore` was added; `__pycache__/*.pyc`, `data/vault.db`, and `data/chroma/` are now untracked (kept on disk).

---

## Executive Summary

| Phase                         | Completion | Key Gap                                                                                                             |
| ----------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------- |
| **Week 1** — Design & Data    | **100%**   | All tasks complete                                                                                                  |
| **Week 2** — Ingestion & KB   | **~85%**   | Connectors, storage, chunking, diff, entity extractor all done — but the **orchestrator/entry point was removed**   |
| **Week 3** — Report Agent     | **~95%**   | ReAct loop, 3 tools, citation prompt, 5 mocked tests, **live model verified** — full `report.md` run still unproven |
| **Week 4** — Concern Engine   | **~5%**    | Only config thresholds exist; no detection rules, no severity scoring                                               |
| **Week 5** — MCP & Guardrails | **~2%**    | Only an unused `audit_log` table; no server, no guardrails                                                          |
| **Week 6** — Packaging        | **0%**     | No runner, no outputs, no report                                                                                    |

> [!IMPORTANT]
> The **foundation (Week 1) and the intelligence layer (Week 3 Report Agent)** are solid and committed, and the agent reaches a live LLM successfully. The two immediate structural gaps are: (1) **no ingestion entry point** — `run_pipeline.py` was deleted, so there's no one-command way to (re)build the SQLite + Chroma stores; and (2) the **Concern Engine (Week 4)**, **delivery layer (Week 5: MCP + guardrails)**, and **packaging (Week 6)** are still not started.

---

## Week 1 — Design & Data Prep

| #   | Task                                                         | Status  | Evidence                                                                                                                                |
| --- | ------------------------------------------------------------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1.1 | **SQLite schema** (entities, snapshots, backlinks, sync_log) | ✅ Done | [init_db.py](src/storage/init_db.py) — 4 plan tables + `audit_log` + 3 indexes (97 lines)                                               |
| 1.1 | **ChromaDB schema** (3 collections)                          | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `confluence_chunks`, `meeting_chunks`, `jira_descriptions`                             |
| 1.2 | **Jira synthetic data**                                      | ✅ Done | [jira_synthetic_AIP.json](data/jira/jira_synthetic_AIP.json) — **1000 issues**, every issue carries a `_ground_truth` field             |
| 1.2 | **Confluence synthetic data** (JSON + metadata)              | ✅ Done | [confluence_synthetic.json](data/confluence/confluence_synthetic.json) — **217 pages**, with `linked_jira_epics`                        |
| 1.2 | **Meeting Notes**                                            | ✅ Done | [meeting_notes.json](data/meeting_notes/meeting_notes.json) — **5 meetings** with action items + ground truth                           |
| 1.2 | **Inject 4 anomaly types**                                   | ✅ Done | AIP-30 (cross-source conflict), AIP-37 (stalled), AIP-53 (deadline), AIP-67 (blocker)                                                   |
| 1.3 | **Python repo structure** (src/, data/, tests/)              | ✅ Done | Layout present with `pyproject.toml`; `.gitignore` added                                                                                |
| 1.3 | **Linter config** (flake8 + black)                           | ✅ Done | `.flake8` + `[tool.black]` in `pyproject.toml` (line-length 88)                                                                         |
| 1.3 | **config.py with thresholds**                                | ✅ Done | [config.py](config.py) — 4 thresholds + `MAX_AGENT_ITERATIONS`, OpenAI settings (key/base_url/model), chunk params, `validate_config()` |
| 1.3 | **Basic unit test** (CI green)                               | ✅ Done | 8 test files; **49 pass / 1 fails** on a stale assertion (see Test Suite)                                                               |

---

## Week 2 — Ingestion Pipeline & Knowledge Base

| #   | Task                                     | Status     | Evidence                                                                                                                                                                                                 |
| --- | ---------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1 | **Jira connector**                       | ✅ Done    | [jira_connector.py](src/ingestion/jira_connector.py) — loads JSON, normalizes, extracts ADF text (86 lines)                                                                                              |
| 2.1 | **Confluence connector**                 | ✅ Done    | [confluence_connector.py](src/ingestion/confluence_connector.py) — folder/JSON loader, validation, normalization (181 lines)                                                                             |
| 2.1 | **Meeting Notes connector**              | ✅ Done    | [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) — JSON + plain text, issue-key extraction (343 lines)                                                                             |
| 2.2 | **Route 1 → ChromaDB** (chunking)        | ✅ Done    | [chroma_store.py](src/storage/chroma_store.py) — `add_confluence_chunks()` (MarkdownHeaderTextSplitter), `add_meeting_chunks()` (RecursiveCharacterTextSplitter), `add_jira_description()` (whole)       |
| 2.2 | **Route 2 → SQLite** (entity upsert)     | ✅ Done    | [sqlite_store.py](src/storage/sqlite_store.py) — `upsert_entity()`, `bulk_upsert()`, `save_snapshot()`, `query_entity()`, `insert_backlinks()`, `update_sync_log()` (203 lines)                          |
| 2.2 | **Entity extraction** (regex + rules)    | ✅ Done    | [entity_extractor.py](src/ingestion/entity_extractor.py) — `EntityExtractor.extract()` produces entities + backlinks (linked epics, inline `AIP-\d+` mentions, action items) across 3 sources (91 lines) |
| 2.3 | **Day-over-day diff**                    | ✅ Done    | [sqlite_store.py:134-154](src/storage/sqlite_store.py#L134-L154) — `get_daily_diff()` with snapshot self-join on `DATE(?, '-1 day')`                                                                     |
| —   | **Ingestion orchestrator / entry point** | ❌ Removed | `src/ingestion/run_pipeline.py` was **deleted** (commit `e48a91b`). The components above exist and are tested, but there is **no single runnable pipeline** to (re)build the stores end-to-end.          |

---

## Week 3 — Report Agent (OpenAI SDK + ReAct Loop)

| #   | Task                                                              | Status        | Evidence                                                                                                                                                                                                                                                                                |
| --- | ----------------------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.1 | **Tool definitions** (query_chroma, query_sqlite, get_daily_diff) | ✅ Done       | [tools.py](src/agents/tools.py) (292 lines) — 3 OpenAI function schemas + `dispatch_tool(name, args, sqlite_store, chroma_store)` returning `{"result", "source_ids"}`; unknown tool → `{"error": "Unknown tool"}`; `epic_filter` matched in Python (Chroma `where` has no `$contains`) |
| 3.2 | **ReAct loop** (OpenAI SDK)                                       | ✅ Done       | [report_agent.py](src/agents/report_agent.py) (274 lines) — `run_report_agent(user_query, date, sqlite_store, chroma_store)`, `tool_choice="auto"`, ≤ `MAX_AGENT_ITERATIONS`; on overflow `_finalize_partial()` salvages a partial report + caveat; logs tool name/args each iteration  |
| 3.3 | **Citation enforcement** (system prompt)                          | ✅ Done       | `SYSTEM_PROMPT` mandates `[source_id]` after every claim, forbids unsourced claims (NO HALLUCINATION → "(No verified data found.)"), and fixes the output to 4 sections: **Overview / Changes Today / Concerns / Next Actions**                                                         |
| —   | **Model configuration + CLI**                                     | ✅ Done       | `MODEL = OPENAI_MODEL` (from `.env`); `_make_client()` honours `OPENAI_API_KEY` + `OPENAI_BASE_URL`; `__main__` CLI (`--date`, `--query`) for `python src/agents/report_agent.py`                                                                                                       |
| —   | **Live LLM connectivity**                                         | ✅ Verified   | Smoke test: a real `chat.completions.create(model="gpt-5.5")` against `https://ckey.vn/v1` returned successfully                                                                                                                                                                        |
| —   | **End-to-end report (V2)**                                        | ⚠️ Unverified | No confirmed full run producing a `report.md` with ≥5 valid citations. The stores exist on disk so a local run is feasible, but it has not been exercised.                                                                                                                              |

---

## Week 4 — Concern Engine (Rule-based + LLM)

| #   | Task                                                 | Status         | Evidence                                                                                                |
| --- | ---------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------- |
| 4.1 | **Config thresholds**                                | ✅ Done        | [config.py](config.py) — `STALLED_DAYS`, `DEADLINE_RISK_DAYS`, `BLOCKER_OPEN_DAYS`, `CONFLICT_WINDOW_H` |
| 4.2 | **Rule 1: Stalled task** (SQL)                       | ❌ Not started | No implementation                                                                                       |
| 4.2 | **Rule 2: Deadline risk** (SQL)                      | ❌ Not started | No implementation                                                                                       |
| 4.2 | **Rule 3: Unresolved blocker** (SQL)                 | ❌ Not started | No implementation                                                                                       |
| 4.3 | **Cross-source conflict** (rule filter → LLM verify) | ❌ Not started | No implementation                                                                                       |
| 4.4 | **Severity scoring**                                 | ❌ Not started | No `score_severity()`                                                                                   |
| —   | **concern_engine.py**                                | ❌ Not started | `src/agents/concern_engine.py` does not exist                                                           |

---

## Week 5 — MCP Server & Guardrails

| #   | Task                                   | Status         | Evidence                                                                                                 |
| --- | -------------------------------------- | -------------- | -------------------------------------------------------------------------------------------------------- |
| 5.1 | **MCP Server** (FastAPI + 3 endpoints) | ❌ Not started | No `mcp/` package                                                                                        |
| 5.2 | **Input guardrail** (sanitize_input)   | ❌ Not started | No `guardrail/` package                                                                                  |
| 5.2 | **Output guardrail** (sanitize_output) | ❌ Not started | Same                                                                                                     |
| 5.2 | **Audit log** (SQLite)                 | ⚠️ Partial     | `audit_log` table exists in [init_db.py:62-69](src/storage/init_db.py#L62-L69), but no code writes to it |
| 5.3 | **End-to-end test** (curl)             | ❌ Not started | No API to test                                                                                           |

---

## Week 6 — Packaging & Report

| #   | Task                                   | Status         | Evidence                                                                |
| --- | -------------------------------------- | -------------- | ----------------------------------------------------------------------- |
| 6.1 | **run_agent.sh** (one-command runner)  | ❌ Not started | File does not exist (and no ingestion entry point to call)              |
| 6.2 | **V1**: e2e no crash                   | ❌ Not started | Blocked — needs ingestion entry point + Week 4–6                        |
| 6.2 | **V2**: report.md with 5+ citations    | ⚠️ Blocked     | Report Agent works + model reachable, but no verified run; no `output/` |
| 6.2 | **V3**: all 4 anomaly types detected   | ❌ Not started | No concern engine                                                       |
| 6.2 | **V4**: Precision/Recall ≥ 80%         | ❌ Not started | No concern engine                                                       |
| 6.2 | **V5**: Guardrail blocks 3+ injections | ❌ Not started | No guardrails                                                           |
| 6.2 | **V6**: Live demo                      | ❌ Not started | —                                                                       |
| 6.3 | **Tech Report**                        | ❌ Not started | —                                                                       |

---

## File-Level Implementation Quality

All files below are committed and present on disk (working tree is clean).

### ✅ Production-Quality Files

| File                                                                   | Lines | Quality Notes                                                                                                     |
| ---------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------- |
| [report_agent.py](src/agents/report_agent.py)                          | 274   | ReAct loop, citation system prompt (4 sections), partial+caveat overflow, per-iteration logging, CLI              |
| [tools.py](src/agents/tools.py)                                        | 292   | 3 OpenAI tool schemas + `dispatch_tool` returning `{"result","source_ids"}`; unknown→error; epic_filter in Python |
| [jira_connector.py](src/ingestion/jira_connector.py)                   | 86    | Clean, ADF text extraction, typed                                                                                 |
| [confluence_connector.py](src/ingestion/confluence_connector.py)       | 181   | Robust validation, logging, docstrings                                                                            |
| [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) | 343   | JSON + plain text, issue-key regex, well-documented                                                               |
| [entity_extractor.py](src/ingestion/entity_extractor.py)               | 91    | Per-source routing, backlink extraction, regex `[A-Z]+-\d+`                                                       |
| [chroma_store.py](src/storage/chroma_store.py)                         | 250   | 3 collections, correct splitters/params, query method                                                             |
| [sqlite_store.py](src/storage/sqlite_store.py)                         | 203   | Context manager, bulk upsert, snapshot + diff, backlinks, typed                                                   |
| [init_db.py](src/storage/init_db.py)                                   | 97    | 5 tables + 3 indexes, CLI                                                                                         |
| [config.py](config.py)                                                 | 29    | Thresholds + OpenAI settings (key/base_url/model via `.env`) + chunk params + `validate_config()`                 |

### ❌ Genuinely Missing (in plan / referenced, not in repo)

| Expected File                              | Plan Reference           | Note                                                  |
| ------------------------------------------ | ------------------------ | ----------------------------------------------------- |
| **ingestion entry point**                  | Week 2 / Week 6          | `run_pipeline.py` was removed; needs reimplementation |
| `src/agents/concern_engine.py`             | Week 4 §4.2–4.4          | Not started                                           |
| `src/guardrail/sanitizer.py`               | Week 5 §5.2              | Not started                                           |
| `src/mcp/server.py`                        | Week 5 §5.1              | Not started                                           |
| `run_agent.sh`                             | Week 6 §6.1              | Not started                                           |
| `src/main.py`                              | (app wiring)             | Removed deliberately ("implement later")              |
| `output/report.md`, `output/concerns.json` | Week 6 generated outputs | Not produced                                          |

---

## Test Suite Status

`python -m pytest`: **49 passed, 1 failed** (no collection errors).

| Test File                                                                | Result    | Notes                                                                                                                            |
| ------------------------------------------------------------------------ | --------- | -------------------------------------------------------------------------------------------------------------------------------- |
| [test_report_agent.py](tests/test_report_agent.py)                       | ✅ Pass   | 5 mocked-OpenAI ReAct tests: tool-call→answer path, max-iteration caveat, empty-result no-hallucination, system-prompt guardrail |
| [test_chunking.py](tests/test_chunking.py)                               | ✅ Pass   | Covers all 3 collection types                                                                                                    |
| [test_confluence_connector.py](tests/test_confluence_connector.py)       | ✅ Pass   | Validation, normalization, edge cases                                                                                            |
| [test_jira_connector.py](tests/test_jira_connector.py)                   | ✅ Pass   | Load + normalize + ground-truth stripping                                                                                        |
| [test_entity_extractor.py](tests/test_entity_extractor.py)               | ✅ Pass   | Jira/Confluence/Meeting extraction paths                                                                                         |
| [test_ingestion_integration.py](tests/test_ingestion_integration.py)     | ✅ Pass   | JiraConnector → SQLiteStore round-trip                                                                                           |
| [test_meeting_notes_connector.py](tests/test_meeting_notes_connector.py) | ⚠️ 1 fail | `test_load_meeting_notes_json` asserts `len == 4` but data has **5 meetings** — stale, update to 5                               |

> Removed since last audit: `tests/test_agent.py` (old `ReportAgent`/`tools.registry` API) and `tests/test_ingestion_pipeline.py` (tested the removed orchestrator).

---

## Configuration & Repo Hygiene

- **LLM:** `config.OPENAI_MODEL=gpt-5.5`, `OPENAI_BASE_URL=https://ckey.vn/v1`, key in untracked `.env`. ckey.vn is an OpenAI-compatible proxy; its usage payload suggests `gpt-5.5` is mapped onto a Claude backend (works for our purposes).
- **`.gitignore`** added: ignores `__pycache__/`, `*.pyc`, caches, venvs, `data/vault.db`, `data/chroma/`, OS cruft. `.env` stays ignored; source JSON under `data/{jira,confluence,meeting_notes}/` stays tracked.
- **Untracked generated stores:** `data/vault.db` + `data/chroma/` are no longer versioned (still on the local disk). A fresh clone has no populated KB **and** no orchestrator to rebuild it.

---

## Risks & Follow-ups

1. **No ingestion entry point (structural).** With `run_pipeline.py` gone, nothing wires the 3 connectors → stores. Needed before the agent can run on a fresh checkout and before Week 6 packaging. Re-create a small orchestrator/CLI.
2. **🔑 Leaked key in git history.** The old `sk-8d11…` key was removed from the tree (with `test_agent.py`), but it still exists in history (commit `d2657ea`). **Rotate it on ckey.vn.** (The current `sk-c9bf…` key is safe — only in the untracked `.env`.)
3. **Agent end-to-end (V2) unproven.** Confidence is from mocked tests + a connectivity smoke test. A real `run_report_agent` → `report.md` with ≥5 valid citations against the populated stores has not been run.
4. **Stale test.** `test_meeting_notes_connector.py::test_load_meeting_notes_json` expects 4 meetings; the data has 5. One-line fix to make the suite fully green.
5. **Import conventions.** `report_agent.py` bootstraps `sys.path` then uses `from agents…/storage…/config`; tests rely on [conftest.py](tests/conftest.py) putting root + `src/` on `sys.path`; `test_entity_extractor.py` uses `from src.ingestion…`. Still works, but standardising would avoid entry-point-dependent imports.

---

## What to Build Next (to follow the plan)

1. **Re-create the ingestion entry point** (orchestrator/CLI): load 3 connectors → `EntityExtractor` → SQLite + Chroma, so the stores can be rebuilt and the agent run end-to-end.
2. **Verify Week 3 end-to-end:** run `run_report_agent` against the populated stores; confirm a `report.md` with ≥5 valid `[source_id]` citations (V2).
3. **Week 4 — `concern_engine.py`:** 3 deterministic SQL rules (stalled / deadline / blocker), cross-source conflict (rule filter → LLM verify), `score_severity()`, CLI emitting `concerns.json`.
4. **Week 5 — delivery layer:** FastAPI MCP server (`/ingest`, `/report`, `/concerns`) with `X-API-Key`; input/output guardrails writing to the existing `audit_log` table.
5. **Week 6 — packaging:** `run_agent.sh`, `output/` artifacts, the V1–V6 verification checklist, and the tech report.
