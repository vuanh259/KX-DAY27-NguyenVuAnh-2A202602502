# Day27 — HITL PR Review Agent

Lời giải gồm đủ Exercise 1–4 và giao diện Streamlit trong `app.py`.

## Chuẩn bị

```powershell
uv sync --python 3.12
```

Mở `.env` trên máy và điền:

- `OPENROUTER_API_KEY`: API key OpenRouter của bạn.
- `GITHUB_TOKEN`: PAT có scope `repo` để truy cập PR-Demo private.
- `GITHUB_USER`: username GitHub của người duyệt (`vuanh259`).

Tài khoản GitHub cũng cần được cấp quyền vào `VinUni-AI20k/PR-Demo`.
Không đưa `.env` hoặc file database lên GitHub.

## Kiểm thử không cần key

```powershell
uv run python -m unittest discover -s tests -v
```

Kiểm thử dùng graph và SQLite thật, giả lập GitHub/LLM nên không đăng comment.

## Chạy bài

```powershell
uv run python exercises/exercise_1_confidence.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
uv run python exercises/exercise_1_confidence.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/2
uv run python exercises/exercise_2_hitl.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
uv run python exercises/exercise_3_escalation.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/2
uv run python exercises/exercise_4_audit.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
uv run streamlit run app.py
```

Mở `http://localhost:8501`, nhập URL PR và bấm **Run review**.

- Confidence ≥ 0.73: tự đăng review.
- 0.58 ≤ confidence < 0.73: hiện diff, lý do và Approve / Reject / Edit.
- Confidence < 0.58: hỏi reviewer, phân tích lại với câu trả lời rồi đăng review.

Approve đăng comment thật dưới tài khoản tương ứng với PAT. Reject và Edit
không đăng; Edit lưu feedback như yêu cầu cơ bản của Lab. Viết lại review khi
Edit là bài bonus. Confidence do LLM tự báo cáo nên nhánh của PR thật có thể
khác giữa các lần chạy.

## Audit và mở lại phiên

```powershell
uv run python -m audit.replay --list
uv run python -m audit.replay --thread <thread_id>
uv run python exercises/exercise_4_audit.py --pr <PR_URL> --thread <thread_id>
```

UI có sidebar Recent sessions và lưu thread trong URL để khôi phục sau khi
tải lại trang. Phiên đã hoàn tất được đọc lại mà không gọi LLM hay đăng lại.
Node có interrupt chạy lại từ đầu khi resume nên dòng audit pending có thể
lặp, đúng cơ chế được giải thích trên VLearn.

SQLite là lựa chọn thay PostgreSQL được đề bài VLearn chấp nhận. Checkpoint
không bảo đảm đăng GitHub đúng một lần nếu tiến trình chết ngay sau khi GitHub
nhận comment nhưng trước lúc lưu kết quả node.

## Nộp bài

Repo đích: https://github.com/vuanh259/KX-DAY27-NguyenVuAnh-2A202602502

Sau khi chạy thử PR thật thành công, commit/push lời giải và nộp link repo lên
VLearn. Kiểm tra repo Public và không có `.env` hoặc database trong commit.
