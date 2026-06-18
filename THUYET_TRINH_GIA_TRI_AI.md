---
marp: true
title: AI giúp được gì hơn — Ranh giới Deterministic vs AI
author: VSF
paginate: true
theme: default
---

<!--
Bộ slide bổ sung, trả lời thẳng 3 ý feedback demo:
  A. Tại sao không dùng Jira là single source of truth?
  B. Report đáp ứng nhu cầu cơ bản của PM trước.
  C. Apply AI rồi thì giúp được gì hơn?
Trục xuyên suốt: tách rõ phần DETERMINISTIC (tin cậy, đo được) và phần AI (ngôn ngữ,
ngữ nghĩa) — để nói chính xác AI thêm giá trị ở đâu, và CỐ Ý không làm gì.
Số liệu lấy từ lần chạy thật `./run_agent.sh 2025-05-30`: 120 test pass · 239 concern
(139 trì trệ · 62 deadline · 36 blocker · 2 xung đột nguồn) · report 4 câu hỏi PM.
-->

# AI giúp được gì hơn?

### Trả lời 3 câu hỏi từ feedback demo

> 1. Tại sao **không** dùng Jira làm _single source of truth_?
> 2. Report phải đáp ứng **nhu cầu cơ bản** của PM trước.
> 3. Apply AI rồi thì **giúp được gì hơn**?

**Trục trả lời:** tách rõ phần **Deterministic** (đáng tin, đo được) và phần **AI**
(ngôn ngữ, ngữ nghĩa) — để biết AI thêm giá trị _chính xác ở đâu_.

---

## Câu A — Jira CÓ là single source of truth... nhưng cho cái gì?

> Jira **là** nguồn chân lý cho **trạng thái có cấu trúc**.
> Jira **không phải** nguồn chân lý cho **thực tế dự án**.

|                     | Nguồn chân lý cho...                                 | Ai giữ?                        |
| ------------------- | ---------------------------------------------------- | ------------------------------ |
| **Trạng thái cứng** | status, assignee, due_date, priority                 | **Jira** ✅ (ta dùng đúng vậy) |
| **Thực tế dự án**   | _vì sao_ quyết vậy, _thực sự_ chốt gì, cam kết miệng | Confluence + Meeting Notes     |

Ta đưa thêm 2 nguồn **không phải để cạnh tranh** làm nguồn trạng thái —
mà để **phát hiện khi Jira nói sai sự thật**.

> Chỉ đọc Jira → **vĩnh viễn không thấy** Jira đang sai.

---

## Câu A — 3 thất bại thực tế của "chỉ Jira"

PM nào cũng gặp:

1. **Jira "Done", thực tế chưa xong** — standup nói "còn 1 bug" nhưng ticket đã đóng.
2. **Họp đổi scope, không ai update ticket** — quyết định sống trong meeting note trước Jira.
3. **Cam kết miệng không thành ticket** — "anh lo phần X" → không ai theo dõi.

**Mấu chốt:** Jira chỉ đúng bằng đúng mức **kỷ luật cập nhật** của con người.
Giả định "Jira = single source of truth" **đòi hỏi** mọi người update hoàn hảo,
tức thời — điều không bao giờ có. Và chi phí ép kỷ luật đó **chính là** cái
overhead thủ công mà AI đáng lẽ phải xoá đi.

---

## Ranh giới hệ thống — phần nào KHÔNG phải AI

> Xương sống tin cậy là **deterministic** — không phải AI.

| Thành phần                                        | Cơ chế            | Vì sao không dùng AI                                   |
| ------------------------------------------------- | ----------------- | ------------------------------------------------------ |
| **Concern Engine** (4 luật)                       | SQL tất định      | Đo được **precision 0.92 / recall 1.00**; tái lập 100% |
| **Diff ngày/kỳ** (`get_daily_diff`, `diff_since`) | SQL trên snapshot | Số học ngày trong code, không để LLM tính              |
| **Đếm rủi ro, xếp severity**                      | Quy tắc cố định   | Cùng input ⇒ cùng output, không "trôi"                 |

**Vì sao quan trọng:** _phán xét sự thật_ (task này có quá hạn không, blocker chặn
mấy task) phải **đo lường được** → thuộc về SQL. Ép LLM trả lời vừa chậm, vừa
không đo được, vừa có thể bịa.

---

## Phần AI THỰC SỰ làm — 3 việc

> AI vào đúng chỗ cần **ngôn ngữ** và **ngữ nghĩa** — nơi SQL bất lực.

1. **Phát hiện mâu thuẫn chéo nguồn** _(semantic)_
   So Jira ↔ meeting/doc → tìm "Jira Done nhưng họp nói còn pending". Cần hiểu _nghĩa_
   của câu trong biên bản, không khớp từ khoá cứng được.

2. **Tổng hợp + diễn giải có trích dẫn** _(narrative)_
   Biến 239 concern thô thành báo cáo PM đọc-là-hành-động, mỗi câu kèm `[source_id]`.

3. **Giao diện hỏi-đáp ngôn ngữ tự nhiên** _(ReAct)_
   PM hỏi _"tháng trước các task đổi gì so với nay?"_ → agent tự chọn tool, tự trả lời có dẫn nguồn.

---

## Câu C — Chỉ Jira vs Có AI Agent

| Nhu cầu PM hằng ngày       | Chỉ có Jira          | Có AI Agent                             |
| -------------------------- | -------------------- | --------------------------------------- |
| Hôm nay cần làm gì trước?  | Tự đọc 50–100 ticket | **5 việc cần quyết** đẩy lên đầu        |
| Deadline nào nguy?         | Tự nhớ / lọc tay     | Tự tính, xếp theo độ gấp + còn mấy ngày |
| Jira ↔ họp có lệch nhau?   | **Không thể biết**   | **Tự phát hiện** (FLINK-1, AIP-30)      |
| "Tuần/tháng trước đổi gì?" | Tự dò lịch sử        | Trả lời ngay (`diff_since`)             |
| Tốn bao lâu?               | 30–60 phút/ngày      | **< 2 phút**, có trích dẫn kiểm chứng   |

> AI **không thay** Jira — AI lo **đúng phần Jira không biết**: mâu thuẫn, ngữ cảnh, hỏi-đáp.

---

## AI CỐ Ý không làm gì — ranh giới tin cậy

> Chính vì giới hạn rõ ràng nên PM **tin được**.

- ❌ **Không tự ý sửa Jira** — chỉ đọc + đối chiếu, không ghi ngược.
- ❌ **Không phán đoán khi không có nguồn** — tool rỗng ⇒ ghi _"(Không tìm thấy dữ liệu xác thực.)"_.
- ❌ **Không bịa** id / ngày / tên / status — mọi claim phải có `[source_id]`.
- ❌ **Không thay PM ra quyết định** — chỉ **đưa việc cần quyết lên đầu**, người quyết vẫn là PM.

> Triết lý: _Để LLM làm phần nó giỏi nhất — viết — và để SQL làm phần nó giỏi nhất —
> phán xét sự thật._

---

## Wow moment — cross-source conflict (đòn bẩy của câu A)

Trên dữ liệu demo, agent tự nổi **đúng 2 mâu thuẫn** mà _chỉ-đọc-Jira sẽ bỏ lỡ_:

```text
## ⚡ Cần bạn quyết hôm nay
1. FLINK-1 — Jira: Closed, nhưng meeting 27/05 nói "re-open vì test còn fail" [FLINK-1]
2. AIP-30  — Jira: Done,   nhưng meeting 28/05 nói "PR vẫn pending review"   [AIP-30]
```

- Phát hiện bằng **semantic** (hiểu "re-open / still failing / pending"), không khớp từ cứng.
- Có **guard thời gian**: chỉ báo khi _ngày họp ≥ ngày Jira đóng task_ (tránh báo nhầm lịch sử cũ).
- Nằm **ngay đầu** mục "Cần bạn quyết" → trả lời câu A _và_ B trong một dòng.

> Thành thật: mới 2 ca, trên data cấy. Đây là **lát cắt nhỏ nhưng giá-trị-cao** —
> phần còn lại của báo cáo (deadline/blocker/stalled) vẫn suy ra được từ Jira.

---

## Câu B — Report giờ map 1-1 vào 4 câu hỏi PM

**Trước:** 5 mục góc-nhìn-analyst (Priority / Overview / Changes / Concerns gộp / Next).
**Giờ:** 6 mục theo **đúng thứ tự PM cần quyết**, có icon, **quyết-định-trước**:

| Mục báo cáo              | Câu hỏi PM                                         |
| ------------------------ | -------------------------------------------------- |
| ⚡ Cần bạn quyết hôm nay | ④ Cần tôi quyết gì? _(conflict + blocker dẫn đầu)_ |
| 🚫 Đang bị chặn          | ② Ai/cái gì kẹt?                                   |
| ⏰ Deadline nguy hiểm    | ③ Deadline nào nguy?                               |
| 🔄 Thay đổi gần đây      | ① Gần đây đổi gì?                                  |
| 📋 Tồn đọng (gộp 1 số)   | Cái gì âm thầm dồn lại?                            |

> Citation đúng task id (`[KAFKA-64]`) → PM **click thẳng về Jira**, không phải mở lại để verify.

---

## Đúc kết

> **Jira là single source of truth — cho TRẠNG THÁI.**
> **AI lo phần Jira không biết: mâu thuẫn chéo nguồn · ngữ cảnh · hỏi-đáp.**

- **A.** Không phủ nhận Jira — ta dùng Jira làm nguồn trạng thái, và thêm 2 nguồn để
  **bắt lỗi Jira** (việc Jira đơn nguồn không bao giờ làm được).
- **B.** Report tái cấu trúc theo 4 câu hỏi PM, quyết-định-trước, trích dẫn click được.
- **C.** AI thêm giá trị ở **3 việc ngôn ngữ/ngữ nghĩa** — và **cố ý** không đụng phần
  cần đo lường, để giữ độ tin cậy.

**Một câu cho buổi demo:**

> _Phần đáng tin để deterministic lo; phần cần ngôn ngữ để AI lo. PM nhận một báo cáo
> vừa trôi chảy, vừa kiểm chứng được — và biết ngay phải quyết gì hôm nay._
