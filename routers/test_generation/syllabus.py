from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from database import get_db
from main import require_admin, templates
from .utils import require_test_gen_access

router = APIRouter(prefix="/test-generation")

@router.get("/syllabus-setting", response_class=HTMLResponse)
def syllabus_setting_page(request: Request):
    user = require_admin(request)
    conn = get_db()
    classes = conn.execute("SELECT * FROM classes ORDER BY name").fetchall()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    blocks = conn.execute("""
        SELECT sb.*, c.name as class_name, s.name as subject_name
        FROM syllabus_blocks sb
        JOIN classes c ON c.id = sb.class_id
        JOIN subjects s ON s.id = sb.subject_id
        ORDER BY c.name, s.name, sb.block_name
    """).fetchall()
    conn.close()
    return templates.TemplateResponse("test_generation/syllabus_setting.html", {
        "request": request, "user": user, "classes": classes, "subjects": subjects, "blocks": blocks
    })

@router.post("/syllabus-setting/add")
def add_syllabus_block(
    request: Request,
    class_id: int = Form(...),
    subject_id: int = Form(...),
    block_name: str = Form(...)
):
    require_admin(request)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO syllabus_blocks (class_id, subject_id, block_name) VALUES (?, ?, ?)",
            (class_id, subject_id, block_name.strip())
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return RedirectResponse(url="/test-generation/syllabus-setting?msg=added", status_code=303)

@router.post("/syllabus-setting/delete/{block_id}")
def delete_syllabus_block(block_id: int, request: Request):
    require_admin(request)
    conn = get_db()
    conn.execute("DELETE FROM question_bank WHERE syllabus_block_id = ?", (block_id,))
    conn.execute("DELETE FROM syllabus_blocks WHERE id = ?", (block_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/test-generation/syllabus-setting?msg=deleted", status_code=303)