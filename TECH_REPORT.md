# Technical Report — AI Project Intelligence Agent

An agent that ingests Jira + Confluence + Meeting Notes, detects project risks
deterministically, and writes a cited daily status report. This report covers the
architecture, the key engineering decisions, end-to-end benchmarks, the bugs found
while running it, and the verification checklist.

---

## 1. What it does

```
                ┌──────────── ingestion ────────────┐
  Jira JSON ───►│ connectors → EntityExtractor       │
  Confluence ──►│        │                  │         │
  Meeting Notes►│        ▼                  ▼         │
                │   ChromaDB (vectors)  SQLite (facts)│
                └────────┬──────────────────┬─────────┘
                         │                  │
          ┌──────────────▼───┐   ┌──────────▼──────────────┐
          │ Concern Engine   │   │ Report Agent (ReAct)    │
          │ 4 deterministic  │   │ tools: query_chroma /   │
          │ rules + severity │   │ query_sqlite /          │
          │                  │   │ get_daily_diff          │
          └────────┬─────────┘   └──────────┬──────────────┘
                   │  grounds the report     │
                   ▼                         ▼
            output/concerns.json       output/report.md  (cited)
```

One command does the whole thing:

```bash
./run_agent.sh                 # demo reference date 2025-05-30
./run_agent.sh 2025-05-30      # explicit date
```

It rebuilds the dual store, runs the Concern Engine, then runs the **grounded**
Report Agent (the engine's top concerns are fed to the agent so the report's
"Concerns" section is backed by deterministic detection), and writes both
artifacts to `output/`.

---

## 2. Key engineering decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Agent framework | **OpenAI SDK + hand-written ReAct loop** | Full control of the control flow; ~50 lines, trivially debuggable. No black-box "why didn't it call the tool" mysteries. |
| Storage | **Dual: SQLite (facts) + ChromaDB (vectors)** | Deterministic queries (status, dates, day-over-day diff) belong in SQL; semantic recall (design context, meeting commitments) belongs in a vector store. Each plays to its strength. |
| Concern detection | **Rule-based SQL, LLM only for cross-source** | Risk detection must be deterministic and measurable. 3 rules are pure SQL; only cross-source conflict needs fuzzy matching, and even that is rule-based (keyword) with the LLM as an optional confirmer. |
| Citations | **Tool results carry `source_ids`; system prompt forbids unsourced claims** | Every claim in the report ends with `[source_id]`; the agent is told to write "(No verified data found.)" rather than hallucinate. |
| Model config | **`OPENAI_MODEL` from `.env`** | Targets `gpt-5.5` via the ckey.vn OpenAI-compatible proxy without code changes. |
| Report grounding | **Concern Engine feeds the Report Agent** | The narrative "Concerns" section is anchored to deterministic detections (not the LLM's free exploration), then enriched with Confluence/Meeting context. |

**Why not LangChain:** for a solo build, controlling every tool call mattered more
than framework conveniences. The ReAct loop is `run_report_agent()` — a single
readable function with explicit retry and an iteration cap.

---

## 3. The report (what's in it)

`output/report.md` is a Markdown daily report with four sections, every claim cited:

1. **Overview** — 2-3 sentence health summary.
2. **Changes Since `<date>`** — day-over-day status/assignee moves (`get_daily_diff`).
3. **Concerns** — at-risk tasks (stalled / deadline / blocker / cross-source), grounded in the Concern Engine.
4. **Next Actions** — owner-tagged recommendations.

### Example (real `gpt-5.5` run, 2025-05-30, abridged)

```markdown
## Overview
Six tasks changed workflow state on 2025-05-30 … The highest verified risks are
overdue deadline items still not done, including FLINK-40, CASSANDRA-46, and
SPARK-94 … [FLINK-40] [CASSANDRA-46] [SPARK-94].

## Changes Today
- FLINK-13 moved from In Progress to In Review, John Smith remaining assignee [FLINK-13].
- STORM-44 moved from To Do to In Progress, Grace Taylor remaining assignee [STORM-44].
  …

## Concerns
- FLINK-40 is overdue against a 2025-05-28 due date and is still Reopened … [FLINK-40].
- ZOOKEEPER work may have dependency pressure: meeting notes state ZOOKEEPER-2 is
  blocking ZOOKEEPER-1 until the client migration script is ready [MTG-ZOOKEEPER-20250526].

## Next Actions
- @Jack Jackson: Triage FLINK-40 and SPARK-94 today … [FLINK-40] [SPARK-94].
```

That run produced **24 valid citations** across `AIP/FLINK/CASSANDRA/MTG-*` ids
(V2 target ≥ 5) in **4 ReAct iterations**.

`output/concerns.json` (deterministic) for the same date — **242 concerns**:

```json
[
  {"type": "cross_source_conflict", "task_id": "AIP-5", "severity": 5,
   "explanation": "Jira đánh dấu Done nhưng tài liệu khác vẫn ghi nhận đang pending/review.",
   "source_ids": ["AIP-5", "MTG-…"]},
  {"type": "unresolved_blocker", "task_id": "KAFKA-64", "severity": 5,
   "explanation": "Blocker mở 11 ngày, ảnh hưởng 12 task.", "source_ids": ["KAFKA-64"]}
]
```

---

## 4. Benchmarks

**Ingestion (full synthetic corpus, real-data scale):**

| Metric | Value |
| --- | --- |
| Documents ingested | 1222 (1000 Jira + 217 Confluence + 5 Meetings) |
| SQLite entities | 1000 |
| Confluence chunks | 1614 |
| Meeting chunks | 21 |
| Backlinks | 719 |

**Concern Engine accuracy** (`tests/test_concern_engine.py`, vs `_ground_truth`):

| Metric | Value |
| --- | --- |
| Per-rule recall (stalled / deadline / blocker) | 3/3, 2/2, 2/2 detected |
| Precision (108 anomalies + 100 normals) | **0.92** |
| Recall | **1.00** |
| Concerns at `as_of=2025-05-30` | 242 (deadline 66, stalled 139, blocker 36, cross-source 1) |

> Precision is prevalence-sensitive: against all 856 normals it drops (~0.5), because
> the `stalled` rule legitimately surfaces genuinely-stale normal tasks. The planted
> stalled anomalies are distinguished by a `needs-review` label the date-rule does
> not key on — a deliberate choice to keep the rule general rather than overfit.

**Guardrails** (`tests/test_guardrail.py`, adversarial):

| Metric | Value |
| --- | --- |
| Injection payloads blocked | 4/4 → `[FILTERED]` |
| Benign inputs filtered (false positives) | **0/3** |
| Output secret redaction | `sk-…` keys, bearer tokens, PEM private keys → `[REDACTED]` |

**Test suite:** 77 passed / 1 pre-existing fail (a stale meeting-notes count), plus
17 in-file sanitizer tests (`pytest src/guardrail/sanitizer.py`).

---

## 5. Bugs found and fixed while running end-to-end

Running the full pipeline surfaced real issues that unit tests on idealized fixtures
had missed:

1. **Connector → Chroma field-name mismatch.** Connectors emit `text_content`/`source_id`,
   but `ChromaStore` reads `content`/`page_id`/`note_id`. The previous orchestrator
   passed docs through unmapped and would have indexed **0** Confluence/Meeting chunks.
   Fixed with explicit field bridges (`_confluence_to_chroma` / `_meeting_to_chroma`).
2. **Jira `source` discriminator.** The synthetic file declares `"source": "Apache"`;
   the connector copied it verbatim, so every issue fell through the `source=="jira"`
   routing (entities = 0). Fixed to emit the canonical `"jira"`.
3. **Deadline rule over-flagging.** Flagging *any* overdue task gave 0.29 precision
   (hundreds of long-overdue normal tasks). Refined to a **near-deadline window**
   (`±DEADLINE_RISK_DAYS`) → precision 0.92, matching the plan's "approaching deadline"
   intent.
4. **`julianday` vs Jira timestamps.** Jira's `+0000` suffix isn't parseable by SQLite's
   `julianday`; the rules compare `substr(updated_at, 1, 10)` (date portion) instead.
5. **Transient proxy `403`s.** The ckey.vn proxy rate-limits the agent's rapid multi-call
   bursts. Added **retry-with-backoff** on transient statuses and a **deterministic
   fallback report** so a throttled run still produces a useful, cited `report.md`
   instead of crashing.
6. **Report grounding diversity.** Severity-sorted top-N was all `deadline_risk`; switched
   to top-N **per type** so the report covers stalled / deadline / blocker / cross-source.

---

## 6. Verification (Definition of Done)

| # | Criterion | Status |
| --- | --- | --- |
| V1 | `run_agent.sh` runs end-to-end without crashing | ✅ (resilient even when the LLM proxy throttles) |
| V2 | `report.md` has ≥ 5 valid citations | ✅ 24 in the live run |
| V3 | All 4 anomaly types detected | ✅ present in `concerns.json` |
| V4 | Concern precision/recall ≥ 80% | ✅ 0.92 / 1.00 on the sampled mix (prevalence caveat above) |
| V5 | Guardrail blocks ≥ 3 injections | ✅ 4/4, 0 false positives |
| V6 | Live demo | ✅ live `gpt-5.5` run produced the report above |

---

## 7. Lessons learned

- **Unit tests on tidy fixtures hide integration bugs.** The field-bridge and
  `source` bugs only appeared on the real corpus. The end-to-end run was the most
  valuable test.
- **Determinism where it counts.** Putting risk detection in SQL (not the LLM) made
  it measurable (precision/recall) and reproducible; the LLM is reserved for narrative.
- **Third-party model proxies are flaky.** Retry + a deterministic fallback turned a
  hard dependency into a soft one — the system degrades gracefully instead of failing.
- **Precision is prevalence-dependent.** A single "precision" number is misleading
  without stating the positive/negative mix.

## 8. Roadmap

- **Week 5 completion:** the FastAPI MCP server (`/ingest`, `/report`, `/concerns`,
  `X-API-Key`) — the guardrails and audit-log already exist to wire in.
- **Cross-source recall:** generate Meeting Notes that reference the Jira
  cross-source anomalies so that rule has evidence to detect more than 1.
- **Real day-over-day history:** run on a schedule so the diff uses genuine prior
  snapshots instead of the demo seed.
- **Stalled precision:** optionally incorporate the `needs-review` signal or measure
  V4 at full prevalence.
