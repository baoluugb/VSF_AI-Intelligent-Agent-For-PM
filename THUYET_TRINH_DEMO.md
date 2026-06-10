---
marp: true
title: AI Project Intelligence Agent — Bản thuyết trình Demo
author: VSF
paginate: true
theme: default
---

<!--
Đây là bản thuyết trình ở dạng Markdown.
- Đọc trực tiếp như một tài liệu, HOẶC render thành slide bằng Marp / reveal.js / VS Code "Marp for VS Code".
- Mỗi dấu `---` là một slide.
- Phần "Kịch bản demo trực tiếp" ở cuối là kịch bản chạy thật ngày mai.
-->

# AI Project Intelligence Agent

### Trợ lý AI giám sát rủi ro dự án phần mềm

**Ingest Jira · Confluence · Meeting Notes → phát hiện rủi ro → báo cáo hằng ngày có trích dẫn**

> Toàn bộ pipeline chạy bằng **một câu lệnh**.
> Mã nguồn: Python thuần · OpenAI SDK (ReAct tự viết, không LangChain) · SQLite + ChromaDB · FastAPI.

_Bản build solo 6 tuần — HEAD `242db5b` — 97 test pass._

---

## Nội dung trình bày

1. **Bài toán** — vì sao PM cần agent này
2. **Kiến trúc tổng quan** — luồng dữ liệu end-to-end
3. **Dữ liệu nguồn** — synthetic data + rủi ro được "gài" sẵn
4. **Tầng Ingestion** — 3 connector + Entity Extractor + orchestrator
5. **Tầng Storage** — kho kép SQLite (sự kiện) + ChromaDB (ngữ nghĩa)
6. **Concern Engine** — 4 luật phát hiện rủi ro + chấm điểm
7. **Report Agent** — vòng lặp ReAct + ép trích dẫn
8. **Report Pipeline** — "grounding", ưu tiên hoá, fallback
9. **Guardrails** — chống prompt-injection + che lộ secret
10. **MCP Server** — mặt API FastAPI
11. **Orchestrator & Exporters** — chạy 1 lệnh, xuất Word/Excel
12. **Kết quả · Quyết định kỹ thuật · Hạn chế · Kịch bản demo**

---

## 1. Bài toán

Một PM theo dõi dự án phần mềm phải đối mặt với:

- **Thông tin phân mảnh** qua 3 nguồn: trạng thái task (**Jira**), thiết kế/quyết định (**Confluence**), cam kết trong họp (**Meeting Notes**).
- **Rủi ro bị chôn vùi**: task trì trệ, sắp/đã quá hạn, blocker kéo dài, và **mâu thuẫn chéo nguồn** (Jira ghi "Done" nhưng biên bản họp vẫn nói "đang chờ review").
- **Báo cáo thủ công** tốn thời gian và dễ bỏ sót.

### Mục tiêu của hệ thống

> Mỗi ngày tự động **đọc cả 3 nguồn → phát hiện rủi ro một cách tất định (deterministic) → viết báo cáo có trích dẫn**, dẫn đầu bằng khối **"Cần xử lý hôm nay"** để PM biết phải làm gì trước.

**Nguyên tắc thiết kế cốt lõi:** Việc _phát hiện rủi ro_ phải **đo lường được** (precision/recall) → dùng **luật SQL tất định**. LLM **chỉ** lo phần _kể chuyện_ (narrative) — không tự suy diễn ra rủi ro.

---

## 2. Kiến trúc tổng quan

```
   Jira JSON ─┐
Confluence ──┤   ┌──────────── TẦNG INGESTION ────────────┐
   Meetings ─┘   │ Connectors → EntityExtractor            │
                 │   (chuẩn hoá doc + tách entity/backlink)│
                 └───────────────┬─────────────────────────┘
                     ┌───────────┴───────────┐
                     ▼                        ▼
              SQLite (SỰ KIỆN)         ChromaDB (NGỮ NGHĨA)
              entities, snapshots,     3 collection:
              backlinks, sync_log,     confluence_chunks,
              audit_log                meeting_chunks,
                     │                 jira_descriptions
                     └───────────┬───────────┘
              ┌──────────────────┴───────────────────┐
              ▼                                       ▼
     ┌──────────────────┐               ┌─────────────────────────┐
     │  CONCERN ENGINE  │── grounding ─▶│   REPORT AGENT (ReAct)   │
     │  4 luật + sev    │  (cấp dữ liệu)│   3 tool · ép trích dẫn  │
     └────────┬─────────┘               └────────────┬────────────┘
              ▼                                       ▼
      output/concerns.json                     output/report.md
      (+ concerns.xlsx)                         (+ report.docx)

   ⟦ GUARDRAILS bọc 2 biên: InputSanitizer (lúc ingest) · OutputSanitizer (lúc xuất báo cáo) ⟧
   ⟦ MCP SERVER (FastAPI) phơi cùng pipeline qua /ingest /report /concerns ⟧
```

**Luồng end-to-end:** Nguồn JSON → ingest vào kho kép → Concern Engine quét rủi ro → nạp rủi ro làm "mỏ neo" cho Report Agent → Agent viết báo cáo có trích dẫn → xuất `.md` / `.docx` / `.json` / `.xlsx`.

---

## 3. Dữ liệu nguồn (synthetic data)

Dữ liệu tổng hợp được tạo sẵn, **gài ground-truth anomalies** để đo độ chính xác.

| Nguồn         | File                                        | Quy mô                                      |
| ------------- | ------------------------------------------- | ------------------------------------------- |
| Jira          | `data/jira/jira_synthetic_AIP.json`         | **1.000 issue** (144 anomaly = 4 loại × 36) |
| Confluence    | `data/confluence/confluence_synthetic.json` | **217 trang**, có liên kết tới Jira epic    |
| Meeting Notes | `data/meeting_notes/meeting_notes.json`     | **5 cuộc họp** + action items               |

### Anomaly được "gài" (để chấm điểm)

- 4 loại rủi ro × 36 mẫu mỗi loại: `stalled`, `deadline_risk`, `blocker`, `cross_source_conflict`.
- **Mẹo nhãn quan trọng:** anomaly `stalled` mang nhãn **`needs-review`**; anomaly cross-source mang nhãn `cross-source-conflict-marker` → giúp engine phân biệt rủi ro "thật cần xử lý" với "tồn đọng kinh niên".

> Kho sinh ra lúc chạy (`data/vault.db`, `data/chroma/`, `output/`) đều **gitignore** — clone mới chạy lại 1 lệnh là dựng lại toàn bộ.

---

## 4. Tầng Ingestion — tổng quan

**Vai trò:** biến JSON thô của 3 nguồn thành (a) _entity có cấu trúc_ cho SQLite và (b) _chunk văn bản_ cho ChromaDB, đồng thời rút ra _backlink_ (quan hệ giữa các thực thể).

**File điều phối:** [src/ingestion/run_pipeline.py](src/ingestion/run_pipeline.py)

```
connectors ──► normalized docs ──┬─► EntityExtractor ─► SQLite (entities, snapshots, backlinks)
                                 └─► ChromaDB (jira_descriptions, confluence/meeting chunks)
```

Các bước trong `run_pipeline()`:

1. `init_db()` tạo schema SQLite.
2. Mỗi connector `.load()` → danh sách **doc đã chuẩn hoá** (cùng khuôn `source`, `source_id`, `text_content`…).
3. _(Tuỳ chọn)_ **InputSanitizer** quét prompt-injection trước khi index.
4. `EntityExtractor.extract()` → `(entities, backlinks)`.
5. Ghi entity + snapshot + backlink + sync_log vào **SQLite**.
6. Route phần văn bản vào **ChromaDB** (qua _field bridge_).

→ Trả về thống kê: `documents, entities, backlinks, jira_docs, confluence_chunks, meeting_chunks, flagged_injections`.

---

## 4a. Connector — JiraConnector

[src/ingestion/jira_connector.py](src/ingestion/jira_connector.py) — đọc 1.000 issue và **chuẩn hoá** từng issue.

**Làm gì:**

- Bóc các field lồng nhau của Jira: `issuetype`, `status.name`, `assignee.displayName`, `priority.name`, `labels`, `duedate`, `created`, `updated`.
- **Xử lý ADF** (Atlassian Document Format): description của Jira là cây JSON → `_extract_adf_text()` duyệt đệ quy lấy text sạch.
- **Ấn định `source = "jira"`** (cố định) — KHÔNG copy field `"source": "Apache"` trong file gốc.

> ⚠️ **Bug thật đã sửa:** file synthetic ghi `"source": "Apache"`; nếu copy nguyên, mọi issue trượt khỏi nhánh định tuyến `source=="jira"` → **0 entity**. Đây là một trong các bug chỉ lộ ra khi chạy end-to-end.

**Output 1 doc chuẩn hoá:** `{source, source_id(=key), title, status, assignee, priority, labels, due_date, description, url, created_at, updated_at}`.

---

## 4b. Connector — Confluence & Meeting Notes

### ConfluenceConnector — [src/ingestion/confluence_connector.py](src/ingestion/confluence_connector.py)

- Đọc **thư mục** file JSON; hỗ trợ cả `{"pages": [...]}` lẫn 1 page/file.
- **Validate**: bắt buộc có `page_id, title, space, content`; thiếu → bỏ qua + cảnh báo (pipeline không gãy vì 1 file lỗi).
- Chuẩn hoá `status` về `current/outdated/draft`; rút `linked_jira_epics`, `tags`; sinh URL placeholder.
- **Không chunk ở đây** — để dành cho `ChromaStore`.

### MeetingNotesConnector — [src/ingestion/meeting_notes_connector.py](src/ingestion/meeting_notes_connector.py)

- Hỗ trợ 2 khuôn: object đơn lẻ **hoặc** `{"meetings": [...]}`, và cả file `.txt` (parse section `[Attendees]` / `[Action Items]`).
- Chuẩn hoá **attendees** → `"Tên (Vai trò)"`; **action_items** → `{issue_key, assignee, text}`.
- **`_extract_issue_keys()`**: regex `[A-Z]+-\d+` tìm mọi key Jira được nhắc tới → phục vụ tách backlink và luật cross-source.

---

## 4c. EntityExtractor — tách entity & backlink

[src/ingestion/entity_extractor.py](src/ingestion/entity_extractor.py) — "bộ não định tuyến" của ingestion.

Nhận danh sách doc đã chuẩn hoá, trả về **2 danh sách**:

| Loại doc          | Sinh ra                                                                                                         |
| ----------------- | --------------------------------------------------------------------------------------------------------------- |
| **Jira**          | Một **entity** (bản ghi gốc → bảng `entities`). Đảm bảo có `task_id`.                                           |
| **Confluence**    | **Backlink** `mentions`: từ `linked_jira_epics` (loại `linked_epic`) + các key Jira nhắc inline trong nội dung. |
| **Meeting Notes** | **Backlink** `action_item` (liên kết mạnh, từ action items) + `mentions` (key nhắc tới khác).                   |

**Backlink** = cạnh quan hệ `(source_entity_id, target_entity_id, link_type, context)`.
→ Ví dụ: trang Confluence CONF-12 _mentions_ AIP-1; biên bản MTG-3 có _action_item_ cho KAFKA-64.

> Backlink chính là cơ sở để Concern Engine đếm **"blocker này chặn bao nhiêu task"** (mức độ nghiêm trọng theo số phụ thuộc).

---

## 4d. Orchestrator — `run_pipeline()` & "field bridge"

**Field bridge** = lớp dịch tên field giữa _connector_ và _ChromaStore_ — một chi tiết nhỏ nhưng từng gây bug nghiêm trọng.

- Connector phát ra: `text_content` / `source_id`.
- `ChromaStore` lại đọc: `content` / `page_id` / `note_id`.
- `_confluence_to_chroma()` và `_meeting_to_chroma()` **bắc cầu** đúng tên field.

> ⚠️ **Bug thật đã sửa:** orchestrator cũ truyền thẳng doc không ánh xạ → ChromaDB index **0** chunk Confluence/Meeting (mất toàn bộ khả năng tìm kiếm ngữ nghĩa) mà không báo lỗi. Field bridge khắc phục triệt để.

**Định tuyến vào kho:**

- `source=="jira"` → `add_jira_description()` (lưu nguyên description, không chunk).
- `source=="confluence"` → `add_confluence_chunks()` (chunk theo header + ký tự).
- `source=="meeting_notes"` → `add_meeting_chunks()` (chunk theo separator).

Mỗi entity còn được ghi **1 snapshot cho "hôm nay"** → làm nền cho `get_daily_diff`.

---

## 5. Tầng Storage — vì sao "kho kép"?

> **Quyết định kiến trúc:** tách thành **2 kho**, mỗi kho làm đúng thế mạnh.

|          | **SQLite** (kho sự kiện)                           | **ChromaDB** (kho ngữ nghĩa)                       |
| -------- | -------------------------------------------------- | -------------------------------------------------- |
| Lưu gì   | Trạng thái, ngày tháng, nhãn, quan hệ              | Đoạn văn bản (thiết kế, cam kết họp)               |
| Truy vấn | **Tất định**: status, due_date, diff ngày-qua-ngày | **Ngữ nghĩa**: tìm theo ý nghĩa, không cần khớp từ |
| Phục vụ  | **Concern Engine** (đo precision/recall)           | **Report Agent** (bổ sung ngữ cảnh, dẫn chứng)     |

**Nguyên tắc:** dữ kiện cứng (task đang ở status nào, hạn ngày nào) thuộc về **SQL**; thông tin mềm (đội đã quyết gì, cam kết gì) thuộc về **vector store**. Ép một LLM trả lời "task X có quá hạn không" vừa chậm vừa không đo được — SQL trả lời tức thì và tái lập 100%.

---

## 5a. Storage — SQLite (sự kiện)

**Schema** ([src/storage/init_db.py](src/storage/init_db.py)) — 5 bảng + 3 index:

| Bảng        | Vai trò                                                                            |
| ----------- | ---------------------------------------------------------------------------------- |
| `entities`  | Bản ghi gốc 1 task: `task_id`(PK), status, assignee, due_date, labels, updated_at… |
| `snapshots` | Ảnh chụp trạng thái theo ngày → tính **diff ngày-qua-ngày**                        |
| `backlinks` | Quan hệ giữa thực thể (mentions / action_item)                                     |
| `sync_log`  | Mốc đồng bộ gần nhất theo từng nguồn                                               |
| `audit_log` | Nhật ký guardrail chặn injection                                                   |

Index: `status`, `updated_at`, `(task_id, snapshot_date)` → tăng tốc các luật.

**Lớp truy cập** ([src/storage/sqlite_store.py](src/storage/sqlite_store.py)) — `SQLiteStore` là context manager (`with ...`), tự commit/rollback:

- `bulk_upsert()` (INSERT OR REPLACE), `save_snapshot()`, `insert_backlinks()`.
- `run_query()` — SQL tuỳ ý cho Concern Engine.
- `get_daily_diff(date)` — self-join `snapshots` hôm nay vs hôm qua, chỉ trả task **có thay đổi**.
- `query_entity(id)` — tra cứu chính xác 1 task (Report Agent dùng).
- `insert_audit_log()` — ghi vết guardrail.

---

## 5b. Storage — ChromaDB (ngữ nghĩa)

[src/storage/chroma_store.py](src/storage/chroma_store.py) — `PersistentClient` với **3 collection chuyên biệt**:

| Collection          | Nội dung                  | Chiến lược chunk                                                                                 |
| ------------------- | ------------------------- | ------------------------------------------------------------------------------------------------ |
| `confluence_chunks` | Trang thiết kế/quyết định | **2 tầng**: tách theo header Markdown (`##`,`###`) **rồi** cắt theo ký tự (size 600, overlap 80) |
| `meeting_chunks`    | Biên bản họp              | Tách theo **separator** `[Action Items]`,`[Attendees]`,… (size 300, overlap 40)                  |
| `jira_descriptions` | Mô tả issue               | **Không chunk** — lưu nguyên 1 doc/issue, `id = source_id` (idempotent)                          |

**Vì sao chunk khác nhau theo nguồn?** Trang Confluence dài & có cấu trúc đề mục → tách theo header giữ ngữ cảnh; biên bản họp ngắn, theo mục → tách theo separator; mô tả Jira ngắn → để nguyên cho tra cứu chính xác.

**`query(collection, query_text, n_results, where)`** → trả `[{document, metadata, distance}]`. `distance` nhỏ = giống hơn (dùng để xếp hạng). Hỗ trợ test bằng `EphemeralClient()` (không cần ổ đĩa).

> Quy mô thực: 1.222 doc → **1.000 entity · 1.614 confluence chunk · 21 meeting chunk · 719 backlink**.

---

## 6. Concern Engine — bộ não phát hiện rủi ro

[src/agents/concern_engine.py](src/agents/concern_engine.py) — **tất định, không LLM** (LLM chỉ là bước xác nhận tuỳ chọn trong tương lai).

`run_all_rules()` chạy 4 luật, gộp kết quả, **sắp xếp theo severity giảm dần**. Mỗi luật so với một **mốc thời gian `as_of`** (mặc định "hôm nay"; demo dùng `2025-05-30` vì data là giữa 2025).

> ⚠️ **Mẹo timestamp:** Jira gắn hậu tố `+0000` mà SQLite `julianday` không parse được → các luật so sánh bằng `substr(updated_at, 1, 10)` (chỉ phần ngày).

**Mỗi concern là một object thống nhất:**

```json
{ "type": "...", "task_id": "...", "severity": 1-5,
  "explanation": "...(theo REPORT_LANG)", "assignee": "...",
  "source_ids": ["..."], "details": { ... } }
```

`source_ids` chính là **mỏ neo trích dẫn** mà Report Agent sẽ dùng.

---

## 6a. Luật 1 — Stalled (trì trệ) · phân tầng

**Định nghĩa:** task `In Progress` không update quá `STALLED_DAYS` (mặc định 3) ngày.

**Điểm tinh tế — phân tầng severity** (để báo cáo ưu tiên đúng):

| Điều kiện                                       | Severity | Ý nghĩa                                                |
| ----------------------------------------------- | -------- | ------------------------------------------------------ |
| Có nhãn **`needs-review`**                      | **4**    | Rủi ro **thật, cần xử lý** (chính là anomaly được gài) |
| Idle > `CHRONIC_STALLED_DAYS` (30) & không nhãn | **2**    | **Tồn đọng kinh niên** — giữ lại nhưng _de-prioritise_ |
| Còn lại                                         | 3        | Trì trệ thường                                         |

> **Vì sao quan trọng:** luật stalled "lương thiện" sẽ nổi _mọi_ task cũ → nhiễu. Thay vì che giấu (giảm recall), ta **giữ tất cả nhưng phân tầng**: các "task zombie 100 ngày" bị đẩy xuống severity 2 và **gộp thành 1 con số** trong báo cáo, không chiếm chỗ khối "Cần xử lý hôm nay".

`_has_label()` parse JSON nhãn một cách phòng thủ (NULL/sai khuôn không bao giờ ném lỗi).

---

## 6b. Luật 2 — Deadline risk (rủi ro hạn chót)

**Định nghĩa:** task **chưa Done** có `due_date` **gần mốc** — trong khoảng `± DEADLINE_RISK_DAYS` (mặc định 2) ngày quanh `as_of`.

**Vì sao là "cửa sổ gần hạn" chứ không phải "mọi task quá hạn"?**

- Một task quá hạn _vài tháng_ là vấn đề _trì trệ/bỏ rơi_, **không phải** rủi ro hạn _đang tới_.
- Gắn cờ mọi task quá hạn → ngập false positive.

> ⚠️ **Bug thật đã sửa:** bản đầu gắn cờ mọi task quá hạn → precision chỉ **0.29**. Thu hẹp về cửa sổ gần hạn → precision **0.92**, đúng tinh thần "deadline đang đến" của plan.

**Chấm điểm:** còn ≤ 1 ngày (hoặc đã quá hạn) → **severity 5**, ngược lại 4. Giải thích nêu rõ "quá hạn N ngày" hoặc "còn N ngày", kèm status hiện tại.

---

## 6c. Luật 3 — Unresolved blocker (blocker treo)

**Định nghĩa:** task **chưa Done**, có nhãn `blocker`, đã mở > `BLOCKER_OPEN_DAYS` (mặc định 2) ngày.

**Điểm tinh tế — đo _tầm ảnh hưởng_ qua backlink:**

```sql
(SELECT COUNT(*) FROM backlinks b WHERE b.target_entity_id = e.task_id) AS dependent_count
```

→ Đếm **bao nhiêu task khác phụ thuộc** vào blocker này.

**Chấm điểm:** `severity = min(3 + dependent_count, 5)` → blocker chặn càng nhiều task càng nghiêm trọng. Sắp xếp ưu tiên blocker chặn nhiều nhất + treo lâu nhất.

Ví dụ thực trong báo cáo: _KAFKA-64 mở 11 ngày, ảnh hưởng 4 task_ → severity cao, đứng đầu nhóm blocker.

---

## 6d. Luật 4 — Cross-source conflict (mâu thuẫn chéo nguồn)

**Đây là luật "đặc sản"** — kết hợp **SQL + tìm kiếm ngữ nghĩa**, vẫn tất định.

**Định nghĩa:** Jira nói task đã hoàn tất _gần đây_, nhưng biên bản họp vẫn ghi _pending/review_.

**3 bước:**

1. **SQL:** lấy task vừa hoàn tất — status ∈ {`Done`,`Closed`,`Resolved`} trong vòng `CONFLICT_WINDOW_H` (48) giờ.
   _(Closed/Resolved cũng tính — nếu chỉ "Done" sẽ bỏ sót ~2/3 ca.)_
2. **Chroma:** với mỗi ứng viên, tìm `meeting_chunks` nhắc tới task.
3. **Khớp:** chunk phải **(a)** chứa **đúng key** (`_mentions_key`, biên từ) **VÀ (b)** chứa từ khoá xung đột (`pending`/`chờ`/`review`/`chưa`) → gắn cờ severity **5**.

> ⚠️ **Bug tinh vi đã sửa:** `_mentions_key` có `(?![0-9])` để **`AIP-5` KHÔNG khớp `AIP-53`** — chính lỗi này từng tạo 1 false-positive cross-source duy nhất.

**Trung thực:** trên ngày demo hiện tại, luật trả **0** ca (xem slide Hạn chế) — đúng theo dữ liệu, không "vẽ" thêm.

---

## 6e. Chấm điểm severity (`score_severity`)

Một hàm `@staticmethod` duy nhất, **đa ngôn ngữ** (vi/en theo `REPORT_LANG`), trả `(severity 1-5, explanation)`.

| Loại                    | Severity  | Logic                                |
| ----------------------- | --------- | ------------------------------------ |
| `stalled_task`          | 2 / 3 / 4 | theo nhãn `needs-review` & "chronic" |
| `deadline_risk`         | 4 / 5     | ≤ 1 ngày → 5                         |
| `unresolved_blocker`    | 3–5       | `3 + số task phụ thuộc`              |
| `cross_source_conflict` | 5         | luôn nghiêm trọng nhất               |

→ Giữ **văn bản giải thích và báo cáo đồng nhất một ngôn ngữ** (mặc định Tiếng Việt).

**Chạy thử CLI:**

```bash
python -m src.agents.concern_engine --date 2025-05-30 --min-sev 3
```

---

## 7. Report Agent — vòng lặp ReAct tự viết

[src/agents/report_agent.py](src/agents/report_agent.py) — **OpenAI Function Calling thuần, KHÔNG LangChain**.

**Vòng lặp ReAct** (`run_report_agent`), tối đa `MAX_AGENT_ITERATIONS` (5) vòng:

```
gửi lịch sử hội thoại + danh sách tool → model
  ├─ model gọi tool?  → thực thi (dispatch_tool) → nạp kết quả lại → lặp
  └─ model trả text?  → đó là báo cáo cuối cùng → return
```

**3 tool** (`tool_choice="auto"`):

- `get_daily_diff(date)` → task đổi status/assignee trong ngày.
- `query_sqlite(entity_id)` → trạng thái chính xác 1 task.
- `query_chroma(query, source_filter, epic_filter)` → tìm ngữ nghĩa trong Confluence/Meeting.

**Vì sao không LangChain?** Với bản solo, **kiểm soát từng tool-call** quan trọng hơn tiện ích framework. Vòng lặp chỉ ~50 dòng, dễ debug, không có "hộp đen" kiểu _vì sao nó không gọi tool_.

---

## 7a. Report Agent — ép trích dẫn & không bịa

**System prompt** ([build_system_prompt](src/agents/report_agent.py), bản vi/en) áp **2 hợp đồng cứng**:

1. **QUY TẮC TRÍCH DẪN (BẮT BUỘC):** mọi phát biểu dữ kiện phải kết thúc bằng `[source_id]` lấy từ `source_ids` mà tool trả về.
   - ✅ _"AIP-45 vẫn 'In Progress' [AIP-45]."_
   - ❌ _"AIP-45 có vẻ đang kẹt."_ ← thiếu nguồn
2. **KHÔNG BỊA:** tool trả rỗng → ghi _"(Không tìm thấy dữ liệu xác thực.)"_, tuyệt đối không bịa id/ngày/tên/status.

**Định dạng đầu ra — đúng 5 mục, đúng thứ tự:**
`Cần xử lý hôm nay` → `Tổng quan` → `Thay đổi hôm nay` → `Rủi ro` → `Hành động tiếp theo`.

**Mọi tool đều trả "phong bì" thống nhất** ([src/agents/tools.py](src/agents/tools.py)):

```python
{"result": <payload>, "source_ids": [<id để trích dẫn>, ...]}
```

→ Agent luôn biết **chính xác id nào được phép trích**.

---

## 7b. Report Agent — chống lỗi mạng (resilience)

LLM/proxy có thể chập chờn → 2 lớp phòng vệ:

**1. Retry-with-backoff** (`_create_with_retry`):

- Các status đáng thử lại: `{403, 408, 409, 425, 429, 500, 502, 503, 504}`.
- Backoff luỹ thừa (2s → tối đa 20s), tối đa 4 lần; lỗi khác → ném ra ngay.
- _(Proxy ckey.vn từng trả 403 "upstream rejected" khi agent bắn nhiều call dồn dập.)_

**2. Partial + caveat khi hết vòng lặp** (`_finalize_partial`):

- Nếu chạm `MAX_AGENT_ITERATIONS` mà chưa xong → ép 1 lần completion **không tool** để "chốt" báo cáo từ dữ liệu đã có, kèm cảnh báo _"báo cáo có thể thiếu dữ liệu"_.

> Model đọc từ `.env` (`OPENAI_MODEL`, mặc định `gpt-4o-mini`); đặt `OPENAI_BASE_URL` để trỏ proxy tương thích OpenAI mà **không sửa code**.

---

## 8. Report Pipeline — "grounding" & ưu tiên hoá

[src/agents/report_pipeline.py](src/agents/report_pipeline.py) — lớp **dùng chung** cho cả `run_agent.py` lẫn MCP server. Đây là nơi biến rủi ro tất định thành báo cáo có chủ đích.

**"Grounding" = neo báo cáo vào phát hiện của Concern Engine** (không để LLM tự do khám phá):

- `build_user_query()` nhồi vào prompt: **dòng tóm tắt đếm rủi ro** + danh sách **TOP ACTIONABLE** + danh sách **rủi ro đa dạng theo loại**.
- `select_actionable()` → top 5 severity cao nhất, **loại bỏ chronic** → khối "Cần xử lý hôm nay".
- `select_diverse()` → top N **mỗi loại** (không chỉ deadline) → mục "Rủi ro" phủ đủ 4 loại.

> ⚠️ **Bug thật đã sửa:** trước đây top-N theo severity toàn là `deadline_risk` → chuyển sang **top-N theo từng loại** để báo cáo phủ đủ stalled/deadline/blocker/cross-source.

`format_summary()` → dòng kiểu: _"Tổng quan rủi ro: 36 blocker · 66 quá hạn · 139 trì trệ (trong đó 47 kinh niên)."_

---

## 8a. Report Pipeline — fallback & "vệ sinh" đầu ra

`generate_grounded_report()` là **đường đi an toàn tuyệt đối** — caller không bao giờ bị crash:

```
build_user_query → run_report_agent
   ├─ LLM lỗi (proxy/mạng)? → fallback_report()  (báo cáo tất định 100% từ Concern Engine)
   ├─ OutputSanitizer.sanitize()                  (che secret nếu lỡ lọt)
   └─ linkify_jira()                              (biến [FLINK-40] thành link nếu có JIRA_BASE_URL)
```

- **`fallback_report()`** — nếu LLM chết, vẫn sinh báo cáo Markdown đủ 5 mục từ rủi ro đã phát hiện (có ghi rõ "đây là bản dự phòng, không có phần tường thuật LLM").
- **`linkify_jira()`** — chỉ đụng key Jira thật; bỏ qua `CONF-*`, `MTG-*`; no-op nếu `JIRA_BASE_URL` rỗng.

> **Triết lý:** biến phụ thuộc _cứng_ (LLM) thành phụ thuộc _mềm_ — hệ thống **suy giảm mượt** (degrade gracefully) thay vì gãy.

---

## 9. Guardrails — bảo vệ 2 biên

[src/guardrail/sanitizer.py](src/guardrail/sanitizer.py) — bọc đúng "biên giới" của agent.

### InputSanitizer (lúc ingest — chặn prompt-injection)

- Bộ mẫu regex bắt: `ignore previous instructions`, `act as DAN/unrestricted…`, `system prompt`, `jailbreak`, header giả `### SYSTEM:`, `bypass restrictions…`.
- Khớp → trả `[FILTERED]` + ghi `audit_log` (có timestamp, source_id, field, snippet).
- **Narrow có chủ đích:** `act as a coordinator` **không** bị chặn (chỉ chặn persona-switch độc hại) → 0 false positive trên input lành.
- Text lành → cắt `MAX_FIELD_LEN` (2000) + strip thẻ HTML.

### OutputSanitizer (lúc xuất báo cáo — che secret)

- Redact `sk-…` (OpenAI key), `Bearer …`, khối **PEM PRIVATE KEY** → `[REDACTED]`.

> **Kết quả test đối kháng:** chặn **4/4** payload injection, **0/3** false positive, che đủ 3 loại secret. (17 test nội tuyến: `pytest src/guardrail/sanitizer.py`.)

---

## 10. MCP Server — mặt API FastAPI

[src/mcp/server.py](src/mcp/server.py) — phơi **cùng một pipeline** ra HTTP (Swagger tại `/docs`).

| Endpoint                       | Làm gì                                                | Guardrail                              |
| ------------------------------ | ----------------------------------------------------- | -------------------------------------- |
| `POST /ingest`                 | Chạy lại ingestion 3 nguồn → kho kép                  | **InputSanitizer** lọc trước khi index |
| `GET /report?date=`            | Concern Engine → Report Agent → Markdown có trích dẫn | **OutputSanitizer**                    |
| `GET /concerns?min_sev=&date=` | Trả danh sách rủi ro tất định (không gọi LLM)         | —                                      |

**Bảo mật:** mọi endpoint yêu cầu header **`X-API-Key`** khớp `MCP_API_KEY`.

- **Fail-closed:** nếu server **chưa cấu hình** key → từ chối _mọi_ request (500), không coi "trống = mở cửa".

**Tối ưu:** client ChromaDB dùng lại giữa request (mở tốn kém); `SQLiteStore` mở theo từng request (nhẹ). Sau `/ingest`, cache Chroma bị **vô hiệu hoá** vì kho đã được dựng lại.

> Test MCP **tự skip** nếu thiếu `fastapi` (`pytest.importorskip`) → thiếu dep tuỳ chọn không làm sập toàn bộ collection.

---

## 11. Orchestrator & Exporters

### `run_agent.py` — chạy 1 lệnh end-to-end ([src/run_agent.py](src/run_agent.py))

1. **Ingest** lại kho kép (bỏ qua bằng `--skip-ingest`).
2. **Concern Engine** → `output/concerns.json`.
3. **Seed snapshot hôm-qua** cho vài task → để `get_daily_diff` có dữ liệu minh hoạ (hệ thống _thiết kế để chạy hằng ngày_; bước này mô phỏng 1 lần chạy trước).
4. **Report Agent** (đã grounding) → `output/report.md`.
5. **Export** → `output/report.docx` + `output/concerns.xlsx`.

### Exporters ([src/exporters.py](src/exporters.py))

- **Word (.docx):** parse Markdown → văn bản có style (heading, bullet, **bold**/_italic_, code block) — bảng màu xanh navy chuyên nghiệp.
- **Excel (.xlsx):** `concerns.json` → bảng phẳng; **tô đỏ** hàng severity = 5; auto width; header xanh đậm.

> Output nhiều định dạng → PM đọc `.md`, sếp nhận `.docx`, team xử lý `.xlsx`.

---

## 12. Config & cách chạy

**Tất cả ngưỡng nằm trong [config.py](config.py), override qua `.env`:**

| Biến                   | Mặc định      | Ý nghĩa                               |
| ---------------------- | ------------- | ------------------------------------- |
| `STALLED_DAYS`         | 3             | Ngày không update → "trì trệ"         |
| `DEADLINE_RISK_DAYS`   | 2             | Cửa sổ ± quanh hạn                    |
| `BLOCKER_OPEN_DAYS`    | 2             | Blocker mở bao lâu thì cảnh báo       |
| `CONFLICT_WINDOW_H`    | 48            | Cửa sổ (giờ) cho mâu thuẫn chéo nguồn |
| `CHRONIC_STALLED_DAYS` | 30            | Ngưỡng "tồn đọng kinh niên"           |
| `MAX_AGENT_ITERATIONS` | 5             | Trần vòng lặp ReAct                   |
| `REPORT_LANG`          | `vi`          | Ngôn ngữ báo cáo (vi/en)              |
| `JIRA_BASE_URL`        | _(trống)_     | Có thì biến trích dẫn thành link      |
| `OPENAI_MODEL`         | `gpt-4o-mini` | Model (override qua `.env`)           |

`validate_config()` **fail fast** nếu thiếu `OPENAI_API_KEY`.

---

## 12a. Lệnh chạy (môi trường `VSF_prj`)

```bash
# 1) Toàn bộ pipeline (ingest + concern + report + export)
python src/run_agent.py --date 2025-05-30

#   reuse kho có sẵn cho nhanh:
python src/run_agent.py --date 2025-05-30 --skip-ingest

# 2) Chỉ ingestion
python src/ingestion/run_pipeline.py

# 3) Chỉ Concern Engine (in JSON)
python -m src.agents.concern_engine --date 2025-05-30 --min-sev 3

# 4) Chỉ Report Agent
python -m src.agents.report_agent --date 2025-05-30

# 5) MCP server (cần MCP_API_KEY trong .env)
python src/mcp/server.py     # Swagger: http://localhost:8000/docs

# 6) Test
python -m pytest             # 97 passed
```

> ⚠️ Luôn truyền **`--date 2025-05-30`** — data synthetic là giữa 2025, "hôm nay" mặc định gần như không ra gì.
> Dùng đúng env `VSF_prj` (`base` thiếu fastapi/chromadb).

---

## 13. Kết quả & Benchmark

**Quy mô ingestion (corpus đầy đủ):**
| Chỉ số | Giá trị |
|---|---|
| Doc ingest | **1.222** (1000 Jira + 217 Confluence + 5 Meeting) |
| Entity SQLite | 1.000 |
| Confluence chunk / Meeting chunk | 1.614 / 21 |
| Backlink | 719 |

**Độ chính xác Concern Engine** (`tests/test_concern_engine.py`):
| Chỉ số | Giá trị |
|---|---|
| Precision (108 anomaly + 100 normal) | **0.92** |
| Recall | **1.00** |
| Concern tại `2025-05-30` | ~**241** (66 deadline · 139 stalled _(47 kinh niên)_ · 36 blocker · 0 cross-source) |

**Guardrails (đối kháng):** chặn **4/4** injection · **0/3** false positive · che `sk-…`/Bearer/PEM.

**Bộ test:** **97 pass** + 17 test sanitizer nội tuyến.

---

## 13a. Ví dụ báo cáo thật (`output/report.md`, 2025-05-30)

```markdown
## Cần xử lý hôm nay

Tổng quan rủi ro: 36 blocker · 66 quá hạn/sắp hết hạn · 139 trì trệ
(trong đó 47 trì trệ kinh niên).

1. [deadline_risk] FLINK-40 — severity 5 — Deadline đã quá hạn 2 ngày,
   status vẫn 'Reopened' [FLINK-40].
2. [deadline_risk] CASSANDRA-46 — severity 5 — ... [CASSANDRA-46].
   ...

## Rủi ro

- **Blocker chưa giải quyết (4 task):**
  - KAFKA-64 — Mở 11 ngày, status 'Blocked', ảnh hưởng 4 task [KAFKA-64].
- **Task trì trệ (4 task):**
  - CASSANDRA-22 — Chưa update 16 ngày, status 'needs-review' [CASSANDRA-22].
```

> **Mọi dòng đều có `[source_id]`** → có thể kiểm chứng ngược về Jira/Confluence/Meeting. Khối đầu trả lời thẳng câu hỏi của PM: _"Hôm nay làm gì trước?"_

---

## 14. Các quyết định kỹ thuật (và lý do)

| Quyết định       | Lựa chọn                                                  | Vì sao                                                                  |
| ---------------- | --------------------------------------------------------- | ----------------------------------------------------------------------- |
| Framework agent  | **OpenAI SDK + ReAct tự viết**                            | Kiểm soát từng tool-call; ~50 dòng, dễ debug; không "hộp đen"           |
| Lưu trữ          | **Kho kép SQLite + ChromaDB**                             | SQL cho dữ kiện cứng (đo được); vector cho ngữ nghĩa                    |
| Phát hiện rủi ro | **Luật SQL tất định**                                     | Đo precision/recall, tái lập 100%; LLM **chỉ** kể chuyện                |
| Trích dẫn        | **Tool trả `source_ids` + prompt cấm phát biểu vô nguồn** | Mọi claim đều kiểm chứng được; thà ghi "(không có dữ liệu)" còn hơn bịa |
| Báo cáo          | **Grounding bằng Concern Engine**                         | Mục "Rủi ro" neo vào phát hiện tất định, rồi LLM bồi ngữ cảnh           |
| Chống lỗi        | **Retry + fallback tất định**                             | Proxy chập chờn vẫn ra báo cáo có ích, không crash                      |

**Một câu tóm tắt:** _Tất định ở chỗ cần đo lường, LLM ở chỗ cần ngôn ngữ._

---

## 15. Bài học & Bug thật đã sửa (chỉ lộ khi chạy end-to-end)

1. **Field mismatch Connector→Chroma** (`text_content` vs `content`) → suýt index **0** chunk. _Sửa bằng field bridge._
2. **Jira `source="Apache"`** copy nguyên → **0 entity**. _Sửa: ấn định `"jira"`._
3. **Deadline over-flag** mọi task quá hạn → precision 0.29. _Sửa: cửa sổ gần hạn → 0.92._
4. **`julianday` không parse `+0000`** → so sánh bằng `substr(...,1,10)`.
5. **Proxy 403 chập chờn** → retry-with-backoff + fallback tất định.
6. **Báo cáo lệch loại** (toàn deadline) → top-N theo từng loại.

> **Bài học lớn:** _unit test trên fixture "sạch" giấu bug tích hợp._ Lần chạy end-to-end thật là bài test giá trị nhất. _Precision phụ thuộc tỉ lệ prevalence_ — một con số "precision" rời rạc dễ gây hiểu nhầm.

---

## 16. Hạn chế & Lộ trình (trung thực)

**Còn mở:**

1. **Cross-source recall = 0** trên ngày demo: cần _data work_ — một biên bản họp nhắc **đúng key** anomaly + từ khoá xung đột **trong cùng chunk**, và Jira `updated` nằm trong cửa sổ 48h. (Đã chẩn đoán: AIP-30 trượt chỉ vì cũ 4 ngày.)
2. **Chưa có lịch chạy/đẩy báo cáo** (cron + email/Slack/Teams) — hiện chỉ ghi file `output/`.
3. **Precision ở prevalence đầy đủ** (~0.5) chưa đo chính thức (con số 0.92 là trên mix lấy mẫu).
4. **Diff ngày-qua-ngày** đang seed mô phỏng; chạy theo lịch thật sẽ dùng snapshot trước thật.

**Đã ship gần đây:** MCP server (Week 5) · khối "Cần xử lý hôm nay" + luật stalled phân tầng · báo cáo đa ngôn ngữ (`REPORT_LANG`).

> Trình bày hạn chế một cách minh bạch chính là điểm cộng: hệ thống **không thổi phồng** kết quả.

---

## 17. Trạng thái dự án (theo plan v3.0)

| Tuần | Hạng mục                   | Hoàn thành |
| ---- | -------------------------- | ---------- |
| 1    | Thiết kế & Dữ liệu         | **100%**   |
| 2    | Ingestion & Knowledge Base | **100%**   |
| 3    | Report Agent               | **~100%**  |
| 4    | Concern Engine             | **~90%**   |
| 5    | MCP & Guardrails           | **100%**   |
| 6    | Đóng gói & Demo            | **~85%**   |

**Definition of Done (V1–V6):**

- ✅ V1: `run_agent` chạy end-to-end không crash (kể cả khi proxy throttle).
- ✅ V2: báo cáo có ≥ 5 trích dẫn hợp lệ.
- ✅ V3: phát hiện đủ 4 loại anomaly (có trong `concerns.json`).
- ✅ V4: precision/recall ≥ 80% (0.92 / 1.00 trên mix lấy mẫu).
- ✅ V5: guardrail chặn ≥ 3 injection (4/4, 0 false positive).
- ✅ V6: demo live `gpt-4o-mini` (HTTP 200, báo cáo tiếng Việt có trích dẫn).

---

## 18. Kịch bản demo trực tiếp (ngày mai)

**Chuẩn bị trước:** mở terminal env `VSF_prj`, đảm bảo `.env` có `OPENAI_API_KEY`.

1. **Giới thiệu bài toán** (1 phút) — 3 nguồn phân mảnh, rủi ro bị chôn vùi (slide 1–2).
2. **Chạy 1 lệnh:**
   ```bash
   python src/run_agent.py --date 2025-05-30
   ```
   → vừa chạy, vừa chỉ log: _ingest 1.222 doc → 241 concern → seed diff → Report Agent → xuất file_.
3. **Mở `output/report.md`** — đọc khối **"Cần xử lý hôm nay"**, chỉ ra **mọi dòng đều có `[source_id]`**.
4. **Mở `output/concerns.xlsx`** — hàng severity 5 tô đỏ; lọc theo loại rủi ro.
5. **Chứng minh tính tất định:**
   ```bash
   python -m src.agents.concern_engine --date 2025-05-30 --min-sev 5
   ```
   → cùng đầu vào ⇒ cùng đầu ra, không phụ thuộc LLM.
6. **Khoe MCP:** `python src/mcp/server.py` → mở `/docs`, gọi `GET /concerns?min_sev=5` kèm `X-API-Key`.
7. **(Tuỳ chọn) Guardrail:** `pytest src/guardrail/sanitizer.py` → chặn injection, che secret.

> **Phương án dự phòng:** nếu mạng/LLM lỗi khi demo → hệ thống tự rơi về **fallback_report** (vẫn ra báo cáo). Hãy nói rõ: _"đây chính là cơ chế suy giảm mượt đã thiết kế."_

---

## 19. Kết luận

**AI Project Intelligence Agent** là một agent **end-to-end, chạy được thật**:

- 🔗 **Ingest 3 nguồn** vào **kho kép** — mỗi kho làm đúng thế mạnh.
- 🎯 **Phát hiện rủi ro tất định** (4 luật, precision 0.92 / recall 1.00) — _đo được, tái lập được_.
- 📝 **Báo cáo có trích dẫn**, dẫn đầu bằng "Cần xử lý hôm nay", **đa ngôn ngữ**.
- 🛡️ **Guardrails** 2 biên + **MCP API** + **xuất Word/Excel**.
- 💪 **Bền vững**: retry + fallback → không bao giờ crash trước mặt người dùng.

**Thông điệp đọng lại:**

> _Để LLM làm phần nó giỏi nhất — viết — và để SQL làm phần nó giỏi nhất — phán xét sự thật. Kết quả: một báo cáo vừa **trôi chảy** vừa **đáng tin**._

### Cảm ơn — Q&A
