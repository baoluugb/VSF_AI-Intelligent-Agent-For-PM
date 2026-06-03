# Project Status Audit

Audit of repository state against [AI_Project_Intelligence_Agent_Plan.md](AI_Project_Intelligence_Agent_Plan.md) (v3.0).

**Audit date:** 2026-06-03 · **Branch:** `main` · **HEAD:** `40a8f05` · **Working tree:** clean

---

> [!NOTE]
>
> ## Current state (clean)
>
> HEAD and the working tree match; the repo describes one coherent state. Recent milestones:
>
> - **Ingestion orchestrator re-added.** [run_pipeline.py](src/ingestion/run_pipeline.py) wires the 3 connectors → `EntityExtractor` → SQLite + ChromaDB, with field bridges that fix a latent indexing bug. **Verified at real-data scale** (1222 docs → 1000 entities, 1614 confluence chunks, 21 meeting chunks, 719 backlinks).
> - **Concern Engine added.** [concern_engine.py](src/agents/concern_engine.py) implements all 4 rules + severity scoring + CLI (Week 4).
> - **Report Agent.** [tools.py](src/agents/tools.py) + [report_agent.py](src/agents/report_agent.py), `{"result","source_ids"}` tool envelopes, model via `.env` (**`gpt-5.5` on the ckey.vn proxy**, live smoke test passed).
> - **Repo hygiene.** `.gitignore` added; `__pycache__/*.pyc`, `data/vault.db`, `data/chroma/` untracked. Legacy/dead files (`main.py`, old `tools/registry.py`, `agent/core.py`, `memory/store.py`) removed.

---

## Executive Summary

| Phase                         | Completion | Key Gap                                                                                                                |
| ----------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------- |
| **Week 1** — Design & Data    | **100%**   | All tasks complete                                                                                                     |
| **Week 2** — Ingestion & KB   | **100%**   | Orchestrator re-added + verified end-to-end at real-data scale                                                         |
| **Week 3** — Report Agent     | **~95%**   | ReAct loop, 3 tools, citation prompt, 5 mocked tests, live model verified — full `report.md` run still unproven (V2)   |
| **Week 4** — Concern Engine   | **~80%**   | All 4 rules + severity + CLI done & smoke-verified — but **no committed unit tests**, precision/recall (V4) unmeasured |
| **Week 5** — MCP & Guardrails | **~2%**    | Only an unused `audit_log` table; no server, no guardrails                                                             |
| **Week 6** — Packaging        | **0%**     | No `run_agent.sh`, no `output/`, no tech report                                                                        |

> [!IMPORTANT]
> The **full data + intelligence path is now in place**: ingestion (Week 2) rebuilds the dual store in one command, the Report Agent (Week 3) reaches a live LLM, and the Concern Engine (Week 4) detects all four anomaly types. Remaining work is the **delivery layer (Week 5: MCP + guardrails)**, **packaging (Week 6)**, and the **end-to-end verifications** (V2 cited report, V4 precision/recall) — plus committed tests for the Concern Engine.

---

## Week 1 — Design & Data Prep

| #   | Task                                                         | Status  | Evidence                                                                                                                                |
| --- | ------------------------------------------------------------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1.1 | **SQLite schema** (entities, snapshots, backlinks, sync_log) | ✅ Done | [init_db.py](src/storage/init_db.py) — 4 plan tables + `audit_log` + 3 indexes (97 lines)                                               |
| 1.1 | **ChromaDB schema** (3 collections)                          | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `confluence_chunks`, `meeting_chunks`, `jira_descriptions`                             |
| 1.2 | **Jira synthetic data**                                      | ✅ Done | [jira_synthetic_AIP.json](data/jira/jira_synthetic_AIP.json) — **1000 issues**, each carries a `_ground_truth` field                    |
| 1.2 | **Confluence synthetic data** (JSON + metadata)              | ✅ Done | [confluence_synthetic.json](data/confluence/confluence_synthetic.json) — **217 pages**, with `linked_jira_epics`                        |
| 1.2 | **Meeting Notes**                                            | ✅ Done | [meeting_notes.json](data/meeting_notes/meeting_notes.json) — **5 meetings** with action items + ground truth                           |
| 1.2 | **Inject 4 anomaly types**                                   | ✅ Done | AIP-30 (cross-source conflict), AIP-37 (stalled), AIP-53 (deadline), AIP-67 (blocker)                                                   |
| 1.3 | **Python repo structure** (src/, data/, tests/)              | ✅ Done | Layout present with `pyproject.toml`; `.gitignore` added                                                                                |
| 1.3 | **Linter config** (flake8 + black)                           | ✅ Done | `.flake8` + `[tool.black]` in `pyproject.toml` (line-length 88)                                                                         |
| 1.3 | **config.py with thresholds**                                | ✅ Done | [config.py](config.py) — 4 thresholds + `MAX_AGENT_ITERATIONS`, OpenAI settings (key/base_url/model), chunk params, `validate_config()` |
| 1.3 | **Basic unit test** (CI green)                               | ✅ Done | 8 test files; **63 pass / 1 fails** on a stale assertion (see Test Suite)                                                               |

---

## Week 2 — Ingestion Pipeline & Knowledge Base

| #   | Task                                     | Status  | Evidence                                                                                                                                                                                                  |
| --- | ---------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1 | **Jira connector**                       | ✅ Done | [jira_connector.py](src/ingestion/jira_connector.py) — loads JSON, normalizes, extracts ADF text; emits canonical `source:"jira"` (payload origin is "Apache") (87 lines)                                 |
| 2.1 | **Confluence connector**                 | ✅ Done | [confluence_connector.py](src/ingestion/confluence_connector.py) — folder/JSON loader, validation, normalization (181 lines)                                                                              |
| 2.1 | **Meeting Notes connector**              | ✅ Done | [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) — JSON + plain text, issue-key extraction (343 lines)                                                                              |
| 2.2 | **Route 1 → ChromaDB** (chunking)        | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `add_confluence_chunks()`, `add_meeting_chunks()`, `add_jira_description()`                                                                              |
| 2.2 | **Route 2 → SQLite** (entity upsert)     | ✅ Done | [sqlite_store.py](src/storage/sqlite_store.py) — `bulk_upsert`, `save_snapshot`, `query_entity`, `insert_backlinks`, `update_sync_log`, `run_query` (213 lines)                                           |
| 2.2 | **Entity extraction** (regex + rules)    | ✅ Done | [entity_extractor.py](src/ingestion/entity_extractor.py) — entities + backlinks across 3 sources (91 lines)                                                                                               |
| 2.3 | **Day-over-day diff**                    | ✅ Done | [sqlite_store.py](src/storage/sqlite_store.py) — `get_daily_diff()` with snapshot self-join on `DATE(?, '-1 day')`                                                                                        |
| —   | **Ingestion orchestrator / entry point** | ✅ Done | [run_pipeline.py](src/ingestion/run_pipeline.py) (231 lines) — `init_db` → connectors → `EntityExtractor` → SQLite + ChromaDB, with field bridges + CLI. **Verified at scale** (1222 docs). 13 e2e tests. |

---

## Week 3 — Report Agent (OpenAI SDK + ReAct Loop)

| #   | Task                                                              | Status        | Evidence                                                                                                                                                                                            |
| --- | ----------------------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.1 | **Tool definitions** (query_chroma, query_sqlite, get_daily_diff) | ✅ Done       | [tools.py](src/agents/tools.py) (292 lines) — 3 schemas + `dispatch_tool` returning `{"result","source_ids"}`; unknown → `{"error": "Unknown tool"}`; `epic_filter` matched in Python               |
| 3.2 | **ReAct loop** (OpenAI SDK)                                       | ✅ Done       | [report_agent.py](src/agents/report_agent.py) (274 lines) — `run_report_agent(user_query, date, sqlite_store, chroma_store)`, `tool_choice="auto"`, ≤ `MAX_AGENT_ITERATIONS`, `_finalize_partial()` |
| 3.3 | **Citation enforcement** (system prompt)                          | ✅ Done       | `SYSTEM_PROMPT` mandates `[source_id]`, forbids unsourced claims, 4 sections: **Overview / Changes Today / Concerns / Next Actions**                                                                |
| —   | **Model config + CLI**                                            | ✅ Done       | `MODEL = OPENAI_MODEL` (`.env`); honours `OPENAI_API_KEY`/`OPENAI_BASE_URL`; `__main__` CLI (`--date`, `--query`)                                                                                   |
| —   | **Live LLM connectivity**                                         | ✅ Verified   | Smoke test: `chat.completions.create(model="gpt-5.5")` against `https://ckey.vn/v1` returned successfully                                                                                           |
| —   | **End-to-end report (V2)**                                        | ⚠️ Unverified | No confirmed full run producing `report.md` with ≥5 valid citations. Now feasible — the orchestrator can populate the stores first.                                                                 |

---

## Week 4 — Concern Engine (Rule-based + LLM)

All rules live in [concern_engine.py](src/agents/concern_engine.py) (344 lines). `ConcernEngine` takes an `as_of` reference date (default today) — needed because the synthetic data is dated mid-2025.

| #   | Task                                                 | Status  | Evidence                                                                                                                                                                |
| --- | ---------------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.1 | **Config thresholds**                                | ✅ Done | [config.py](config.py) — `STALLED_DAYS`, `DEADLINE_RISK_DAYS`, `BLOCKER_OPEN_DAYS`, `CONFLICT_WINDOW_H`                                                                 |
| 4.2 | **Rule 1: Stalled task** (SQL)                       | ✅ Done | `_rule_stalled()` — `status='In Progress'` AND not updated > `STALLED_DAYS` (date via `substr` to dodge `+0000` tz)                                                     |
| 4.2 | **Rule 2: Deadline risk** (SQL)                      | ✅ Done | `_rule_deadline_risk()` — not-`Done` AND within/past `DEADLINE_RISK_DAYS` of `due_date`                                                                                 |
| 4.2 | **Rule 3: Unresolved blocker** (SQL)                 | ✅ Done | `_rule_blocker()` — `json_each(labels)` has `'blocker'`, open > `BLOCKER_OPEN_DAYS`; `dependent_count` from `backlinks`                                                 |
| 4.3 | **Cross-source conflict** (rule filter → LLM verify) | ✅ Done | `_rule_cross_source_conflict()` — recent-`Done` (`CONFLICT_WINDOW_H`) + meeting chunk with `pending\|chờ\|review\|chưa`. Rule-based; LLM phrasing left as optional hook |
| 4.4 | **Severity scoring**                                 | ✅ Done | `score_severity(type, **kwargs)` → `(1-5, explanation)`, exactly per plan §4.4                                                                                          |
| —   | **concern_engine.py + CLI**                          | ✅ Done | `run_all_rules()` merges + sorts desc; CLI `--date/--min-sev` emits concerns JSON. **Smoke-verified** (all 4 fire); no committed unit tests yet                         |

---

## Week 5 — MCP Server & Guardrails

| #   | Task                                   | Status         | Evidence                                                                                   |
| --- | -------------------------------------- | -------------- | ------------------------------------------------------------------------------------------ |
| 5.1 | **MCP Server** (FastAPI + 3 endpoints) | ❌ Not started | No `mcp/` package                                                                          |
| 5.2 | **Input guardrail** (sanitize_input)   | ❌ Not started | No `guardrail/` package                                                                    |
| 5.2 | **Output guardrail** (sanitize_output) | ❌ Not started | Same                                                                                       |
| 5.2 | **Audit log** (SQLite)                 | ⚠️ Partial     | `audit_log` table exists in [init_db.py](src/storage/init_db.py), but no code writes to it |
| 5.3 | **End-to-end test** (curl)             | ❌ Not started | No API to test                                                                             |

---

## Week 6 — Packaging & Report

| #   | Task                                   | Status         | Evidence                                                                     |
| --- | -------------------------------------- | -------------- | ---------------------------------------------------------------------------- |
| 6.1 | **run_agent.sh** (one-command runner)  | ❌ Not started | File does not exist (but ingestion + concern CLIs now exist to wire)         |
| 6.2 | **V1**: e2e no crash                   | ⚠️ Partial     | Ingestion + concern engine run clean at scale; no single runner yet          |
| 6.2 | **V2**: report.md with 5+ citations    | ⚠️ Blocked     | Agent + model work; no verified run; no `output/`                            |
| 6.2 | **V3**: all 4 anomaly types detected   | ⚠️ Partial     | Engine detects all 4 types (smoke test); not yet run on the ground-truth set |
| 6.2 | **V4**: Precision/Recall ≥ 80%         | ❌ Not started | Not measured against `_ground_truth`                                         |
| 6.2 | **V5**: Guardrail blocks 3+ injections | ❌ Not started | No guardrails                                                                |
| 6.2 | **V6**: Live demo                      | ❌ Not started | —                                                                            |
| 6.3 | **Tech Report**                        | ❌ Not started | —                                                                            |

---

## File-Level Implementation Quality

All files below are committed and present on disk (working tree is clean).

### ✅ Production-Quality Files

| File                                                                   | Lines | Quality Notes                                                                                                     |
| ---------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------- |
| [concern_engine.py](src/agents/concern_engine.py)                      | 344   | 4 rules (3 SQL + 1 rule-based cross-source), `score_severity`, `as_of` date, CLI                                  |
| [report_agent.py](src/agents/report_agent.py)                          | 274   | ReAct loop, citation system prompt (4 sections), partial+caveat overflow, per-iteration logging, CLI              |
| [tools.py](src/agents/tools.py)                                        | 292   | 3 OpenAI tool schemas + `dispatch_tool` returning `{"result","source_ids"}`; unknown→error; epic_filter in Python |
| [run_pipeline.py](src/ingestion/run_pipeline.py)                       | 231   | Orchestrator + CLI; connector→Chroma field bridges; verified at real-data scale                                   |
| [jira_connector.py](src/ingestion/jira_connector.py)                   | 87    | ADF text extraction; normalizes `source` to canonical `"jira"`                                                    |
| [confluence_connector.py](src/ingestion/confluence_connector.py)       | 181   | Robust validation, logging, docstrings                                                                            |
| [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) | 343   | JSON + plain text, issue-key regex, well-documented                                                               |
| [entity_extractor.py](src/ingestion/entity_extractor.py)               | 91    | Per-source routing, backlink extraction                                                                           |
| [chroma_store.py](src/storage/chroma_store.py)                         | 250   | 3 collections, correct splitters/params, query method                                                             |
| [sqlite_store.py](src/storage/sqlite_store.py)                         | 213   | Context manager, bulk upsert, snapshot + diff, backlinks, `run_query`, typed                                      |
| [init_db.py](src/storage/init_db.py)                                   | 97    | 5 tables + 3 indexes, CLI                                                                                         |
| [config.py](config.py)                                                 | 29    | Thresholds + OpenAI settings (`.env`) + chunk params + `validate_config()`                                        |

### ❌ Genuinely Missing (in plan / referenced, not in repo)

| Expected File                              | Plan Reference           | Note                                     |
| ------------------------------------------ | ------------------------ | ---------------------------------------- |
| `src/guardrail/sanitizer.py`               | Week 5 §5.2              | Not started                              |
| `src/mcp/server.py`                        | Week 5 §5.1              | Not started                              |
| `run_agent.sh`                             | Week 6 §6.1              | Not started                              |
| `src/main.py`                              | (app wiring)             | Removed deliberately ("implement later") |
| `output/report.md`, `output/concerns.json` | Week 6 generated outputs | Not produced                             |

---

## Test Suite Status

`python -m pytest`: **63 passed, 1 failed** (no collection errors).

| Test File                                                                | Result     | Notes                                                                                                |
| ------------------------------------------------------------------------ | ---------- | ---------------------------------------------------------------------------------------------------- |
| [test_run_pipeline.py](tests/test_run_pipeline.py)                       | ✅ Pass    | 13 e2e tests: SQLite routing, Chroma routing incl. bridged `page_id`/`note_id`, field bridges, stats |
| [test_report_agent.py](tests/test_report_agent.py)                       | ✅ Pass    | 5 mocked-OpenAI ReAct tests: tool path, max-iteration caveat, empty-result no-hallucination          |
| [test_chunking.py](tests/test_chunking.py)                               | ✅ Pass    | Covers all 3 collection types                                                                        |
| [test_confluence_connector.py](tests/test_confluence_connector.py)       | ✅ Pass    | Validation, normalization, edge cases                                                                |
| [test_jira_connector.py](tests/test_jira_connector.py)                   | ✅ Pass    | Load, normalize, ground-truth stripping, **source normalized to "jira"**                             |
| [test_entity_extractor.py](tests/test_entity_extractor.py)               | ✅ Pass    | Jira/Confluence/Meeting extraction paths                                                             |
| [test_ingestion_integration.py](tests/test_ingestion_integration.py)     | ✅ Pass    | JiraConnector → SQLiteStore round-trip                                                               |
| [test_meeting_notes_connector.py](tests/test_meeting_notes_connector.py) | ⚠️ 1 fail  | `test_load_meeting_notes_json` asserts `len == 4` but data has **5 meetings** — stale, update to 5   |
| `tests/test_concern_engine.py`                                           | ❌ Missing | Concern Engine verified via a manual smoke test only — no committed unit tests yet                   |

---

## Configuration & Repo Hygiene

- **LLM:** `OPENAI_MODEL=gpt-5.5`, `OPENAI_BASE_URL=https://ckey.vn/v1`, key in untracked `.env`. ckey.vn is an OpenAI-compatible proxy; its usage payload suggests `gpt-5.5` maps onto a Claude backend (works for our purposes).
- **`.gitignore`** ignores `__pycache__/`, `*.pyc`, caches, venvs, `data/vault.db`, `data/chroma/`, OS cruft. `.env` ignored; source JSON under `data/{jira,confluence,meeting_notes}/` tracked.
- **Untracked generated stores:** `data/vault.db` + `data/chroma/` are not versioned (kept on local disk). A fresh clone has no populated KB but **can rebuild it** via `python src/ingestion/run_pipeline.py`.

---

## Risks & Follow-ups

1. **🔑 Leaked key in git history.** The old `sk-8d11…` key was removed from the tree but still exists in history (commit `d2657ea`). **Rotate it on ckey.vn.** (The current `sk-c9bf…` key is safe — only in the untracked `.env`.)
2. **Concern Engine has no committed tests.** It is verified by a manual smoke test (all 4 rules fire), but a `tests/test_concern_engine.py` is needed to lock the SQL behaviour in CI.
3. **`as_of` reminder.** Concern-engine rules compare against `as_of` (default today). The synthetic data is mid-2025, so run with `--date 2025-05-2x` for meaningful results; with default "now" everything reads as overdue/stale.
4. **End-to-end verifications unproven.** V2 (cited `report.md`) and V4 (precision/recall ≥ 80% vs `_ground_truth`) have not been run.
5. **Stale test.** `test_meeting_notes_connector.py::test_load_meeting_notes_json` expects 4 meetings; data has 5. One-line fix to make the suite fully green.

---

## What to Build Next (to follow the plan)

1. **Test + measure the Concern Engine:** add `tests/test_concern_engine.py`; run on the `_ground_truth` set and report V3/V4 (all-4-types + precision/recall ≥ 80%).
2. **Verify Week 3 end-to-end (V2):** ingest → `run_report_agent` → `report.md` with ≥5 valid `[source_id]` citations.
3. **Week 5 — delivery layer:** FastAPI MCP server (`/ingest`, `/report`, `/concerns`) with `X-API-Key`; input/output guardrails writing to the existing `audit_log` table.
4. **Week 6 — packaging:** `run_agent.sh` (wire the ingestion + report + concern CLIs), `output/` artifacts, the V1–V6 checklist, and the tech report.
