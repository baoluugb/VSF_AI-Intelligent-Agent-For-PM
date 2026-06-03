# Project Status Audit

Audit of repository state against [AI_Project_Intelligence_Agent_Plan.md](AI_Project_Intelligence_Agent_Plan.md) (v3.0).

**Audit date:** 2026-06-03 · **Branch:** `main` · **HEAD:** `d2657ea`

---

> [!CAUTION]
>
> ## ⚠️ Working tree is in a broken/inconsistent state
>
> This audit reflects the **committed code in git HEAD**, which is the project's real built state. However, the **working tree on disk currently has uncommitted deletions** of files that HEAD contains. As a result, the project **cannot run from disk right now** and `test_agent.py` cannot even be imported.
>
> **Deleted in working tree (present in HEAD — recover with `git restore .`):**
>
> | Path | In HEAD | On disk | Impact |
> | ---- | ------- | ------- | ------ |
> | `src/agents/report_agent.py` | ✅ 326 lines | ❌ deleted | Report Agent unrunnable; `test_agent.py` import fails |
> | `src/agents/__init__.py` | ✅ | ❌ deleted | `agents` package missing |
> | `src/tools/registry.py` | ✅ 276 lines | ❌ deleted | Tool dispatch missing |
> | `src/tools/__init__.py` | ✅ | ❌ deleted | `tools` package missing |
> | `src/ingestion/run_pipeline.py` | ✅ 113 lines | ❌ deleted | Ingestion orchestrator missing |
> | `src/agent/core.py` + `__init__.py` | ✅ 41 lines | ❌ deleted | Legacy placeholder removed |
> | `src/memory/store.py` + `__init__.py` | ✅ 13 lines | ❌ deleted | Placeholder removed |
> | `tests/test_ingestion_pipeline.py` | ✅ 81 lines | ❌ deleted | Pipeline test removed |
>
> **Everything below describes the committed (HEAD) state unless explicitly noted as "working tree".**

---

## Executive Summary

| Phase | Completion (HEAD) | Key Gap |
| ----- | ----------------- | ------- |
| **Week 1** — Design & Data | **100%** | All tasks complete |
| **Week 2** — Ingestion & KB | **100%** | Orchestrator + entity extractor are committed (audit previously missed them) |
| **Week 3** — Report Agent | **~95%** | ReAct loop, 3 tools, citation prompt, 30-test suite all committed; not yet verified end-to-end against a live LLM |
| **Week 4** — Concern Engine | **~5%** | Only config thresholds exist; no detection rules, no severity scoring |
| **Week 5** — MCP & Guardrails | **~2%** | Only an unused `audit_log` table; no server, no guardrails |
| **Week 6** — Packaging | **0%** | No runner, no outputs, no report |

> [!IMPORTANT]
> **The project is materially further along than a quick `ls` of the working tree suggests.** Weeks 1–3 (foundation + the entire intelligence/agent layer) are implemented and committed with good test coverage. The remaining work is the **Concern Engine (Week 4)**, the **delivery layer (Week 5: MCP + guardrails)**, and **packaging (Week 6)** — all genuinely not started. The immediate blocker is mechanical: **restore the deleted working-tree files** so the committed code can run.

---

## Week 1 — Design & Data Prep

| #   | Task | Status | Evidence |
| --- | ---- | ------ | -------- |
| 1.1 | **SQLite schema** (entities, snapshots, backlinks, sync_log) | ✅ Done | [init_db.py](src/storage/init_db.py) — 4 plan tables + `audit_log` + 3 indexes (97 lines) |
| 1.1 | **ChromaDB schema** (3 collections) | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `confluence_chunks`, `meeting_chunks`, `jira_descriptions` |
| 1.2 | **Jira synthetic data** | ✅ Done | [jira_synthetic_AIP.json](data/jira/jira_synthetic_AIP.json) — **1000 issues**, every issue carries a `_ground_truth` field |
| 1.2 | **Confluence synthetic data** (JSON + metadata) | ✅ Done | [confluence_synthetic.json](data/confluence/confluence_synthetic.json) — **217 pages**, with `linked_jira_epics` |
| 1.2 | **Meeting Notes** | ✅ Done | [meeting_notes.json](data/meeting_notes/meeting_notes.json) — **5 meetings** with action items + ground truth |
| 1.2 | **Inject 4 anomaly types** | ✅ Done | AIP-30 (cross-source conflict), AIP-37 (stalled), AIP-53 (deadline), AIP-67 (blocker) |
| 1.3 | **Python repo structure** (src/, data/, tests/) | ✅ Done | Layout present with `pyproject.toml` |
| 1.3 | **Linter config** (flake8 + black) | ✅ Done | `.flake8` + `[tool.black]` in `pyproject.toml` (line-length 88) |
| 1.3 | **config.py with thresholds** | ✅ Done | [config.py](config.py) — all 4 thresholds + `MAX_AGENT_ITERATIONS`, OpenAI settings, chunk params, `validate_config()` |
| 1.3 | **Basic unit test** (CI green) | ✅ Done | 9 test files; **44 pass / 1 fails** on a stale assertion (see Test Suite) |

---

## Week 2 — Ingestion Pipeline & Knowledge Base

| #   | Task | Status | Evidence |
| --- | ---- | ------ | -------- |
| 2.1 | **Jira connector** | ✅ Done | [jira_connector.py](src/ingestion/jira_connector.py) — loads JSON, normalizes, extracts ADF text (86 lines) |
| 2.1 | **Confluence connector** | ✅ Done | [confluence_connector.py](src/ingestion/confluence_connector.py) — folder/JSON loader, validation, normalization (181 lines) |
| 2.1 | **Meeting Notes connector** | ✅ Done | [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) — JSON + plain text, issue-key extraction (343 lines) |
| 2.2 | **Route 1 → ChromaDB** (chunking) | ✅ Done | [chroma_store.py](src/storage/chroma_store.py) — `add_confluence_chunks()` (MarkdownHeaderTextSplitter), `add_meeting_chunks()` (RecursiveCharacterTextSplitter), `add_jira_description()` (whole) |
| 2.2 | **Route 2 → SQLite** (entity upsert) | ✅ Done | [sqlite_store.py](src/storage/sqlite_store.py) — `upsert_entity()`, `bulk_upsert()`, `save_snapshot()`, `query_entity()`, `insert_backlinks()`, `update_sync_log()` (203 lines) |
| 2.2 | **Entity extraction** (regex + rules) | ✅ Done | [entity_extractor.py](src/ingestion/entity_extractor.py) — `EntityExtractor.extract()` produces entities + backlinks (linked epics, inline `AIP-\d+` mentions, action items) across all 3 sources (91 lines) |
| 2.3 | **Day-over-day diff** | ✅ Done | [sqlite_store.py:134-154](src/storage/sqlite_store.py#L134-L154) — `get_daily_diff()` with snapshot self-join on `DATE(?, '-1 day')` |
| — | **Ingestion orchestrator** (run_pipeline.py) | ✅ Done *(HEAD)* | `src/ingestion/run_pipeline.py` (113 lines, committed) — `init_db` → load 3 connectors → `EntityExtractor` → SQLite (bulk upsert, snapshots, backlinks, sync_log) → ChromaDB routing, with argparse CLI. **⚠️ deleted in working tree** |

---

## Week 3 — Report Agent (OpenAI SDK + ReAct Loop)

> Previously reported as **0% / missing**. This was incorrect — the entire layer is **committed in HEAD** (only deleted in the working tree).

| #   | Task | Status | Evidence |
| --- | ---- | ------ | -------- |
| 3.1 | **Tool definitions** (query_chroma, query_sqlite, get_daily_diff) | ✅ Done *(HEAD)* | `src/tools/registry.py` (276 lines) — 3 OpenAI function schemas + `dispatch_tool()` routing to Chroma/SQLite, with metadata→`source_id` normalization for citations. **⚠️ deleted in working tree** |
| 3.2 | **ReAct loop** (OpenAI SDK) | ✅ Done *(HEAD)* | `src/agents/report_agent.py` (326 lines) — `run_report_agent()` think-act loop, `tool_choice="auto"`, `max_iterations` safety net, lazy store injection. **⚠️ deleted in working tree** |
| 3.3 | **Citation enforcement** (system prompt) | ✅ Done *(HEAD)* | `SYSTEM_PROMPT` mandates `[source_id]` after every claim, forbids unsourced claims, defines a 5-section Markdown report format |
| — | **ReportAgent class + CLI** | ✅ Done *(HEAD)* | `ReportAgent` wrapper + `from_cli()` + `__main__` (`--date/--query/--model`) for `run_agent.sh` wiring |
| — | **End-to-end verification vs live LLM** | ⚠️ Unverified | Logic is covered by mocked tests; no confirmed real run producing a cited `report.md`. Requires a valid `OPENAI_API_KEY` (config points at `OPENAI_BASE_URL`, default model `gpt-4o-mini`) |

---

## Week 4 — Concern Engine (Rule-based + LLM)

| #   | Task | Status | Evidence |
| --- | ---- | ------ | -------- |
| 4.1 | **Config thresholds** | ✅ Done | [config.py](config.py) — `STALLED_DAYS`, `DEADLINE_RISK_DAYS`, `BLOCKER_OPEN_DAYS`, `CONFLICT_WINDOW_H` |
| 4.2 | **Rule 1: Stalled task** (SQL) | ❌ Not started | No implementation |
| 4.2 | **Rule 2: Deadline risk** (SQL) | ❌ Not started | No implementation |
| 4.2 | **Rule 3: Unresolved blocker** (SQL) | ❌ Not started | No implementation |
| 4.3 | **Cross-source conflict** (rule filter → LLM verify) | ❌ Not started | No implementation |
| 4.4 | **Severity scoring** | ❌ Not started | No `score_severity()` |
| — | **concern_engine.py** | ❌ Not started | Not in HEAD or on disk. [main.py](src/main.py) imports `agents.concern_engine.ConcernEngine` which does not exist |

---

## Week 5 — MCP Server & Guardrails

| #   | Task | Status | Evidence |
| --- | ---- | ------ | -------- |
| 5.1 | **MCP Server** (FastAPI + 3 endpoints) | ❌ Not started | No `mcp/` package. [main.py](src/main.py) imports `mcp.server.MCPServer` which does not exist |
| 5.2 | **Input guardrail** (sanitize_input) | ❌ Not started | No `guardrail/` package. [main.py](src/main.py) imports `guardrail.sanitizer.Sanitizer` which does not exist |
| 5.2 | **Output guardrail** (sanitize_output) | ❌ Not started | Same |
| 5.2 | **Audit log** (SQLite) | ⚠️ Partial | `audit_log` table exists in [init_db.py:62-69](src/storage/init_db.py#L62-L69), but no code writes to it |
| 5.3 | **End-to-end test** (curl) | ❌ Not started | No API to test |

---

## Week 6 — Packaging & Report

| #   | Task | Status | Evidence |
| --- | ---- | ------ | -------- |
| 6.1 | **run_agent.sh** (one-command runner) | ❌ Not started | File does not exist |
| 6.2 | **V1**: e2e no crash | ❌ Not started | Blocked — needs Week 4–6 + working-tree restore |
| 6.2 | **V2**: report.md with 5+ citations | ⚠️ Blocked | Report Agent exists (HEAD) but no verified run; no `output/` dir |
| 6.2 | **V3**: all 4 anomaly types detected | ❌ Not started | No concern engine |
| 6.2 | **V4**: Precision/Recall ≥ 80% | ❌ Not started | No concern engine |
| 6.2 | **V5**: Guardrail blocks 3+ injections | ❌ Not started | No guardrails |
| 6.2 | **V6**: Live demo | ❌ Not started | — |
| 6.3 | **Tech Report** | ❌ Not started | — |

---

## File-Level Implementation Quality

### ✅ Production-Quality Files (committed)

| File | Lines | Quality Notes |
| ---- | ----- | ------------- |
| [jira_connector.py](src/ingestion/jira_connector.py) | 86 | Clean, ADF text extraction, typed |
| [confluence_connector.py](src/ingestion/confluence_connector.py) | 181 | Robust validation, logging, docstrings |
| [meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py) | 343 | JSON + plain text, issue-key regex, well-documented |
| [entity_extractor.py](src/ingestion/entity_extractor.py) | 91 | Per-source routing, backlink extraction, regex `[A-Z]+-\d+` |
| [chroma_store.py](src/storage/chroma_store.py) | 250 | 3 collections, correct splitters/params, query method |
| [sqlite_store.py](src/storage/sqlite_store.py) | 203 | Context manager, bulk upsert, snapshot + diff, backlinks, typed |
| [init_db.py](src/storage/init_db.py) | 97 | 5 tables + 3 indexes, CLI |
| [config.py](config.py) | 29 | All thresholds + agent/OpenAI settings + `validate_config()` |
| `src/agents/report_agent.py` *(HEAD only)* | 326 | ReAct loop, citation system prompt, class wrapper, CLI — **deleted in working tree** |
| `src/tools/registry.py` *(HEAD only)* | 276 | 3 tool schemas + dispatcher with citation normalization — **deleted in working tree** |
| `src/ingestion/run_pipeline.py` *(HEAD only)* | 113 | End-to-end ingestion orchestrator with CLI — **deleted in working tree** |

### ⚠️ Stub / Problem Files

| File | Lines | Issue |
| ---- | ----- | ----- |
| [main.py](src/main.py) | 39 | Wiring stub — imports `agents.concern_engine`, `guardrail.sanitizer`, `guardrail.audit_log`, `mcp.server` (none exist) so it crashes on import. Also uses bare `from ingestion…` / `from agents…` imports with no `sys.path` setup, so `python src/main.py` would fail even for modules that do exist |
| `src/agent/core.py` *(HEAD only)* | 41 | Intentional deprecated placeholder (`CoreAgent`, emits `DeprecationWarning`) superseded by `agents/report_agent.py` |
| `src/memory/store.py` *(HEAD only)* | 13 | Trivial in-memory `MemoryStore` placeholder, unused |

### ❌ Genuinely Missing (in plan, not in HEAD or disk)

| Expected File | Plan Reference |
| ------------- | -------------- |
| `src/agents/concern_engine.py` | Week 4 §4.2–4.4 |
| `src/guardrail/sanitizer.py` | Week 5 §5.2 |
| `src/guardrail/audit_log.py` | Week 5 §5.2 |
| `src/mcp/server.py` | Week 5 §5.1 |
| `run_agent.sh` | Week 6 §6.1 |
| `output/report.md`, `output/concerns.json` | Week 6 generated outputs |

---

## Test Suite Status

Run on the **current working tree** (`python -m pytest`): **44 passed, 1 failed, 1 collection error**.

| Test File | Result | Notes |
| --------- | ------ | ----- |
| [test_chunking.py](tests/test_chunking.py) | ✅ Pass | Covers all 3 collection types |
| [test_confluence_connector.py](tests/test_confluence_connector.py) | ✅ Pass | Validation, normalization, edge cases |
| [test_jira_connector.py](tests/test_jira_connector.py) | ✅ Pass | Load + normalize + ground-truth stripping |
| [test_meeting_notes_connector.py](tests/test_meeting_notes_connector.py) | ⚠️ 1 fail | `test_load_meeting_notes_json` asserts `len == 4` but data now has **5 meetings** — stale assertion, update to 5 |
| [test_entity_extractor.py](tests/test_entity_extractor.py) | ✅ Pass | Jira/Confluence/Meeting extraction paths |
| [test_ingestion_integration.py](tests/test_ingestion_integration.py) | ✅ Pass | JiraConnector → SQLiteStore round-trip |
| [test_agent.py](tests/test_agent.py) | ⛔ Collection error | 30 well-written Week-3 tests (tool schemas, `dispatch_tool`, mocked ReAct loop, system prompt). **Cannot import** — `ModuleNotFoundError: No module named 'agents'` because `src/agents/` is deleted in the working tree |
| `tests/test_ingestion_pipeline.py` *(HEAD only)* | n/a | 81-line pipeline test, deleted in working tree |

---

## Risks & Follow-ups

1. **Restore the working tree (blocker).** `git restore .` (or `git checkout -- .`) brings back `src/agents/`, `src/tools/`, `run_pipeline.py`, etc. Until then the agent and orchestrator cannot run and `test_agent.py` cannot collect. Decide whether these deletions were intentional — if so, commit them; if not, restore.
2. **🔑 Hardcoded secret in tests.** [test_agent.py](tests/test_agent.py#L153) asserts an exact `api_key` value `sk-…` and `base_url` `https://ckey.vn/v1`. This (a) leaks a live-looking credential into version control and (b) couples the test to one developer's `.env`, so it will fail for anyone else. Replace with a monkeypatched/dummy key and rotate the exposed secret.
3. **Mixed import conventions.** `run_pipeline.py` uses `from src.ingestion…`; `report_agent.py`/`registry.py` use `from storage…`/`from tools…`; `main.py` uses `from ingestion…`. Tests work only because [conftest.py](tests/conftest.py) puts both repo root and `src/` on `sys.path`. Standardize on one scheme to avoid entry-point-dependent `ImportError`s.
4. **`main.py` is dead on arrival.** It imports four modules that do not exist; it needs a rewrite once Weeks 4–5 land (or should defer those imports).
5. **Agent path not yet exercised end-to-end.** All Week-3 confidence is from mocked tests. A real `report.md` with ≥5 valid citations (V2) is still unproven.

---

## What to Build Next (to follow the plan)

1. **Restore working tree** + commit or discard the pending deletions intentionally.
2. **Week 4 — `concern_engine.py`:** 3 deterministic SQL rules (stalled / deadline / blocker), cross-source conflict (rule filter → LLM verify), `score_severity()`, CLI emitting `concerns.json`.
3. **Week 5 — delivery layer:** FastAPI MCP server (`/ingest`, `/report`, `/concerns`) with `X-API-Key`; input/output guardrails writing to the existing `audit_log` table.
4. **Week 6 — packaging:** `run_agent.sh`, `output/` artifacts, and the verification checklist (V1–V6).
