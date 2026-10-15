# Kết quả kiểm thử — 15/10/2026

## Kiểm thử tự động

Python 3.12, dependency cài bằng uv theo `uv.lock`.

```text
python -m unittest discover -s tests -v
Ran 13 tests
OK
```

Các test dùng LangGraph, SQLite và Streamlit AppTest thật, giả lập LLM/GitHub.
Bao phủ ba nhánh, ngưỡng 0.58/0.73, approve/reject/edit, escalation/refinement,
audit, khôi phục checkpoint, không đăng lại khi mở phiên đã xong và lỗi posting.

## Kiểm thử dịch vụ thật

GitHub API và OpenRouter đều dùng key riêng trong `.env`; không đưa key lên Git.
Tài khoản xác thực: `vuanh259`. Repo bài làm Public.

| Trường hợp | Kết quả |
|---|---|
| Draft PR #1, lần đầu | Confidence 65%, dừng chờ duyệt; Reject sau khi phát hiện nhận xét sai về `sorted([])`; final_action `rejected`, không đăng |
| Draft PR #1, sau cải thiện prompt | Confidence 55%, escalation; trả lời 4 câu hỏi dựa trên code; refined confidence 65%, final_action `committed` |
| Draft PR #2, ví dụ login/migration cố ý có lỗi | Confidence 50%, escalation; trả lời câu hỏi, tổng hợp lại; final_action `committed`, confidence vẫn 50% vì còn thiếu ngữ cảnh production |

Comment thật đã được đọc lại từ GitHub API để xác minh:

- [PR #1 — review task priority](https://github.com/vuanh259/KX-DAY27-NguyenVuAnh-2A202602502/pull/1#issuecomment-6184763117)
- [PR #2 — review login/migration](https://github.com/vuanh259/KX-DAY27-NguyenVuAnh-2A202602502/pull/2#issuecomment-6184770077)

Các comment đều hiện dưới username `vuanh259`. Không merge hai draft PR thử.
PR #2 chỉ là dữ liệu tổng hợp để luyện review, không được triển khai.

## Audit trên máy chạy

Các thread thực tế có thể replay khi còn file SQLite trên máy:

```powershell
uv run python -m audit.replay --thread live-demo-2c056281-ce63-4ec7-be9d-b52600f8f50d
uv run python -m audit.replay --thread live-demo-quality-review
uv run python -m audit.replay --thread live-demo-escalation-review
```

Database không được commit. Người clone repo mới có thể tạo phiên riêng bằng
UI hoặc CLI. Các dòng pending lặp lại là do node interrupt chạy lại khi resume.

## Phạm vi xác minh

Ba nhánh và approve/reject/edit đã được kiểm thử tự động. Kiểm thử với dịch vụ
thật xác minh fetch, LLM analysis, human rejection, escalation, refinement,
đăng comment và audit. Không ép confidence để làm đẹp kết quả; LLM tự chấm điểm.

Repo private `VinUni-AI20k/PR-Demo` của lớp chưa truy cập được với tài khoản này,
nên hai draft PR trong repo cá nhân được dùng thay thế theo yêu cầu người dùng.
Điều kiện chạy đúng hai PR chuẩn của lớp còn phụ thuộc quyền truy cập repo đó.

Người dùng đã đăng nhập lại VLearn; form nộp bài hiện truy cập được.
Trạng thái nộp cuối cùng được xác minh riêng trên VLearn.


## Kiểm thử bốn bonus

Sáu test bổ sung trong `tests/test_bonuses.py` kiểm tra:

- Edit gửi đúng feedback/diff/review cũ tới LLM và không đăng trước lần duyệt mới.
- Hai task Send có interrupt riêng, đóng/mở checkpoint giữa các câu trả lời;
  một câu trả lời chưa được synthesize hoặc đăng; đủ hai mới tổng hợp đúng một lần.
- Fork checkpoint vào thread mới, chọn quyết định khác mà source không đổi;
  từ chối ghi đè thread có sẵn và từ chối fork checkpoint không chờ duyệt.
- Calibration chỉ đếm quyết định cuối, không đếm trùng Edit và pending.
- UI Edit và UI hai reviewer chạy đủ các lượt form.

Đã thử thêm với LLM/GitHub thật từ checkpoint cũ:

- Source `live-demo-2c056281-ce63-4ec7-be9d-b52600f8f50d`: kết quả giữ `rejected`.
- Fork `live-bonus-time-travel-edit`: Edit yêu cầu sửa nhận xét sai; LLM trả
  confidence 75% và chỉ còn đề xuất, vẫn dừng chờ duyệt lại dù vượt ngưỡng auto.
- Sau Approve, fork có kết quả `committed`; audit chứa `time_travel`, `rewrite`,
  `edit`, `approve`, `commit`. Calibration có thêm quyết định approve mới.

Fan-out đã kiểm thử bằng graph/checkpoint/UI thật với LLM/GitHub giả lập;
không khẳng định có hai người thật tham gia review.

Toàn bộ 13 test cũng đã chạy qua với `LANGGRAPH_STRICT_MSGPACK=true`.
Checkpointer đăng ký rõ hai kiểu Pydantic của lab trong
`exercises/checkpoints.py`, nên đọc lại checkpoint không phụ thuộc chế độ
deserialize permissive của LangGraph.
