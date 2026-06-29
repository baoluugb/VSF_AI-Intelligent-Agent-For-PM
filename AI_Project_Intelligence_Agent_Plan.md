# KẾ HOẠCH TRIỂN KHAI

**Phiên bản: v4.0** — Tái cấu trúc 6 tuần theo **project charter chính thức** (bảng yêu cầu từ PM/mentor)

> **Thay đổi chính so với v3:** (1) Tái cấu trúc 6 tuần bám đúng charter — Jira ingestion + snapshot + entity extraction về **Tuần 1**; day-over-day diff về **Tuần 3**; MCP + guardrail thành **(Stretch) ở Tuần 5**. (2) Bổ sung mục **Sản phẩm bàn giao** và **Tiêu chí hoàn thành** lấy nguyên văn từ charter. (3) Thêm dòng **Trạng thái thực tế** cho mỗi tuần (✅ đã làm / ngoài kế hoạch). Toàn bộ chi tiết kỹ thuật của v3 được giữ nguyên, chỉ sắp xếp lại theo tuần.

---

## 📦 SẢN PHẨM BÀN GIAO (theo charter)

- **Ingestion pipeline 3 nguồn** → normalized docs + entity extraction
- **Knowledge base**
- **Report agent**: tool-using, tự quyết investigation steps, citation
- **Concern engine**: stalled / blocker / deadline + cross-source conflict
- **MCP server** + guardrail
- **Report + demo**

## ✅ TIÊU CHÍ HOÀN THÀNH (theo charter)

- Ingest đầy đủ 3 nguồn vào knowledge base; pipeline có **unit test** và **reproducible**.
- Knowledge base **truy hồi chính xác toàn bộ mention** của một entity qua backlink.
- Report agent sinh báo cáo với **mỗi mục có trích dẫn nguồn kiểm chứng được**; **day-over-day diff chính xác**.
- Concern engine: **rule-based hoạt động đúng**.
- Toàn hệ thống chạy **end-to-end**; có **README**, **tech report** nêu rõ thiết kế / kết quả / hạn chế; **demo hoàn chỉnh**.
- **MCP verify được trạng thái live**; **guardrail chặn được tập test prompt injection**.

---

## 🛠️ TECH STACK ĐÃ CHỐT

| Thành phần             | Quyết định                                             | Lý do                                                             |
| ---------------------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| **LLM Agent**          | OpenAI SDK + Function Calling (tự viết ReAct loop)     | Kiểm soát 100% control flow, dễ debug hơn LangChain               |
| **Vector Storage**     | ChromaDB                                               | Lưu text chunks + embeddings để semantic search                   |
| **Structured Storage** | SQLite                                                 | Lưu Entity, Metadata, Snapshot, Day-over-day diff — deterministic |
| **Data Sources**       | Jira JSON + Confluence JSON + Meeting Notes plain text | Đã có bộ synthetic với ground truth                               |
| **Cadence**            | Daily batch                                            | Chạy một lần mỗi ngày, sinh `report.md`                           |

> **Lý do bỏ LangChain:** LangChain là "hộp đen" khổng lồ — khi Agent không chịu gọi Tool, debug mất hàng giờ mà không rõ lỗi ở layer nào. Với 6 tuần solo dev, kiểm soát từng dòng code quan trọng hơn dùng framework. ReAct loop viết thẳng bằng OpenAI SDK chỉ ~50 dòng và hoàn toàn trong tầm kiểm soát.

---

## 🗓️ Tuần 1: Thiết Kế & Nền Tảng Dữ Liệu (Design + Jira Ingestion)

_Mục tiêu (charter): Viết design doc (kiến trúc, data model, phương pháp đánh giá) trình mentor review; dựng 3 nguồn dữ liệu synthetic (Jira inject sẵn các trường hợp bất thường làm ground truth); triển khai ingestion cho Jira, snapshot vào vault, và entity extraction._

### 1.1 Design Doc & Schema (Kiến Trúc Kép)

Định nghĩa cấu trúc lưu trữ kép:

**SQLite** — lưu dữ liệu có cấu trúc, query deterministic:

- Bảng `entities`: `task_id`, `assignee`, `status`, `priority`, `due_date`, `source`, `updated_at`
- Bảng `snapshots`: trạng thái entity theo từng ngày (dùng cho day-over-day diff)
- Bảng `backlinks`: liên kết cross-source giữa Jira ticket ↔ Confluence page ↔ Meeting note
- Bảng `sync_log`: lưu `last_run_date` để incremental sync

**ChromaDB** — lưu text chunks + embeddings, query semantic:

- Collection `confluence_chunks`: nội dung Confluence đã chunk theo Markdown heading
- Collection `meeting_chunks`: nội dung Meeting Notes đã chunk theo section
- Collection `jira_descriptions`: mô tả Jira (1 ticket = 1 chunk, không cắt thêm)

> **Phương pháp đánh giá (trình mentor):** đo precision/recall của Concern Engine trên bộ `_ground_truth`, citation accuracy của Report Agent, và tỉ lệ guardrail chặn injection — chi tiết tiêu chí ở Tuần 5–6.

### 1.2 Chuẩn bị Dữ liệu (Data Prep) — 3 nguồn synthetic

**Jira:** Dùng bộ synthetic `jira_synthetic_AIP.json` đã generate (đủ 4 loại anomaly với `_ground_truth` label).

**Confluence** — format JSON chuẩn (metadata khớp với Jira để filter chính xác):

```json
{
  "page_id": "CONF-001",
  "title": "Kiến trúc Ingestion Pipeline — Quyết định thiết kế",
  "space": "AIP",
  "author": "Minh Tuan",
  "last_updated": "2025-05-20",
  "status": "current",
  "linked_jira_epics": ["AIP-1"],
  "tags": ["ingestion", "architecture", "decision"],
  "content": "---\ntitle: ...\n\n## Bối cảnh\n...\n\n## Quyết định\n...\n\n## Trạng thái hiện tại\n..."
}
```

> **Tại sao JSON thay vì plain text?** Trường `linked_jira_epics` cho phép ChromaDB filter: _"Chỉ tìm trong pages liên quan đến AIP-1"_ — tốc độ và độ chính xác tăng đáng kể. Trường `status: current | outdated | draft` giúp loại bỏ page cũ khỏi kết quả tìm kiếm.

**Meeting Notes** — plain text có 2 section cố định:

```
date: 2025-05-21
project: AIP
attendees_raw: Minh Tuan, Bao Chau, Duc Anh

[Attendees]
- Minh Tuan (Tech Lead)
- Bao Chau (Backend)
- Duc Anh (Data)

[Action Items]
- AIP-45: Minh Tuan hoàn thiện ingestion pipeline trước 2025-05-24
- AIP-67: Bao Chau review vault schema — đang pending, chưa có update
```

> **Cấy anomaly cross-source conflict:** Một số Meeting Notes sẽ đề cập task "đang pending/review" trong khi Jira đã đánh dấu "Done" — đây là ground truth cho Concern Engine.

Cố tình cấy đủ 4 loại lỗi vào bộ data: Stalled, Deadline Risk, Blocker, Cross-source Conflict.

### 1.3 Jira Ingestion + Entity Extraction + Snapshot vào Vault

Viết connector Jira trả về `normalized_doc` dict theo contract dùng chung cho cả 3 nguồn (Confluence/Meeting connector tái dùng đúng contract này ở Tuần 2):

```python
# Mỗi connector trả về format thống nhất:
{
  "source": "jira" | "confluence" | "meeting_notes",
  "source_id": "AIP-123" | "CONF-001" | "MTG-2025-05-21",
  "title": "...",
  "text_content": "...",   # Đưa vào ChromaDB
  "metadata": { ... },     # Đưa vào SQLite + ChromaDB metadata
}
```

**Entity Extraction** (regex + rule, không cần LLM): trích Task ID, Person, Date từ nội dung Jira.

**Route vào vault cho nguồn Jira:**

- Metadata + status + assignee + due_date → bảng `entities` (SQLite)
- Snapshot trạng thái ngày hôm đó → bảng `snapshots` (SQLite) — nền tảng cho day-over-day diff ở Tuần 3
- Jira description → collection `jira_descriptions` (ChromaDB), push thẳng không chunk

### 1.4 Môi trường & CI

- Setup Python repo với cấu trúc thư mục: `src/`, `data/`, `tests/`, `config/`
- Cấu hình Linter: `flake8` + `black`
- File `config.py` chứa các threshold (xem Tuần 4): `STALLED_DAYS`, `DEADLINE_RISK_DAYS`
- Viết unit test cơ bản để CI pipeline chạy xanh (yêu cầu charter: pipeline có unit test + reproducible)

> **Trạng thái thực tế:** ✅ Đã hoàn thành — `src/storage/init_db.py`, `src/storage/sqlite_store.py`, `src/storage/chroma_store.py`, `src/ingestion/jira_connector.py`, `src/ingestion/entity_extractor.py`. Bộ Jira synthetic `data/jira/jira_synthetic_AIP.json` có 144 anomaly (36 mỗi loại) + label `_ground_truth`.

---

## 🗓️ Tuần 2: Hoàn Thiện Ingestion & Knowledge Base

_Mục tiêu (charter): Bổ sung ingestion cho Confluence và meeting notes; gắn wikilink/backlink giữa các entity; hoàn thiện truy vấn theo link, metadata và keyword trên vault._

### 2.1 Confluence & Meeting Notes Connectors

Viết 2 connector còn lại, trả về cùng `normalized_doc` contract như Tuần 1. Không dùng LangChain Document Loaders — connector tự viết để kiểm soát parsing (ADF, YAML front-matter, section `[Attendees]` / `[Action Items]`).

### 2.2 Chunking & Routing vào ChromaDB

**Route → ChromaDB** (semantic, dùng cho Report Agent):

- Confluence content → chunk theo Markdown heading (`MarkdownHeaderTextSplitter` từ `langchain_text_splitters` — chỉ dùng splitter, không import toàn bộ LangChain)
- Meeting Notes → chunk theo section `[Attendees]` / `[Action Items]` + `RecursiveCharacterTextSplitter`
- Jira description → push thẳng, không chunk (đã thực hiện ở Tuần 1)

**Chunking parameters:**

| Nguồn            | Splitter                       | chunk_size | overlap  | Lý do                                           |
| ---------------- | ------------------------------ | ---------- | -------- | ----------------------------------------------- |
| Confluence       | MarkdownHeaderTextSplitter     | 600 token  | 80 token | Section heading là semantic boundary tự nhiên   |
| Meeting Notes    | RecursiveCharacterTextSplitter | 300 token  | 40 token | Văn bản trơn, ngắn, 2 section ít liên quan nhau |
| Jira description | Không chunk                    | —          | —        | Đã ngắn sau khi extract từ ADF                  |

### 2.3 Wikilink / Backlink giữa các Entity

Entity Extractor sinh backlink từ mention (Jira key trong Confluence/Meeting) và action item → bảng `backlinks` (SQLite). Đây là nền tảng cho tiêu chí charter: _"Knowledge base truy hồi chính xác toàn bộ mention của một entity qua backlink."_

### 2.4 Truy vấn Vault: theo Link, Metadata, Keyword

- **Theo link/backlink:** từ một `task_id`, lấy mọi mention cross-source qua bảng `backlinks`
- **Theo metadata:** filter ChromaDB theo `linked_jira_epics`, `source`, `status`
- **Theo keyword:** semantic + keyword search trên các collection ChromaDB

> **Trạng thái thực tế:** ✅ Đã hoàn thành — `src/ingestion/confluence_connector.py`, `src/ingestion/meeting_notes_connector.py`, `src/storage/chroma_store.py` (3 collection, filter epic/source/status), bảng `backlinks` đã populate. _Hạn chế hiện tại:_ backlink đã lưu đầy đủ nhưng **chưa được surface trong report UI** (Report Agent chưa query bảng backlinks) — để dành cho tính năng "related tasks".

---

## 🗓️ Tuần 3: Report Agent (OpenAI SDK + ReAct Loop)

_Mục tiêu (charter): Xây dựng các tool deterministic, tách biệt và test độc lập; triển khai agent tự điều tra và sinh báo cáo có trích dẫn; bổ sung day-over-day diff dựa trên snapshot._

### 3.1 Day-over-day Diff (dựa trên snapshot)

Query SQL thuần, so sánh snapshot hôm nay vs hôm qua (snapshot được ghi từ Tuần 1):

```sql
SELECT
  today.task_id,
  yesterday.status AS status_yesterday,
  today.status     AS status_today,
  yesterday.assignee AS assignee_yesterday,
  today.assignee   AS assignee_today
FROM snapshots today
JOIN snapshots yesterday
  ON today.task_id = yesterday.task_id
 AND today.snapshot_date  = DATE('now')
 AND yesterday.snapshot_date = DATE('now', '-1 day')
WHERE today.status != yesterday.status
   OR today.assignee != yesterday.assignee;
```

### 3.2 Xây dựng Tools (OpenAI Function Calling schema) — deterministic, test độc lập

Định nghĩa các tool dưới dạng JSON schema cho OpenAI API. Mỗi tool deterministic và có test riêng:

```python
TOOLS = [
  {
    "type": "function",
    "function": {
      "name": "query_chroma",
      "description": "Tìm kiếm ngữ nghĩa trong Confluence và Meeting Notes",
      "parameters": {
        "type": "object",
        "properties": {
          "query":        {"type": "string"},
          "source_filter":{"type": "string", "enum": ["confluence","meeting_notes","all"]},
          "epic_filter":  {"type": "string", "description": "Lọc theo linked_jira_epics, VD: AIP-1"}
        },
        "required": ["query"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "query_sqlite",
      "description": "Lấy chính xác trạng thái task từ SQLite",
      "parameters": {
        "type": "object",
        "properties": {
          "entity_id": {"type": "string", "description": "VD: AIP-123"}
        },
        "required": ["entity_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "get_daily_diff",
      "description": "Lấy danh sách thay đổi so với ngày hôm qua",
      "parameters": {
        "type": "object",
        "properties": {
          "date": {"type": "string", "description": "ISO date, VD: 2025-05-21"}
        },
        "required": ["date"]
      }
    }
  }
]
```

### 3.3 ReAct Loop (tự viết ~50 dòng, không dùng LangChain)

```python
import openai, json

def run_report_agent(user_query: str, max_iterations: int = 5) -> str:
    client = openai.OpenAI()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_query}
    ]

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"
        )
        msg = response.choices[0].message

        # Không còn tool call → Agent đã có đủ thông tin, trả về
        if not msg.tool_calls:
            return msg.content

        # Thực thi từng tool call, ném kết quả lại cho Agent
        messages.append(msg)
        for tc in msg.tool_calls:
            result = dispatch_tool(tc.function.name,
                                   json.loads(tc.function.arguments))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False)
            })

    return "Đã đạt giới hạn vòng lặp — báo cáo chưa đầy đủ."
```

> **Tại sao max 5 vòng?** Ngăn infinite loop khi Agent không tìm được thông tin. Nếu sau 5 vòng vẫn thiếu, báo cáo sẽ có caveat rõ ràng thay vì hallucinate.

### 3.4 Citation Enforcement

System prompt ép Agent trích dẫn nguồn (tiêu chí charter: mỗi mục có trích dẫn kiểm chứng được):

```
Mọi nhận định (claim) PHẢI kèm [source_id] lấy từ metadata của tool result.
Nếu không có nguồn xác thực → KHÔNG được viết nhận định đó.
Ví dụ đúng:  "Task AIP-45 đang stalled từ 2025-05-18 [AIP-45]"
Ví dụ sai:   "Task AIP-45 có vẻ đang bị chậm"
```

> **Trạng thái thực tế:** ✅ Đã hoàn thành — `src/agents/tools.py` (4 tool: thêm `get_tasks_changed_since` cho diff dài hạn), `src/agents/report_agent.py` (ReAct loop + retry/backoff), `src/agents/report_pipeline.py`. Day-over-day diff tại `src/storage/sqlite_store.py::get_daily_diff()`. Live run gần nhất: **24 citation / 3 vòng lặp**.

---

## 🗓️ Tuần 4: Concern Engine (Rule-based + Cross-source)

_Mục tiêu (charter): Triển khai tầng rule-based (stalled, blocker, deadline); triển khai cross-source conflict qua entity link, có verify live khi cần; thêm severity scoring và lý giải cho mỗi concern._

### 4.1 Config file — Threshold tập trung

```python
# config.py — thay đổi tại đây, không sửa code
STALLED_DAYS       = 3   # Task không update X ngày → Stalled
DEADLINE_RISK_DAYS = 2   # Còn X ngày đến deadline mà status != Done → Risk
BLOCKER_OPEN_DAYS  = 2   # Blocker tồn tại > X ngày → Escalate
CONFLICT_WINDOW_H  = 48  # Tìm conflict trong X giờ gần nhất
```

### 4.2 Rule-based Detection (SQLite — deterministic)

Ba rule chạy thuần SQL, không cần LLM:

```sql
-- Rule 1: Stalled task
SELECT task_id, assignee, julianday('now') - julianday(updated_at) AS days_stalled
FROM entities
WHERE status = 'In Progress'
  AND julianday('now') - julianday(updated_at) > :STALLED_DAYS;

-- Rule 2: Deadline risk
SELECT task_id, due_date, status,
       julianday(due_date) - julianday('now') AS days_remaining
FROM entities
WHERE status != 'Done'
  AND julianday(due_date) - julianday('now') <= :DEADLINE_RISK_DAYS;

-- Rule 3: Unresolved blocker
SELECT task_id, assignee, julianday('now') - julianday(updated_at) AS days_open
FROM entities
WHERE 'blocker' IN (SELECT value FROM json_each(labels))
  AND status != 'Done'
  AND julianday('now') - julianday(updated_at) > :BLOCKER_OPEN_DAYS;
```

### 4.3 Cross-source Conflict (qua entity link)

Quy trình 2 bước:

1. **Rule-based filter trước:** Tìm các task có `status = 'Done'` (chấp nhận cả `Closed`/`Resolved`) trong SQLite mà có chunk Meeting/Confluence được cập nhật trong `CONFLICT_WINDOW_H` giờ gần nhất, liên kết qua entity backlink.
2. **Verify khi cần:** Cặp (SQLite record, ChromaDB chunk) đã lọc được kiểm tra bằng keyword conflict (`pending|chờ|review|chưa|in progress|re-open|still fail|...`). Hook LLM verify để confirm/deny là tùy chọn, mặc định chạy rule-based để giữ deterministic và đo được precision/recall.

> **Lưu ý:** Giữ rule-based làm lớp chính giúp đo precision/recall ổn định; LLM verify chỉ là lớp tăng cường tùy chọn để giảm false positive.

### 4.4 Severity Scoring + Lý giải

```python
def score_severity(concern_type: str, **kwargs) -> tuple[int, str]:
    if concern_type == "stalled_task":
        days = kwargs["days_stalled"]
        sev = 4 if days > 7 else 3
        return sev, f"Task chưa có update trong {days} ngày."

    if concern_type == "deadline_risk":
        days = kwargs["days_remaining"]
        sev = 5 if days <= 1 else 4
        return sev, f"Deadline còn {days} ngày, status vẫn '{kwargs['status']}'."

    if concern_type == "unresolved_blocker":
        sev = min(3 + kwargs["dependent_count"], 5)
        return sev, f"Blocker mở {kwargs['days_open']} ngày, ảnh hưởng {kwargs['dependent_count']} task."

    if concern_type == "cross_source_conflict":
        return 5, "Jira đánh dấu Done nhưng tài liệu khác vẫn ghi nhận đang pending."
```

> **Trạng thái thực tế:** ✅ Đã hoàn thành — `src/agents/concern_engine.py`. Tinh chỉnh thực tế: stalled-rule phân tầng (label `needs-review` → sev 4; idle > 30 ngày không label → sev 2 "chronic backlog"; còn lại → sev 3). Cross-source dùng rule-based, LLM-verify để dạng optional hook. **Precision 0.92 / Recall 1.00** trên bộ `_ground_truth`.

---

## 🗓️ Tuần 5: Hoàn Thiện & Kiểm Chứng (Stretch: MCP + Guardrail)

_Mục tiêu (charter): Củng cố concern engine và đánh giá trên các trường hợp đã inject. **(Stretch)** expose MCP server, bổ sung guardrail chống injection._

### 5.1 Củng cố & Đánh giá Concern Engine (primary)

- Chạy Concern Engine trên toàn bộ case anomaly đã inject, đối chiếu `_ground_truth`.
- Đo **precision / recall** từng loại concern; mục tiêu ≥ 80% (xem V4 ở Tuần 6).
- Tinh chỉnh threshold trong `config.py` để giảm false positive mà không bỏ sót ground truth.

> **Lưu ý:** Các threshold/rule đã được tinh chỉnh theo benchmark — không mở rộng phạm vi rule mà chưa chạy lại bộ test precision/recall.

### 5.2 (Stretch) MCP Server (FastAPI + MCP SDK)

Mở 3 endpoints chính:

```
POST /ingest              → Trigger ingestion pipeline
GET  /report?date=...     → Chạy Report Agent, trả về report.md content
GET  /concerns?min_sev=3  → Trả về danh sách concern đã lọc theo severity
```

Thêm Basic Auth (API key trong header `X-API-Key`), fail-closed nếu chưa cấu hình `MCP_API_KEY`.

### 5.3 (Stretch) Guardrails

**Input Guardrail** — chạy trước khi text đi vào ChromaDB hoặc LLM context:

```python
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"act\s+as\s+",
    r"system\s+prompt",
    r"jailbreak",
    r"DAN\b",
]

def sanitize_input(text: str, field_name: str, source_id: str) -> str:
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            audit_log(source_id, field_name, "injection_attempt", text[:200])
            return f"[FILTERED: potential injection in {field_name}]"
    return text[:2000]  # Hard cap độ dài field
```

**Output Guardrail** — kiểm tra sau khi Agent trả về:

```python
SECRET_PATTERNS = [r"sk-[A-Za-z0-9]{32,}", r"Bearer\s+[A-Za-z0-9\-_]+", r"[A-Za-z0-9]{40,}"]

def sanitize_output(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text)
    return text
```

Audit log (SQLite): `timestamp | source_id | field | flag_type | snippet`

### 5.4 (Stretch) End-to-end Test MCP

```bash
curl -X POST http://localhost:8000/ingest -H "X-API-Key: $KEY"
curl http://localhost:8000/report?date=$(date +%F) -H "X-API-Key: $KEY"
curl http://localhost:8000/concerns?min_sev=3 -H "X-API-Key: $KEY"
```

Kỳ vọng: pipeline chạy không crash, report có citation, concerns có severity + explanation.

> **Trạng thái thực tế:** Đánh giá concern engine ✅. **(Stretch) đã hoàn thành sớm** — `src/mcp/server.py` (3 endpoint + `X-API-Key`, fail-closed), `src/guardrail/sanitizer.py` (input injection + output secret redaction), bảng `audit_log` đã ghi. Guardrail chặn **4/4** injection test, **0 false positive**.

---

## 🗓️ Tuần 6: Tích Hợp & Trình Bày (One-Command)

_Mục tiêu (charter): Hoàn thiện luồng end-to-end chạy bằng một lệnh; viết tech report; chuẩn bị demo và tech talk cho team._

### 6.1 One-Command Runner

```bash
# run_agent.sh
#!/bin/bash
set -e
echo "=== AI Project Intelligence Agent ==="

echo "[1/4] Xóa DB cũ..."
rm -f data/vault.db && python src/init_db.py

echo "[2/4] Chạy Ingestion..."
python src/ingestion/run_pipeline.py \
  --jira  data/jira_synthetic_AIP.json \
  --conf  data/confluence_synthetic/ \
  --notes data/meeting_notes_synthetic/

echo "[3/4] Chạy Agent..."
python src/agents/report_agent.py --date $(date +%F) > output/report.md
python src/agents/concern_engine.py --date $(date +%F) > output/concerns.json

echo "[4/4] Done. Kết quả:"
echo "  → output/report.md"
echo "  → output/concerns.json"
```

### 6.2 Verification — Definition of Done (6 bước)

- [ ] **V1:** `run_agent.sh` chạy end-to-end không crash trên máy fresh
- [ ] **V2:** `report.md` có ít nhất 5 citation với `source_id` hợp lệ (tồn tại trong vault)
- [ ] **V3:** Concern Engine phát hiện được tất cả 4 loại anomaly trong bộ ground truth
- [ ] **V4:** Precision/Recall của Concern Engine ≥ 80% trên bộ test `_ground_truth`
- [ ] **V5:** Guardrail chặn được ít nhất 3 test case injection đã chuẩn bị
- [ ] **V6:** Demo live chạy được trước audience mà không cần can thiệp thủ công

### 6.3 Tech Report & Demo

Tech report bao gồm:

- Lý do chọn OpenAI SDK trực tiếp thay vì LangChain
- Lý do chọn dual storage SQLite + ChromaDB và trade-off
- Benchmark: precision/recall của concern engine, tỉ lệ citation accuracy
- Lessons learned và roadmap tiếp theo

Kịch bản demo live (+ tech talk cho team):

1. Chạy `./run_agent.sh` từ terminal
2. Mở `report.md` — chỉ vào citation
3. Mở `concerns.json` — demo cross-source conflict được phát hiện
4. Gọi MCP endpoint từ tool ngoài

> **Trạng thái thực tế:** ✅ Đã hoàn thành — `run_agent.sh` → `src/run_agent.py` (orchestrator có `--reset`, V2/V3 self-check). Tất cả V1–V6 đạt; có `README.md` + `TECH_REPORT.md`.

---

## ➕ Ngoài Kế Hoạch (đã giao vượt charter)

Các hạng mục đã làm thêm, không nằm trong charter ban đầu:

- **Word/Excel exporters** (`src/exporters.py`): `report.md` → `.docx`, `concerns.json` → `.xlsx` (conditional formatting cho severity = 5); tự chạy trong `run_agent.py`.
- **`report_pipeline.py`**: tách logic bucketing concern + grounding + sanitize để CLI và MCP server dùng chung.
- **Retry-with-backoff** trong Report Agent: xử lý lỗi 403/408/429/5xx.
- **`audit_log`** writes: ghi mọi lần guardrail flag (timestamp / source_id / field / flag_type / snippet).
- **Incremental `sync_log`**: nền tảng cho ingest tăng dần (idempotent qua UNIQUE index trên `snapshots`).

---

## 💡 Tóm tắt các quyết định kiến trúc

| Quyết định             | Chọn                                    | Bỏ               | Lý do                                   |
| ---------------------- | --------------------------------------- | ---------------- | --------------------------------------- |
| LLM Agent layer        | OpenAI SDK + ReAct tự viết              | LangChain        | Debug dễ hơn, control flow rõ ràng      |
| Chunking Confluence    | MarkdownHeaderTextSplitter              | Fixed token      | Section heading là semantic boundary    |
| Chunking Meeting Notes | RecursiveCharacterTextSplitter (300/40) | Fixed token      | Separator tùy chỉnh cho 2 section       |
| Confluence format      | JSON + YAML metadata                    | Plain text       | Metadata filter trong ChromaDB          |
| Concern Engine logic   | Rule-based SQL trước, LLM chỉ confirm   | LLM toàn bộ      | Deterministic, đo được, tiết kiệm token |
| Cross-source conflict  | Rule filter → LLM verify (optional)     | LLM scan toàn bộ | Giảm false positive và chi phí API      |
