import json
from datetime import date
from io import BytesIO

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from database import get_db
from main import current_user, require_admin, templates

from services.test_generator import (
    select_questions,
    generate_docx,
    generate_xlsx,
    generate_answer_key_docx,
)

router = APIRouter(prefix="/test-generation")
@router.get("", response_class=HTMLResponse)
def test_generation_index(request: Request):
    user = require_admin(request) if request.session.get("user", {}).get("role") == "admin" else require_login(request)
    conn = get_db()
    perm = conn.execute("SELECT allow_teachers FROM test_permissions WHERE id = 1").fetchone()
    conn.close()
    allow_teachers = perm["allow_teachers"] if perm else 0
    # Agar teacher hai aur allow_teachers = 0 to redirect with error
    if user.get("role") != "admin" and not allow_teachers:
        return RedirectResponse(url="/dashboard?error=no_access", status_code=303)
    return templates.TemplateResponse("test_generation/index.html", {
        "request": request,
        "user": user,
    })
# ===================== SYLLABUS SETTING =====================
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
        "request": request,
        "user": user,
        "classes": classes,
        "subjects": subjects,
        "blocks": blocks
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

# ===================== QUESTION BANK =====================
@router.get("/question-bank", response_class=HTMLResponse)
def question_bank_page(
    request: Request,
    class_id: int | None = None,
    subject_id: int | None = None,
    block_id: int | None = None
):
    user = require_admin(request)
    conn = get_db()
    classes = conn.execute("SELECT * FROM classes ORDER BY name").fetchall()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    blocks = []
    selected_class = None
    selected_subject = None
    selected_block = None
    questions_mcq = []
    questions_short = []
    questions_long = []
    comprehensions = []

    if class_id and subject_id:
        blocks = conn.execute("""
            SELECT * FROM syllabus_blocks WHERE class_id = ? AND subject_id = ?
            ORDER BY block_name
        """, (class_id, subject_id)).fetchall()
        selected_class = class_id
        selected_subject = subject_id

    if block_id:
        selected_block = block_id
        questions_mcq = conn.execute("""
            SELECT * FROM question_bank
            WHERE syllabus_block_id = ? AND question_type = 'mcq' AND parent_comprehension_id IS NULL
            ORDER BY id DESC
        """, (block_id,)).fetchall()
        questions_short = conn.execute("""
            SELECT * FROM question_bank
            WHERE syllabus_block_id = ? AND question_type = 'short'
            ORDER BY id DESC
        """, (block_id,)).fetchall()
        questions_long = conn.execute("""
            SELECT * FROM question_bank
            WHERE syllabus_block_id = ? AND question_type = 'long'
            ORDER BY id DESC
        """, (block_id,)).fetchall()

        comprehensions = conn.execute("""
            SELECT * FROM question_bank
            WHERE syllabus_block_id = ? AND question_type = 'comprehension'
            ORDER BY id DESC
        """, (block_id,)).fetchall()

        comprehensions_with_mcqs = []
        for comp in comprehensions:
            comp_dict = dict(comp)
            comp_dict["mcqs"] = conn.execute("""
                SELECT * FROM question_bank
                WHERE parent_comprehension_id = ? AND question_type = 'mcq'
                ORDER BY id DESC
            """, (comp["id"],)).fetchall()
            comprehensions_with_mcqs.append(comp_dict)
        comprehensions = comprehensions_with_mcqs

    conn.close()
    return templates.TemplateResponse("test_generation/question_bank.html", {
        "request": request,
        "user": user,
        "classes": classes,
        "subjects": subjects,
        "blocks": blocks,
        "selected_class": selected_class,
        "selected_subject": selected_subject,
        "selected_block": selected_block,
        "questions_mcq": questions_mcq,
        "questions_short": questions_short,
        "questions_long": questions_long,
        "comprehensions": comprehensions,
    })

# MCQ CRUD
@router.post("/question-bank/mcq/add")
def add_mcq(
    request: Request,
    block_id: int = Form(...),
    question_text: str = Form(...),
    option_a: str = Form(...),
    option_b: str = Form(...),
    option_c: str = Form(...),
    option_d: str = Form(...),
    correct_answer: str = Form(...),
):
    require_admin(request)
    conn = get_db()
    conn.execute("""
        INSERT INTO question_bank (syllabus_block_id, question_type, question_text,
                                  option_a, option_b, option_c, option_d, correct_answer)
        VALUES (?, 'mcq', ?, ?, ?, ?, ?, ?)
    """, (block_id, question_text, option_a, option_b, option_c, option_d, correct_answer))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=added", status_code=303)

@router.post("/question-bank/mcq/edit/{qid}")
def edit_mcq(
    qid: int,
    request: Request,
    question_text: str = Form(...),
    option_a: str = Form(...),
    option_b: str = Form(...),
    option_c: str = Form(...),
    option_d: str = Form(...),
    correct_answer: str = Form(...),
):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("""
        UPDATE question_bank
        SET question_text=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_answer=?
        WHERE id=?
    """, (question_text, option_a, option_b, option_c, option_d, correct_answer, qid))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=updated", status_code=303)

@router.post("/question-bank/mcq/delete/{qid}")
def delete_mcq(qid: int, request: Request):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=deleted", status_code=303)

# Short Questions CRUD
@router.post("/question-bank/short/add")
def add_short(
    request: Request,
    block_id: int = Form(...),
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_admin(request)
    conn = get_db()
    conn.execute("""
        INSERT INTO question_bank (syllabus_block_id, question_type, question_text, answer_text)
        VALUES (?, 'short', ?, ?)
    """, (block_id, question_text, answer_text))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=added", status_code=303)

@router.post("/question-bank/short/edit/{qid}")
def edit_short(
    qid: int,
    request: Request,
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("UPDATE question_bank SET question_text=?, answer_text=? WHERE id=?", (question_text, answer_text, qid))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=updated", status_code=303)

@router.post("/question-bank/short/delete/{qid}")
def delete_short(qid: int, request: Request):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=deleted", status_code=303)

# Long Questions CRUD
@router.post("/question-bank/long/add")
def add_long(
    request: Request,
    block_id: int = Form(...),
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_admin(request)
    conn = get_db()
    conn.execute("""
        INSERT INTO question_bank (syllabus_block_id, question_type, question_text, answer_text)
        VALUES (?, 'long', ?, ?)
    """, (block_id, question_text, answer_text))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=added", status_code=303)

@router.post("/question-bank/long/edit/{qid}")
def edit_long(
    qid: int,
    request: Request,
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("UPDATE question_bank SET question_text=?, answer_text=? WHERE id=?", (question_text, answer_text, qid))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=updated", status_code=303)

@router.post("/question-bank/long/delete/{qid}")
def delete_long(qid: int, request: Request):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=deleted", status_code=303)

# Comprehension CRUD
@router.post("/question-bank/comprehension/add")
def add_comprehension(
    request: Request,
    block_id: int = Form(...),
    passage: str = Form(...),
):
    require_admin(request)
    conn = get_db()
    conn.execute("""
        INSERT INTO question_bank (syllabus_block_id, question_type, comprehension_passage)
        VALUES (?, 'comprehension', ?)
    """, (block_id, passage))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=added", status_code=303)

@router.post("/question-bank/comprehension/edit/{pid}")
def edit_comprehension(
    pid: int,
    request: Request,
    passage: str = Form(...),
):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (pid,)).fetchone()["syllabus_block_id"]
    conn.execute("UPDATE question_bank SET comprehension_passage=? WHERE id=?", (passage, pid))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=updated", status_code=303)

@router.post("/question-bank/comprehension/delete/{pid}")
def delete_comprehension(pid: int, request: Request):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (pid,)).fetchone()["syllabus_block_id"]
    conn.execute("DELETE FROM question_bank WHERE parent_comprehension_id = ?", (pid,))
    conn.execute("DELETE FROM question_bank WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=deleted", status_code=303)

# Comprehension MCQ CRUD
@router.post("/question-bank/comprehension/mcq/add")
def add_comprehension_mcq(
    request: Request,
    block_id: int = Form(...),
    parent_id: int = Form(...),
    question_text: str = Form(...),
    option_a: str = Form(...),
    option_b: str = Form(...),
    option_c: str = Form(...),
    option_d: str = Form(...),
    correct_answer: str = Form(...),
):
    require_admin(request)
    conn = get_db()
    conn.execute("""
        INSERT INTO question_bank (syllabus_block_id, question_type, parent_comprehension_id,
                                  question_text, option_a, option_b, option_c, option_d, correct_answer)
        VALUES (?, 'mcq', ?, ?, ?, ?, ?, ?, ?)
    """, (block_id, parent_id, question_text, option_a, option_b, option_c, option_d, correct_answer))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=added", status_code=303)

@router.post("/question-bank/comprehension/mcq/edit/{qid}")
def edit_comprehension_mcq(
    qid: int,
    request: Request,
    question_text: str = Form(...),
    option_a: str = Form(...),
    option_b: str = Form(...),
    option_c: str = Form(...),
    option_d: str = Form(...),
    correct_answer: str = Form(...),
):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("""
        UPDATE question_bank
        SET question_text=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_answer=?
        WHERE id=?
    """, (question_text, option_a, option_b, option_c, option_d, correct_answer, qid))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=updated", status_code=303)

@router.post("/question-bank/comprehension/mcq/delete/{qid}")
def delete_comprehension_mcq(qid: int, request: Request):
    require_admin(request)
    conn = get_db()
    block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
    conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank?block_id={block_id}&msg=deleted", status_code=303)

# ===================== TEST GENERATION =====================
@router.get("/generate", response_class=HTMLResponse)
def generate_page(request: Request):
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
    return templates.TemplateResponse("test_generation/generate_test.html", {
        "request": request,
        "user": user,
        "classes": classes,
        "subjects": subjects,
        "blocks": blocks
    })

@router.post("/generate/download")
async def generate_download(request: Request):
    user = require_admin(request)
    form = await request.form()
    class_id = int(form.get("class_id"))
    subject_id = int(form.get("subject_id"))
    block_ids = form.getlist("block_ids")
    if not block_ids:
        return RedirectResponse(url="/test-generation/generate?error=no_blocks", status_code=303)

    mcq_count = int(form.get("mcq_count", 0))
    short_count = int(form.get("short_count", 0))
    long_count = int(form.get("long_count", 0))
    comp_count = int(form.get("comp_count", 0))
    mcq_marks = float(form.get("mcq_marks", 1))
    short_marks = float(form.get("short_marks", 2))
    long_marks = float(form.get("long_marks", 4))
    page_size = form.get("page_size", "A4")
    font_size = int(form.get("font_size", 10))
    num_pages = int(form.get("num_pages", 1))
    output_format = form.get("output_format", "docx")

    conn = get_db()
    class_row = conn.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
    subject_row = conn.execute("SELECT name FROM subjects WHERE id=?", (subject_id,)).fetchone()
    blocks_rows = conn.execute(f"""
        SELECT id, block_name FROM syllabus_blocks
        WHERE id IN ({','.join('?' for _ in block_ids)})
    """, block_ids).fetchall()
    blocks_names = [b["block_name"] for b in blocks_rows]

    try:
        selected = select_questions(conn, block_ids, mcq_count, short_count, long_count, comp_count)
    except ValueError as e:
        conn.close()
        return RedirectResponse(url=f"/test-generation/generate?error={str(e)}", status_code=303)

    total_marks = mcq_count * mcq_marks + short_count * short_marks + long_count * long_marks
    for comp in selected["comprehensions"]:
        total_marks += len(comp["mcqs"]) * mcq_marks

    if output_format == "docx":
        file_bytes = generate_docx(selected, class_row["name"], subject_row["name"], blocks_names,
                                   total_marks, mcq_marks, short_marks, long_marks,
                                   page_size, font_size, num_pages)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"test_{class_row['name']}_{subject_row['name']}_{date.today().isoformat()}.docx"
    else:
        file_bytes = generate_xlsx(selected, class_row["name"], subject_row["name"], blocks_names,
                                   total_marks, mcq_marks, short_marks, long_marks,
                                   page_size, font_size, num_pages)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"test_{class_row['name']}_{subject_row['name']}_{date.today().isoformat()}.xlsx"

    questions_json = json.dumps({
        "mcqs": [dict(q) for q in selected["mcqs"]],
        "short": [dict(q) for q in selected["short"]],
        "long": [dict(q) for q in selected["long"]],
        "comprehensions": [{"passage": dict(comp["passage"]), "mcqs": [dict(m) for m in comp["mcqs"]]} for comp in selected["comprehensions"]],
    }, default=str)
    answer_key_json = json.dumps({
        "mcqs": [q["correct_answer"] for q in selected["mcqs"]],
        "comprehension_mcqs": [m["correct_answer"] for comp in selected["comprehensions"] for m in comp["mcqs"]],
        "short_answers": [q["answer_text"] for q in selected["short"]],
        "long_answers": [q["answer_text"] for q in selected["long"]],
    }, default=str)

    conn.execute("""
        INSERT INTO generated_tests (class_id, subject_id, syllabus_block_ids, total_marks,
                                     mcq_count, short_count, long_count, comprehension_count,
                                     mcq_marks_each, short_marks_each, long_marks_each,
                                     questions_json, answer_key_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (class_id, subject_id, json.dumps(block_ids), total_marks,
          mcq_count, short_count, long_count, comp_count,
          mcq_marks, short_marks, long_marks, questions_json, answer_key_json))
    conn.commit()
    conn.close()

    return StreamingResponse(
        file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.post("/generate/answer-key")
async def generate_answer_key(request: Request):
    user = require_admin(request)
    form = await request.form()
    test_id = int(form.get("test_id"))
    conn = get_db()
    test = conn.execute("SELECT * FROM generated_tests WHERE id=?", (test_id,)).fetchone()
    if not test:
        conn.close()
        return RedirectResponse(url="/test-generation/history?error=not_found", status_code=303)
    selected = json.loads(test["questions_json"])
    class_row = conn.execute("SELECT name FROM classes WHERE id=?", (test["class_id"],)).fetchone()
    subject_row = conn.execute("SELECT name FROM subjects WHERE id=?", (test["subject_id"],)).fetchone()
    blocks_rows = conn.execute(f"""
        SELECT block_name FROM syllabus_blocks WHERE id IN ({test['syllabus_block_ids']})
    """).fetchall()
    blocks_names = [b["block_name"] for b in blocks_rows]
    conn.close()

    file_bytes = generate_answer_key_docx(selected, class_row["name"], subject_row["name"], blocks_names, test["total_marks"])
    filename = f"answer_key_{class_row['name']}_{subject_row['name']}_{date.today().isoformat()}.docx"
    return StreamingResponse(
        file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ===================== PERMISSIONS =====================
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
    user = require_admin(request)
    conn = get_db()
    current = conn.execute("SELECT allow_teachers FROM test_permissions WHERE id = 1").fetchone()
    new_val = 0 if current and current["allow_teachers"] else 1
    conn.execute("INSERT OR REPLACE INTO test_permissions (id, allow_teachers) VALUES (1, ?)", (new_val,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/test-generation/permissions?msg=updated", status_code=303)

# ===================== HISTORY =====================
@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    user = require_admin(request)
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

@router.get("/generate/download/{test_id}")
def download_history_test(test_id: int, request: Request, format: str = "docx"):
    user = require_admin(request)
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
        file_bytes = generate_xlsx(selected, class_row["name"], subject_row["name"], blocks_names,
                                   test["total_marks"], test["mcq_marks_each"], test["short_marks_each"],
                                   test["long_marks_each"], "A4", 10, 1)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"test_{class_row['name']}_{subject_row['name']}_{test['created_at'][:10]}.xlsx"
    else:
        file_bytes = generate_docx(selected, class_row["name"], subject_row["name"], blocks_names,
                                   test["total_marks"], test["mcq_marks_each"], test["short_marks_each"],
                                   test["long_marks_each"], "A4", 10, 1)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"test_{class_row['name']}_{subject_row['name']}_{test['created_at'][:10]}.docx"

    return StreamingResponse(
        file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )