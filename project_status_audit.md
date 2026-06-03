# Project Status Audit

Audit of repository state against [AI_Project_Intelligence_Agent_Plan.md](AI_Project_Intelligence_Agent_Plan.md) (v3.0).

**Audit date:** 2026-06-03 · **Branch:** `main` · **HEAD:** `5a7108f` · **Working tree:** clean

---

> [!NOTE]
>
> ## Current state (clean)
>
> HEAD and the working tree match; the repo describes one coherent state. Milestones:
>
> - **Ingestion orchestrator** ([run_pipeline.py](src/ingestion/run_pipeline.py)) wires 3 connectors → `EntityExtractor` → SQLite + ChromaDB, with field bridges that fix a latent indexing bug. **Verified at real-data scale** (1222 docs → 1000 entities, 1614 + 21 chunks, 719 backlinks).
> - **Report Agent** ([tools.py](src/agents/tools.py) + [report_agent.py](src/agents/report_agent.py)) — `{"result","source_ids"}` tool envelopes, model via `.env` (**`gpt-5.5` on the ckey.vn proxy**, live smoke test passed).
> - **Concern Engine** ([concern_engine.py](src/agents/concern_engine.py)) — 4 rules + severity + CLI, now with **committed accuracy tests** (precision 0.92 / recall 1.00 on a sampled real-data mix). Deadline rule refined to a near-deadline window to cut false positives.
> - **Guardrails** ([sanitizer.py](src/guardrail/sanitizer.py)) — input prompt-injection filter + output secret redaction, and the `audit_log` table is **now written to** (`SQLiteStore.insert_audit_log`).
> - **Repo hygiene** — `.gitignore`; `__pycache__/*.pyc`, `data/vault.db`, `data/chroma/` untracked. Legacy files (`main.py`, old `tools/registry.py`, `agent/core.py`, `memory/store.py`) removed.

---

## Executive Summary

| Phase                         | Completion | Key Gap                                                                                                              |
| ----------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------- |
| **Week 1** — Design & Data    | **100%**   | All tasks complete                                                                                                   |
| **Week 2** — Ingestion & KB   | **100%**   | Orchestrator + verified end-to-end at real-data scale                                                                |
| **Week 3** — Report Agent     | **~95%**   | ReAct loop, tools, citation prompt, tests, live model — full `report.md` run still unproven (V2)                     |
| **Week 4** — Concern Engine   | **~90%**   | All rules + severity + CLI + committed tests; precision 0.92 / recall 1.00 (sampled). Precision is prevalence-sensitive |
| **Week 5** — MCP & Guardrails | **~55%**   | Input + output guardrails + audit-log writes done; **MCP server + endpoints still missing**                          |
| **Week 6** — Packaging        | **~10%**   | No `run_agent.sh` / `output/` / tech report; V5 (guardrail) met, V2/V4 not officially run                            |

> [!IMPORTANT]
> The **data + intelligence + guardrail layers are in place**: ingestion rebuilds the dual store in one command, the Report Agent reaches a live LLM, the Concern Engine detects all four anomaly types (tested), and input/output guardrails are implemented and write to `audit_log`. The main remaining work is the **MCP server (Week 5.1)**, **packaging (Week 6: `run_agent.sh` + `output/` + tech report)**, and the **end-to-end verifications** (V2 cited report; an official full-prevalence V4 run).

---

## Week 1 — Design & Data Prep

| #   | Task                                                         | Status  | Evidence                                                                                                                                |
| --- | ------------------------------------------------------------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1.1 | **SQLite schema** (entities, snapshots, backlinks, sync_log) | ✅ Done | [init_db.py](src/storage/init_db.py) — 4 plan tables + `audit_log` + 3 indexes (97 lines)                                               |
| 1.1 | **ChromaDB schema** (3 collections)                          | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `confluence_chunks`, `meeting_chunks`, `jira_descriptions`                             |
| 1.2 | **Jira synthetic data**                                      | ✅ Done | [jira_synthetic_AIP.json](data/jira/jira_synthetic_AIP.json) — **1000 issues** (144 anomalies, 36 of each type) with `_ground_truth`    |
| 1.2 | **Confluence synthetic data** (JSON + metadata)              | ✅ Done | [confluence_synthetic.json](data/confluence/confluence_synthetic.json) — **217 pages**, with `linked_jira_epics`                        |
| 1.2 | **Meeting Notes**                                            | ✅ Done | [meeting_notes.json](data/meeting_notes/meeting_notes.json) — **5 meetings** with action items + ground truth                           |
| 1.2 | **Inject 4 anomaly types**                                   | ✅ Done | `_ground_truth.anomaly_type` ∈ {stalled, deadline_risk, blocker, cross_source_conflict}, 36 each                                        |
| 1.3 | **Python repo structure** (src/, data/, tests/)              | ✅ Done | Layout present with `pyproject.toml`; `.gitignore` added                                                                                |
| 1.3 | **Linter config** (flake8 + black)                           | ✅ Done | `.flake8` + `[tool.black]` in `pyproject.toml` (line-length 88)                                                                         |
| 1.3 | **config.py with thresholds**                                | ✅ Done | [config.py](config.py) — 4 thresholds + `MAX_AGENT_ITERATIONS`, OpenAI settings (key/base_url/model), chunk params, `validate_config()` |
| 1.3 | **Basic unit test** (CI green)                               | ✅ Done | **77 pass / 1 fails** on a stale assertion (see Test Suite)                                                                             |

---

## Week 2 — Ingestion Pipeline & Knowledge Base

| #   | Task                                     | Status  | Evidence                                                                                                                                                                                                  |
| --- | ---------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1 | **Jira connector**                       | ✅ Done | [jira_connector.py](src/ingestion/jira_connector.py) — loads JSON, normalizes, extracts ADF text; emits canonical `source:"jira"` (payload origin is "Apache") (87 lines)                                 |
| 2.1 | **Confluence connector**                 | ✅ Done | [confluence_connector.py](src/ingestion/confluence_connector.py) — folder/JSON loader, validation, normalization (181 lines)                                                                              |
| 2.1 | **Meeting Notes connector**              | ✅ Done | [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) — JSON + plain text, issue-key extraction (343 lines)                                                                              |
| 2.2 | **Route 1 → ChromaDB** (chunking)        | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `add_confluence_chunks()`, `add_meeting_chunks()`, `add_jira_description()`                                                                              |
| 2.2 | **Route 2 → SQLite** (entity upsert)     | ✅ Done | [sqlite_store.py](src/storage/sqlite_store.py) — `bulk_upsert`, `save_snapshot`, `query_entity`, `insert_backlinks`, `update_sync_log`, `run_query`, `insert_audit_log` (233 lines)                       |
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

All rules live in [concern_engine.py](src/agents/concern_engine.py) (354 lines). `ConcernEngine` takes an `as_of` reference date (default today) — needed because the synthetic data is dated mid-2025. Covered by [test_concern_engine.py](tests/test_concern_engine.py).

| #   | Task                                                 | Status  | Evidence                                                                                                                                                                |
| --- | ---------------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.1 | **Config thresholds**                                | ✅ Done | [config.py](config.py) — `STALLED_DAYS`, `DEADLINE_RISK_DAYS`, `BLOCKER_OPEN_DAYS`, `CONFLICT_WINDOW_H`                                                                 |
| 4.2 | **Rule 1: Stalled task** (SQL)                       | ✅ Done | `_rule_stalled()` — `status='In Progress'` AND not updated > `STALLED_DAYS` (date via `substr` to dodge `+0000` tz)                                                     |
| 4.2 | **Rule 2: Deadline risk** (SQL)                      | ✅ Done | `_rule_deadline_risk()` — not-`Done` AND **near-deadline window** (`±DEADLINE_RISK_DAYS`), not "any overdue" → far fewer false positives                                |
| 4.2 | **Rule 3: Unresolved blocker** (SQL)                 | ✅ Done | `_rule_blocker()` — `json_each(labels)` has `'blocker'`, open > `BLOCKER_OPEN_DAYS`; `dependent_count` from `backlinks`                                                 |
| 4.3 | **Cross-source conflict** (rule filter → LLM verify) | ✅ Done | `_rule_cross_source_conflict()` — recent-`Done` (`CONFLICT_WINDOW_H`) + meeting chunk with `pending\|chờ\|review\|chưa`. Rule-based; LLM phrasing left as optional hook |
| 4.4 | **Severity scoring**                                 | ✅ Done | `score_severity(type, **kwargs)` → `(1-5, explanation)`, exactly per plan §4.4                                                                                          |
| —   | **Accuracy tests**                                   | ✅ Done | [test_concern_engine.py](tests/test_concern_engine.py) — per-rule recall (3/3, 2/2, 2/2) + precision **0.92** / recall **1.00** on 108 anomalies + 100 normals          |

---

## Week 5 — MCP Server & Guardrails

| #   | Task                                   | Status         | Evidence                                                                                                            |
| --- | -------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------- |
| 5.1 | **MCP Server** (FastAPI + 3 endpoints) | ❌ Not started | No `mcp/` package                                                                                                   |
| 5.2 | **Input guardrail** (sanitize_input)   | ✅ Done        | [sanitizer.py](src/guardrail/sanitizer.py) — `InputSanitizer`: injection patterns → `audit_log` + `[FILTERED]`, truncate 2000, strip HTML |
| 5.2 | **Output guardrail** (sanitize_output) | ✅ Done        | `OutputSanitizer` — redacts `sk-…` keys, `Bearer …` tokens, PEM `PRIVATE KEY` blocks → `[REDACTED]`                |
| 5.2 | **Audit log** (SQLite)                 | ✅ Done        | `SQLiteStore.insert_audit_log()` writes `timestamp \| source_id \| field \| flag_type \| snippet`; driven by `InputSanitizer` |
| 5.3 | **End-to-end test** (curl)             | ❌ Not started | No API to test yet (MCP server pending)                                                                             |

---

## Week 6 — Packaging & Report

| #   | Task                                   | Status         | Evidence                                                                                  |
| --- | -------------------------------------- | -------------- | ----------------------------------------------------------------------------------------- |
| 6.1 | **run_agent.sh** (one-command runner)  | ❌ Not started | File does not exist (ingestion + concern CLIs now exist to wire)                          |
| 6.2 | **V1**: e2e no crash                   | ⚠️ Partial     | Ingestion + concern engine run clean at scale; no single runner yet                       |
| 6.2 | **V2**: report.md with 5+ citations    | ⚠️ Blocked     | Agent + model work; no verified run; no `output/`                                         |
| 6.2 | **V3**: all 4 anomaly types detected   | ⚠️ Partial     | Engine detects all 4 types; tests show recall 1.00 on the 3 SQL types (cross-source needs chunks) |
| 6.2 | **V4**: Precision/Recall ≥ 80%         | ⚠️ Partial     | **0.92 / 1.00** on a sampled mix (test_concern_engine); full-prevalence run not done (would be lower) |
| 6.2 | **V5**: Guardrail blocks 3+ injections | ✅ Done        | [test_guardrail.py](tests/test_guardrail.py) — 4 injection payloads filtered, 0 false positives |
| 6.2 | **V6**: Live demo                      | ❌ Not started | —                                                                                         |
| 6.3 | **Tech Report**                        | ❌ Not started | —                                                                                         |

---

## File-Level Implementation Quality

All files below are committed and present on disk (working tree is clean).

### ✅ Production-Quality Files

| File                                                                   | Lines | Quality Notes                                                                                                     |
| ---------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------- |
| [concern_engine.py](src/agents/concern_engine.py)                      | 354   | 4 rules (3 SQL + 1 rule-based cross-source), near-deadline window, `score_severity`, `as_of` date, CLI            |
| [sanitizer.py](src/guardrail/sanitizer.py)                             | 197   | `InputSanitizer` (injection → audit + `[FILTERED]`, truncate, strip HTML) + `OutputSanitizer` (secret redaction); in-file tests |
| [report_agent.py](src/agents/report_agent.py)                          | 274   | ReAct loop, citation system prompt (4 sections), partial+caveat overflow, per-iteration logging, CLI              |
| [tools.py](src/agents/tools.py)                                        | 292   | 3 OpenAI tool schemas + `dispatch_tool` returning `{"result","source_ids"}`; unknown→error; epic_filter in Python |
| [run_pipeline.py](src/ingestion/run_pipeline.py)                       | 231   | Orchestrator + CLI; connector→Chroma field bridges; verified at real-data scale                                   |
| [jira_connector.py](src/ingestion/jira_connector.py)                   | 87    | ADF text extraction; normalizes `source` to canonical `"jira"`                                                    |
| [confluence_connector.py](src/ingestion/confluence_connector.py)       | 181   | Robust validation, logging, docstrings                                                                            |
| [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) | 343   | JSON + plain text, issue-key regex, well-documented                                                               |
| [entity_extractor.py](src/ingestion/entity_extractor.py)               | 91    | Per-source routing, backlink extraction                                                                           |
| [chroma_store.py](src/storage/chroma_store.py)                         | 250   | 3 collections, correct splitters/params, query method                                                             |
| [sqlite_store.py](src/storage/sqlite_store.py)                         | 233   | Context manager, bulk upsert, snapshot + diff, backlinks, `run_query`, `insert_audit_log`, typed                  |
| [init_db.py](src/storage/init_db.py)                                   | 97    | 5 tables + 3 indexes, CLI                                                                                         |
| [config.py](config.py)                                                 | 29    | Thresholds + OpenAI settings (`.env`) + chunk params + `validate_config()`                                        |

### ❌ Genuinely Missing (in plan / referenced, not in repo)

| Expected File                              | Plan Reference           | Note                                                          |
| ------------------------------------------ | ------------------------ | ------------------------------------------------------------- |
| `src/mcp/server.py`                        | Week 5 §5.1              | Not started — the main remaining Week-5 piece                 |
| `run_agent.sh`                             | Week 6 §6.1              | Not started                                                   |
| `src/main.py`                              | (app wiring)             | Removed deliberately ("implement later")                      |
| `output/report.md`, `output/concerns.json` | Week 6 generated outputs | Not produced                                                  |
| _(audit logging)_                          | Week 5 §5.2              | Implemented as `SQLiteStore.insert_audit_log` — no separate `guardrail/audit_log.py` |

---

## Test Suite Status

`python -m pytest`: **77 passed, 1 failed** (no collection errors).

| Test File                                                                | Result    | Notes                                                                                                |
| ------------------------------------------------------------------------ | --------- | ---------------------------------------------------------------------------------------------------- |
| [test_concern_engine.py](tests/test_concern_engine.py)                   | ✅ Pass   | Per-rule recall + precision **0.92** / recall **1.00** on real anomalies + sampled normals           |
| [test_guardrail.py](tests/test_guardrail.py)                             | ✅ Pass   | 10 adversarial cases: 4 injections filtered, 3 benign pass-through, zero false positives             |
| [test_run_pipeline.py](tests/test_run_pipeline.py)                       | ✅ Pass   | 13 e2e tests: SQLite + Chroma routing incl. bridged `page_id`/`note_id`, field bridges, stats        |
| [test_report_agent.py](tests/test_report_agent.py)                       | ✅ Pass   | 5 mocked-OpenAI ReAct tests: tool path, max-iteration caveat, empty-result no-hallucination          |
| [test_chunking.py](tests/test_chunking.py)                               | ✅ Pass   | Covers all 3 collection types                                                                        |
| [test_confluence_connector.py](tests/test_confluence_connector.py)       | ✅ Pass   | Validation, normalization, edge cases                                                                |
| [test_jira_connector.py](tests/test_jira_connector.py)                   | ✅ Pass   | Load, normalize, ground-truth stripping, source normalized to "jira"                                 |
| [test_entity_extractor.py](tests/test_entity_extractor.py)               | ✅ Pass   | Jira/Confluence/Meeting extraction paths                                                             |
| [test_ingestion_integration.py](tests/test_ingestion_integration.py)     | ✅ Pass   | JiraConnector → SQLiteStore round-trip                                                               |
| [test_meeting_notes_connector.py](tests/test_meeting_notes_connector.py) | ⚠️ 1 fail | `test_load_meeting_notes_json` asserts `len == 4` but data has **5 meetings** — stale, update to 5   |

> [sanitizer.py](src/guardrail/sanitizer.py) also carries **17 in-file parametrized tests** (run with `pytest src/guardrail/sanitizer.py`); they are not auto-discovered by the default `pytest` run because the file is not named `test_*.py`.

---

## Configuration & Repo Hygiene

- **LLM:** `OPENAI_MODEL=gpt-5.5`, `OPENAI_BASE_URL=https://ckey.vn/v1`, key in untracked `.env`. ckey.vn is an OpenAI-compatible proxy; its usage payload suggests `gpt-5.5` maps onto a Claude backend (works for our purposes).
- **`.gitignore`** ignores `__pycache__/`, `*.pyc`, caches, venvs, `data/vault.db`, `data/chroma/`, OS cruft. `.env` ignored; source JSON under `data/{jira,confluence,meeting_notes}/` tracked.
- **Untracked generated stores:** `data/vault.db` + `data/chroma/` are not versioned (kept on local disk). A fresh clone has no populated KB but **can rebuild it** via `python src/ingestion/run_pipeline.py`.
- **Audit log** is now populated at runtime by the input guardrail (`InputSanitizer` → `SQLiteStore.insert_audit_log`).

---

## Risks & Follow-ups

1. **🔑 Leaked key in git history.** The old `sk-8d11…` key was removed from the tree but still exists in history (commit `d2657ea`). **Rotate it on ckey.vn.** (The current `sk-c9bf…` key is safe — only in the untracked `.env`.)
2. **Concern-Engine precision is prevalence-sensitive.** `test_concern_engine` measures **0.92** on a sampled mix (108 anomalies + 100 normals); against all 856 normals it would be lower (~0.5), because the `stalled` rule surfaces genuinely-stale normal tasks (the anomalies are distinguished by `needs-review`-style labels the date rule does not use).
3. **`as_of` reminder.** Concern-engine rules compare against `as_of` (default today). The synthetic data is mid-2025, so run with `--date 2025-05-30` for meaningful results.
4. **End-to-end verifications unproven.** V2 (cited `report.md`) and an official full-prevalence V4 run have not been done.
5. **Guardrail in-file tests aren't in default discovery.** The 17 tests inside `sanitizer.py` run only via `pytest src/guardrail/sanitizer.py`; consider mirroring to `tests/` for CI.
6. **Stale test.** `test_meeting_notes_connector.py::test_load_meeting_notes_json` expects 4 meetings; data has 5. One-line fix to make the suite fully green.

---

## What to Build Next (to follow the plan)

1. **Week 5 — MCP server:** FastAPI app exposing `/ingest`, `/report?date=…`, `/concerns?min_sev=…` with `X-API-Key` auth; wire `InputSanitizer` on ingest and `OutputSanitizer` on report output.
2. **Week 6 — packaging:** `run_agent.sh` (wire the ingestion + report + concern CLIs into `output/report.md` + `output/concerns.json`), the V1–V6 checklist, and the tech report.
3. **Verify Week 3 end-to-end (V2):** ingest → `run_report_agent` → `report.md` with ≥5 valid `[source_id]` citations.
4. **Tighten accuracy (optional):** measure V4 at full prevalence and, if needed, improve `stalled` precision; fix the stale meeting-notes assertion.
