# KẾ HOẠCH TRIỂN KHAI

**Phiên bản: v3.0** — Cập nhật theo feedback Senior Engineer Review

> **Thay đổi chính so với v2:** (1) Bỏ LangChain → dùng OpenAI SDK trực tiếp với ReAct loop tự viết. (2) Confluence synthetic chuyển sang format JSON có metadata khớp với Jira. (3) Chunking strategy được định nghĩa rõ per nguồn dữ liệu.

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

## 🗓️ Tuần 1: Thiết Kế Kiến Trúc Kép & Chuẩn Bị Dữ Liệu

_Mục tiêu: Chốt schema cho ChromaDB và SQLite, chuẩn bị đủ bộ 3 data có ground truth, setup CI/CD._

### 1.1 Design Doc & Schema

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

### 1.2 Chuẩn bị Dữ liệu (Data Prep)

**Jira:** Dùng bộ synthetic `jira_synthetic_AIP.json` đã generate (104 issues, đủ 4 loại anomaly với `_ground_truth` label).

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

### 1.3 Môi trường & CI

- Setup Python repo với cấu trúc thư mục: `src/`, `data/`, `tests/`, `config/`
- Cấu hình Linter: `flake8` + `black`
- File `config.py` chứa các threshold (xem tuần 4): `STALLED_DAYS`, `DEADLINE_RISK_DAYS`
- Viết 1 unit test cơ bản để CI pipeline chạy xanh

---

## 🗓️ Tuần 2: Ingestion Pipeline & Knowledge Base (Hệ Lưu Trữ Kép)

_Mục tiêu: Xây dựng pipeline đọc 3 nguồn, phân luồng đúng vào ChromaDB và SQLite._

### 2.1 Custom Ingestion (OpenAI SDK — không dùng LangChain Document Loaders)

Viết 3 connector độc lập, mỗi cái đọc 1 nguồn và trả về `normalized_doc` dict chuẩn:

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

### 2.2 Entity Extraction & Routing (Phân luồng)

Trích xuất entity (Task ID, Person, Date) bằng regex + rule đơn giản — không cần LLM ở bước này.

**Route 1 → ChromaDB** (semantic, dùng cho Report Agent):

- Confluence content → chunk theo Markdown heading (`MarkdownHeaderTextSplitter` từ `langchain_text_splitters` — chỉ dùng splitter, không import toàn bộ LangChain)
- Meeting Notes → chunk theo section `[Attendees]` / `[Action Items]` + `RecursiveCharacterTextSplitter`
- Jira description → push thẳng, không chunk thêm

**Chunking parameters:**

| Nguồn            | Splitter                       | chunk_size | overlap  | Lý do                                           |
| ---------------- | ------------------------------ | ---------- | -------- | ----------------------------------------------- |
| Confluence       | MarkdownHeaderTextSplitter     | 600 token  | 80 token | Section heading là semantic boundary tự nhiên   |
| Meeting Notes    | RecursiveCharacterTextSplitter | 300 token  | 40 token | Văn bản trơn, ngắn, 2 section ít liên quan nhau |
| Jira description | Không chunk                    | —          | —        | Đã ngắn sau khi extract từ ADF                  |

**Route 2 → SQLite** (structured, dùng cho Concern Engine):

- Entity metadata, status, assignee, due_date → bảng `entities`
- Snapshot trạng thái ngày hôm đó → bảng `snapshots`

### 2.3 Day-over-day Diff

Query SQL thuần, so sánh snapshot hôm nay vs hôm qua:

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

---

## 🗓️ Tuần 3: Xây Dựng Report Agent (OpenAI SDK + ReAct Loop)

_Mục tiêu: Tạo Agent biết dùng Tool để tổng hợp báo cáo có trích dẫn — không dùng LangChain._

### 3.1 Xây dựng Tools (OpenAI Function Calling schema)

Định nghĩa 3 tools dưới dạng JSON schema cho OpenAI API:

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

### 3.2 ReAct Loop (tự viết ~50 dòng, không dùng LangChain)

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

### 3.3 Citation Enforcement

System prompt ép Agent trích dẫn nguồn:

```
Mọi nhận định (claim) PHẢI kèm [source_id] lấy từ metadata của tool result.
Nếu không có nguồn xác thực → KHÔNG được viết nhận định đó.
Ví dụ đúng:  "Task AIP-45 đang stalled từ 2025-05-18 [AIP-45]"
Ví dụ sai:   "Task AIP-45 có vẻ đang bị chậm"
```

---

## 🗓️ Tuần 4: Concern Engine (Rule-based + LLM Assisted)

_Mục tiêu: Phát hiện rủi ro tự động — deterministic rules trên SQLite, LLM chỉ dùng cho cross-source conflict._

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

### 4.3 Cross-source Conflict (LLM — duy nhất nơi dùng AI trong Concern Engine)

Quy trình 2 bước:

1. **Rule-based filter trước:** Tìm các task có `status = 'Done'` trong SQLite mà có chunk trong ChromaDB được cập nhật trong `CONFLICT_WINDOW_H` giờ gần nhất.
2. **LLM verify:** Chỉ gửi những cặp (SQLite record, ChromaDB chunk) đã lọc ra cho LLM xác nhận có mâu thuẫn thực sự không → giảm false positive, tiết kiệm token.

> **Lưu ý:** LLM output cho cross-source conflict là non-deterministic. Để đo precision/recall chính xác, cần giữ thêm rule-based fallback: nếu chunk chứa keyword `pending|chờ|review|chưa xong` và SQLite status = `Done` → flag là potential conflict, LLM chỉ confirm/deny.

### 4.4 Severity Scoring

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

---

## 🗓️ Tuần 5: MCP Server & Guardrails

_Mục tiêu: Đóng gói thành API chuẩn MCP, bảo vệ hệ thống khỏi prompt injection._

### 5.1 MCP Server (FastAPI + MCP SDK)

Mở 3 endpoints chính:

```
POST /ingest              → Trigger ingestion pipeline
GET  /report?date=...     → Chạy Report Agent, trả về report.md content
GET  /concerns?min_sev=3  → Trả về danh sách concern đã lọc theo severity
```

Thêm Basic Auth (API key trong header `X-API-Key`).

### 5.2 Guardrails

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

### 5.3 End-to-end Test

```bash
curl -X POST http://localhost:8000/ingest -H "X-API-Key: $KEY"
curl http://localhost:8000/report?date=$(date +%F) -H "X-API-Key: $KEY"
curl http://localhost:8000/concerns?min_sev=3 -H "X-API-Key: $KEY"
```

Kỳ vọng: pipeline chạy không crash, report có citation, concerns có severity + explanation.

---

## 🗓️ Tuần 6: Đóng Gói (One-Command) & Báo Cáo

_Mục tiêu: Hoàn thiện để demo — một lệnh chạy toàn bộ, tech report đầy đủ._

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

Kịch bản demo live:

1. Chạy `./run_agent.sh` từ terminal
2. Mở `report.md` — chỉ vào citation
3. Mở `concerns.json` — demo cross-source conflict được phát hiện
4. Gọi MCP endpoint từ tool ngoài

---

## 💡 Tóm tắt các quyết định kiến trúc

| Quyết định             | Chọn                                    | Bỏ               | Lý do                                   |
| ---------------------- | --------------------------------------- | ---------------- | --------------------------------------- |
| LLM Agent layer        | OpenAI SDK + ReAct tự viết              | LangChain        | Debug dễ hơn, control flow rõ ràng      |
| Chunking Confluence    | MarkdownHeaderTextSplitter              | Fixed token      | Section heading là semantic boundary    |
| Chunking Meeting Notes | RecursiveCharacterTextSplitter (300/40) | Fixed token      | Separator tùy chỉnh cho 2 section       |
| Confluence format      | JSON + YAML metadata                    | Plain text       | Metadata filter trong ChromaDB          |
| Concern Engine logic   | Rule-based SQL trước, LLM chỉ confirm   | LLM toàn bộ      | Deterministic, đo được, tiết kiệm token |
| Cross-source conflict  | Rule filter → LLM verify                | LLM scan toàn bộ | Giảm false positive và chi phí API      |
