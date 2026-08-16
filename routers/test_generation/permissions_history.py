import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from database import get_db
from main import require_admin, templates
from services.rich_export import generate_rich_docx, generate_rich_xlsx
from .utils import require_test_gen_access

router = APIRouter(prefix="/test-generation")

# ----------------------- Permissions (Admin Only) -----------------------
@router.get("/permissions", response_class=HTMLResponse)
def permissions_page(request: Request):
    user = require_admin(request)
    conn = get_db()
    perm = conn.execute("SELECT allow_teachers FROM test_permissions WHERE id = 1").fetchone()
    conn.close()
    allow = perm["allow_teachers"] if perm else 0
    return templates.TemplateResponse("test_generation/permissions.html", {
        "request": request,
        "user": user,
        "allow_teachers": allow,
    })


@router.post("/permissions/toggle")
def toggle_permission(request: Request):
    require_admin(request)
    conn = get_db()
    current = conn.execute("SELECT allow_teachers FROM test_permissions WHERE id = 1").fetchone()
    new_val = 0 if current and current["allow_teachers"] else 1
    conn.execute("INSERT OR REPLACE INTO test_permissions (id, allow_teachers) VALUES (1, ?)", (new_val,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/test-generation/permissions?msg=updated", status_code=303)


# ----------------------- History -----------------------
@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    user = require_test_gen_access(request)
    conn = get_db()
    tests = conn.execute("""
        SELECT gt.*, c.name as class_name, s.name as subject_name
        FROM generated_tests gt
        JOIN classes c ON c.id = gt.class_id
        JOIN subjects s ON s.id = gt.subject_id
        ORDER BY gt.created_at DESC
    """).fetchall()

    history = []
    for t in tests:
        if t["syllabus_block_ids"]:
            block_ids = json.loads(t["syllabus_block_ids"])
            if block_ids:
                blocks = conn.execute(f"""
                    SELECT block_name FROM syllabus_blocks
                    WHERE id IN ({','.join('?' for _ in block_ids)})
                """, block_ids).fetchall()
                block_names = ", ".join(b["block_name"] for b in blocks)
            else:
                block_names = ""
        else:
            block_names = ""
        t_dict = dict(t)
        t_dict["block_names"] = block_names
        history.append(t_dict)
    conn.close()
    return templates.TemplateResponse("test_generation/history.html", {
        "request": request,
        "user": user,
        "history": history,
    })


# ----------------------- Download History Test -----------------------
@router.get("/generate/download/{test_id}")
def download_history_test(test_id: int, request: Request, format: str = "docx"):
    user = require_test_gen_access(request)
    conn = get_db()
    test = conn.execute("SELECT * FROM generated_tests WHERE id=?", (test_id,)).fetchone()
    if not test:
        conn.close()
        return RedirectResponse(url="/test-generation/history?error=not_found", status_code=303)

    selected = json.loads(test["questions_json"])
    class_row = conn.execute("SELECT name FROM classes WHERE id=?", (test["class_id"],)).fetchone()
    subject_row = conn.execute("SELECT name FROM subjects WHERE id=?", (test["subject_id"],)).fetchone()
    block_ids = json.loads(test["syllabus_block_ids"])
    blocks_rows = conn.execute(f"""
        SELECT block_name FROM syllabus_blocks WHERE id IN ({','.join('?' for _ in block_ids)})
    """, block_ids).fetchall()
    blocks_names = [b["block_name"] for b in blocks_rows]
    conn.close()

    if format == "xlsx":
        file_bytes = generate_rich_xlsx(selected, class_row["name"], subject_row["name"], blocks_names,
                                        test["total_marks"], test["mcq_marks_each"], test["short_marks_each"],
                                        test["long_marks_each"], "A4", 10, 1)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"test_{class_row['name']}_{subject_row['name']}_{test['created_at'][:10]}.xlsx"
    else:
        file_bytes = generate_rich_docx(selected, class_row["name"], subject_row["name"], blocks_names,
                                        test["total_marks"], test["mcq_marks_each"], test["short_marks_each"],
                                        test["long_marks_each"], "A4", 10, 1)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"test_{class_row['name']}_{subject_row['name']}_{test['created_at'][:10]}.docx"

    return StreamingResponse(
        file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )