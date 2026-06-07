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
| Model config | **`OPENAI_MODEL` from `.env`** | Defaults to `gpt-4o-mini` on `api.openai.com`; set `OPENAI_BASE_URL` to target an OpenAI-compatible proxy (e.g. ckey.vn) without code changes. |
| Report grounding | **Concern Engine feeds the Report Agent** | The narrative "Concerns" section is anchored to deterministic detections (not the LLM's free exploration), then enriched with Confluence/Meeting context. |

**Why not LangChain:** for a solo build, controlling every tool call mattered more
than framework conveniences. The ReAct loop is `run_report_agent()` — a single
readable function with explicit retry and an iteration cap.

---

## 3. The report (what's in it)

`output/report.md` is a Markdown daily report. The output language follows
`REPORT_LANG` (default Vietnamese), it **leads with a prioritised action block**,
and every claim is cited:

1. **Priority Actions Today** — a one-line risk-count summary + the top ≤5 highest-severity, decision-ready items (chronic backlog excluded).
2. **Overview** — 2-3 sentence health summary.
3. **Changes Today** — day-over-day status/assignee moves (`get_daily_diff`).
4. **Concerns** — at-risk tasks (stalled / deadline / blocker / cross-source), grounded in the Concern Engine; low-priority chronic backlog is summarised as a count, not listed.
5. **Next Actions** — owner-tagged recommendations.

### Example (real `gpt-4o-mini` run, 2025-05-30, Vietnamese default, abridged)

```markdown
## Cần xử lý hôm nay
Tổng quan rủi ro: 36 blocker · 66 quá hạn/sắp hết hạn · 139 trì trệ · 1 xung đột nguồn.

1. **FLINK-40** — severity 5 — Deadline quá hạn 2 ngày, status 'Reopened', phụ trách Jack Jackson [FLINK-40].
2. **CASSANDRA-46** — severity 5 — Deadline quá hạn 2 ngày, status 'Reopened', phụ trách Duc Anh [CASSANDRA-46].
   …

## Rủi ro
- **Trì trệ**: 139 task đang trì trệ (gộp; các mục kinh niên không liệt kê từng cái).
- **Blocker**: KAFKA-64 mở 11 ngày, ảnh hưởng 4 task [KAFKA-64].
- **Xung đột nguồn**: AIP-5 ghi Done trong Jira nhưng tài liệu khác vẫn pending/review [AIP-5].
```

That run finished in **3 ReAct iterations** and produced a fully-cited Vietnamese
report led by the priority block. (Citation count varies by run; an earlier
English run produced 24.)

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
> the `stalled` rule still *surfaces* every genuinely-stale task. Rather than overfit
> the detector, renovation #1 keeps all stalled tasks but **tiers their severity**: the
> 35 planted anomalies carry a `needs-review` label → severity 4 (actionable), while
> long-idle/unlabelled tasks are marked **chronic** → severity 2 and kept out of the
> report's top block. The noise is de-prioritised, not hidden.

**Guardrails** (`tests/test_guardrail.py`, adversarial):

| Metric | Value |
| --- | --- |
| Injection payloads blocked | 4/4 → `[FILTERED]` |
| Benign inputs filtered (false positives) | **0/3** |
| Output secret redaction | `sk-…` keys, bearer tokens, PEM private keys → `[REDACTED]` |

**Test suite:** 95 passed (the stale meeting-notes count is fixed; MCP-server tests
self-skip when `fastapi` is absent), plus 17 in-file sanitizer tests
(`pytest src/guardrail/sanitizer.py`).

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
| V6 | Live demo | ✅ live `gpt-4o-mini` run produced the report above |

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

**Shipped since the first draft of this report:**

- **MCP server (Week 5):** FastAPI front-end (`/ingest`, `/report`, `/concerns`,
  `X-API-Key`) with the guardrails wired in — implemented and live-verified.
- **Report prioritisation (renovation #1):** a "Priority Actions Today" block + a
  one-line risk-count summary, and a tiered `stalled` rule that uses the
  `needs-review` signal so chronic backlog no longer crowds the top. Output language
  is configurable (`REPORT_LANG`, default Vietnamese).

**Still open:**

- **Cross-source recall:** generate Meeting Notes that reference the Jira
  cross-source anomalies so that rule has evidence to detect more than 1.
- **Real day-over-day history:** run on a schedule so the diff uses genuine prior
  snapshots instead of the demo seed.
- **Stalled precision at full prevalence:** measure V4 against all normals.
- **Delivery:** push the daily report to where PMs work (email / Slack / Teams).
