import io
from datetime import date

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from database import get_db
from main import templates
from services.test_generator import select_questions
from services.rich_export import generate_rich_docx, generate_rich_xlsx, generate_answer_key_rich_docx
from .utils import require_test_gen_access

router = APIRouter(prefix="/test-generation")


@router.get("/generate", response_class=HTMLResponse)
def generate_page(request: Request):
    user = require_test_gen_access(request)
    conn = get_db()
    try:
        classes = conn.execute("SELECT * FROM classes ORDER BY name").fetchall()
        subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
        blocks = conn.execute("""
            SELECT sb.*, c.name as class_name, s.name as subject_name
            FROM syllabus_blocks sb
            JOIN classes c ON c.id = sb.class_id
            JOIN subjects s ON s.id = sb.subject_id
            ORDER BY c.name, s.name, sb.block_name
        """).fetchall()
    finally:
        conn.close()
    return templates.TemplateResponse("test_generation/generate_test.html", {
        "request": request, "user": user, "classes": classes, "subjects": subjects, "blocks": blocks
    })


@router.post("/generate/download")
async def generate_download(request: Request):
    user = require_test_gen_access(request)
    form = await request.form()

    try:
        class_id = int(form.get("class_id", 0))
        subject_id = int(form.get("subject_id", 0))
    except (TypeError, ValueError):
        return RedirectResponse(url="/test-generation/generate?error=invalid_class_or_subject", status_code=303)

    block_ids = form.getlist("block_ids")
    if not block_ids:
        return RedirectResponse(url="/test-generation/generate?error=no_blocks", status_code=303)

    mcq_count = int(form.get("mcq_count", 10))
    mcq_marks = int(form.get("mcq_marks", 1))
    short_count = int(form.get("short_count", 5))
    short_marks = int(form.get("short_marks", 2))
    long_count = int(form.get("long_count", 2))
    long_marks = int(form.get("long_marks", 4))
    comp_count = int(form.get("comp_count", 1))
    page_size = form.get("page_size", "A4")
    font_size = int(form.get("font_size", 10))
    num_pages = int(form.get("num_pages", 1))
    output_format = form.get("output_format", "docx")

    total_marks = (mcq_count * mcq_marks) + (short_count * short_marks) + (long_count * long_marks)

    conn = get_db()
    try:
        class_row = conn.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
        subject_row = conn.execute("SELECT name, default_direction FROM subjects WHERE id = ?", (subject_id,)).fetchone()
        class_name = class_row["name"] if class_row else ""
        subject_name = subject_row["name"] if subject_row else ""
        default_direction = subject_row["default_direction"] if subject_row else "ltr"

        block_ids_int = [int(b) for b in block_ids]
        placeholders = ",".join(["?"] * len(block_ids_int))
        blocks_rows = conn.execute(
            f"SELECT block_name FROM syllabus_blocks WHERE id IN ({placeholders})",
            block_ids_int
        ).fetchall()
        blocks_names = [row["block_name"] for row in blocks_rows] if blocks_rows else []

        selected = select_questions(conn, block_ids_int, mcq_count, short_count, long_count, comp_count)
    except ValueError as ve:
        return RedirectResponse(url=f"/test-generation/generate?error=insufficient_questions&msg={ve}", status_code=303)
    finally:
        conn.close()

    if output_format == "xlsx":
        file_stream = generate_rich_xlsx(
            selected, class_name, subject_name, blocks_names, total_marks,
            mcq_marks, short_marks, long_marks, page_size, font_size, num_pages, default_direction
        )
        filename = f"test_{date.today().strftime('%Y%m%d')}.xlsx"
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        file_stream = generate_rich_docx(
            selected, class_name, subject_name, blocks_names, total_marks,
            mcq_marks, short_marks, long_marks, page_size, font_size, num_pages, default_direction
        )
        filename = f"test_{date.today().strftime('%Y%m%d')}.docx"
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    file_stream.seek(0)
    return StreamingResponse(
        file_stream,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )