# KẾ HOẠCH CẢI TIẾN (PM Roadmap)

**Góc nhìn:** Product/Project Manager — ưu tiên theo **giá trị cho người dùng** và **mức độ sẵn sàng đưa vào dùng thật**, không phải theo độ thú vị kỹ thuật.

> **Trọng tâm vòng này:** **Sẵn sàng thực tế (adoption)** — đưa sản phẩm từ "demo trên dữ liệu synthetic" tiến một bước về "PM chạy hằng ngày và nhận digest rủi ro ở nơi họ làm việc".

---

## 1. Tầm nhìn & người dùng

- **Người dùng:** Project Manager / Tech Lead theo dõi nhiều task qua Jira + Confluence + meeting notes.
- **Việc cần làm (JTBD):** mỗi sáng biết ngay *"hôm nay cần quyết gì, ai đang kẹt, deadline nào nguy hiểm, có gì mâu thuẫn giữa Jira và tài liệu"* — mà không phải tự đọc 1000 ticket.
- **Giá trị cốt lõi:** **một radar rủi ro hằng ngày *đáng tin* mà PM thực sự hành động theo.** Đáng tin = mỗi nhận định có trích dẫn kiểm chứng được; hành động theo = được giao tận nơi PM làm việc.
- **Hành trình sản phẩm:** `demo synthetic` ✅ (đang ở đây) → **`PM chạy hằng ngày, nhận qua Slack`** (vòng này) → `nối Jira/Confluence thật` → `nhiều dự án + dashboard`.

## 2. Hiện trạng (điểm xuất phát)

Đã xong (feature-complete trên synthetic): ingestion 3 nguồn, dual store, Report Agent có trích dẫn, Concern Engine 4 rule (precision 0.92 / recall 1.00 trên mẫu), MCP server, guardrail, exporters; 120 test xanh.

Khoảng trống *từ góc PM* (chính repo tự nêu): (a) chỉ đọc JSON dump — **chưa nối Jira/Confluence thật**; (b) **không có scheduler** nên lịch sử day-over-day không tích lũy → mục "Recent Changes" luôn rỗng khi demo; (c) báo cáo **không được giao** tới nơi PM làm việc (Slack/email/Teams); (d) precision nhạy theo prevalence; (e) **một API key bị lộ trong git history** (commit `d2657ea`).

## 3. Bảng sáng kiến ưu tiên (Now / Next / Later)

Thang điểm: Impact (giá trị PM) × Effort (công sức) → Ưu tiên. Metric = cách đo "đã đạt".

### 🟢 NOW — P0 (triển khai trong vòng này)

| Sáng kiến | Impact | Effort | Metric thành công |
| --- | --- | --- | --- |
| **Chạy hằng ngày + tích lũy lịch sử** (`scripts/daily_run.sh`, không `--reset`) | Cao | Thấp | Sau ≥2 ngày, mục "Thay đổi gần đây" có dữ liệu; `get_daily_diff` trả đúng thay đổi |
| **Giao báo cáo qua Slack (MVP)** (`src/delivery/slack.py`, env `SLACK_WEBHOOK_URL`) | Cao | Thấp | Mỗi lần chạy (khi bật webhook) digest tới Slack: tổng quan + top-5 theo severity |
| **Scaffolding nguồn live + secrets gate** (`.env.example`, `SECURITY.md`) | Cao | Thấp | `.env.example` đủ field Jira/Confluence/Slack + `SOURCE_MODE`; key lộ được nêu rõ phải xoay |

> Vì sao 3 việc này trước: chúng *nhỏ, làm được ngay, test được bằng synthetic*, và mở khóa giá trị adoption nền tảng — đặc biệt scheduler là **tiền đề** cho mọi tính năng "xu hướng / thay đổi tuần này" về sau.

### 🟡 NEXT — P1

| Sáng kiến | Impact | Effort | Metric thành công |
| --- | --- | --- | --- |
| **Connector Jira API live** (REST + JQL incremental) | Rất cao | Cao | Ingest từ Jira thật qua `SOURCE_MODE=api`; idempotent nhờ `sync_log` |
| **Connector Confluence API live** | Cao | Cao | Ingest từ Confluence Cloud thật |
| **Xoay key lộ + scrub git history** (`d2657ea`) | Cao (bảo mật) | Trung | Key cũ vô hiệu hóa; history được làm sạch (`git filter-repo`) |
| **Eval full-prevalence + SLO precision actionable** ✅ | Trung | Thấp | Đo trên toàn 856 normals: recall 1.0; precision overall 0.52, **actionable (sev≥3) 0.66**, top (sev≥4) 0.87. SLO đặt theo actionable; rule giữ nguyên (siết theo `needs-review` = overfit synthetic) |
| **Tăng recall cross-source + LLM verify** | Trung | Trung | Recall cross-source đo được, vượt mức demo (1–2 ca) |
| **Giao qua Email/Teams** | Trung | Thấp | Digest tới email/Teams ngoài Slack |

### 🔵 LATER — P2

| Sáng kiến | Impact | Effort | Ghi chú |
| --- | --- | --- | --- |
| **Rollup theo người phụ trách / theo epic** | Trung | Trung | "Ai đang quá tải"; nhóm theo `linked_jira_epics` |
| **Xu hướng theo thời gian** (blocker tuần này vs tuần trước) | Trung | Trung | *Mở khóa sau khi P0 scheduler tích lũy đủ lịch sử* |
| **Observability + theo dõi chi phí LLM** | Trung | Trung | Metric số concern theo ngày, latency, token/cost mỗi run |
| **Multi-project + dashboard nhẹ** | Trung | Cao | Vượt phạm vi 1 dự án (AIP) |

## 4. Rủi ro & phụ thuộc

- **Bảo mật chặn adoption:** P1 thêm Jira/Confluence token → **phải xử lý key lộ (`d2657ea`) trước** khi đưa creds thật vào. Xem `SECURITY.md`.
- **Phụ thuộc lịch sử:** "Xu hướng" và "Thay đổi tuần này" (P2) chỉ có nghĩa **sau khi** P0 scheduler đã tích lũy nhiều ngày snapshot.
- **Phụ thuộc môi trường thật:** connector live (P1) cần một instance Jira/Confluence thật + quyền truy cập để test đầy đủ — không thể nghiệm thu chỉ bằng synthetic.
- **Chi phí/độ ổn định LLM:** chạy hằng ngày làm tăng lệnh gọi LLM (đã có retry + fallback deterministic nên không crash; cần theo dõi chi phí — P2).

## 5. KPI tổng

- **Adoption:** số PM chạy/nhận digest hằng ngày; số ngày liên tục pipeline chạy không lỗi.
- **Độ tin:** precision đo trên full-prevalence đạt SLO; tỉ lệ nhận định có citation hợp lệ.
- **Tính hành động:** tỉ lệ digest được mở/click; số "Decisions Needed Today" được xử lý.

---

## Phụ lục — Chạy hằng ngày (scheduler)

`run_agent.sh` dùng `--reset` (baseline sạch cho demo). Để **tích lũy lịch sử** day-over-day, chạy `scripts/daily_run.sh` (KHÔNG `--reset`). Ví dụ cron 7:00 mỗi sáng:

```cron
0 7 * * *  cd /path/to/VSF_AI-Intelligent-Agent-For-PM && ./scripts/daily_run.sh >> logs/daily.log 2>&1
```

Hoặc systemd timer (`daily_run.service` + `daily_run.timer` với `OnCalendar=*-*-* 07:00:00`). Khi đặt `SLACK_WEBHOOK_URL`, mỗi lần chạy sẽ tự giao digest tới Slack.
