from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from database import get_db
from main import current_user, require_admin, templates

router = APIRouter(prefix="/test-generation")

# ===================== SYLLABUS SETTING (Phase 1) =====================
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

# ===================== QUESTION BANK (Phase 2) =====================
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
        # Comprehensions (passages) aur unke linked MCQs
        comprehensions = conn.execute("""
            SELECT * FROM question_bank
            WHERE syllabus_block_id = ? AND question_type = 'comprehension'
            ORDER BY id DESC
        """, (block_id,)).fetchall()
        # For each comprehension, fetch its MCQs
        for comp in comprehensions:
            comp["mcqs"] = conn.execute("""
                SELECT * FROM question_bank
                WHERE parent_comprehension_id = ? AND question_type = 'mcq'
                ORDER BY id DESC
            """, (comp["id"],)).fetchall()
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
    # Delete linked MCQs first
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