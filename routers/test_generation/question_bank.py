import os
import uuid

from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from database import get_db
from main import templates
from services.sanitizer import clean_html
from .utils import require_test_gen_access

router = APIRouter(prefix="/test-generation")

# ===================== QUESTION BANK PAGE (Library) =====================
@router.get("/question-bank", response_class=HTMLResponse)
def question_bank_library(
    request: Request,
    class_id: str = "",
    subject_id: str = "",
    block_id: str = ""
):
    user = require_test_gen_access(request)
    conn = get_db()
    classes = conn.execute("SELECT * FROM classes ORDER BY name").fetchall()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()

    query = """
        SELECT sb.id, sb.block_name, sb.class_id, sb.subject_id,
               c.name as class_name, s.name as subject_name,
               (SELECT COUNT(*) FROM question_bank q WHERE q.syllabus_block_id = sb.id AND q.question_type = 'mcq') as mcq_count,
               (SELECT COUNT(*) FROM question_bank q WHERE q.syllabus_block_id = sb.id AND q.question_type = 'short') as short_count,
               (SELECT COUNT(*) FROM question_bank q WHERE q.syllabus_block_id = sb.id AND q.question_type = 'long') as long_count,
               (SELECT COUNT(*) FROM question_bank q WHERE q.syllabus_block_id = sb.id AND q.question_type = 'comprehension') as comp_count
        FROM syllabus_blocks sb
        JOIN classes c ON c.id = sb.class_id
        JOIN subjects s ON s.id = sb.subject_id
        WHERE 1=1
    """
    params = []

    if class_id.strip():
        query += " AND sb.class_id = ?"
        params.append(int(class_id))
    if subject_id.strip():
        query += " AND sb.subject_id = ?"
        params.append(int(subject_id))
    if block_id.strip():
        query += " AND sb.id = ?"
        params.append(int(block_id))

    query += " ORDER BY c.name, s.name, sb.block_name"

    blocks = conn.execute(query, params).fetchall()
    conn.close()

    return templates.TemplateResponse("test_generation/question_bank_library.html", {
        "request": request,
        "user": user,
        "classes": classes,
        "subjects": subjects,
        "blocks": blocks,
        "selected_class": class_id.strip(),
        "selected_subject": subject_id.strip(),
        "selected_block_id": block_id.strip(),
    })

# ===================== QUESTION BANK EDITOR =====================
@router.get("/question-bank/editor/{block_id}", response_class=HTMLResponse)
def question_bank_editor(request: Request, block_id: int):
    user = require_test_gen_access(request)
    conn = get_db()
    try:
        block = conn.execute("""
            SELECT sb.*, c.name as class_name, s.name as subject_name
            FROM syllabus_blocks sb
            JOIN classes c ON c.id = sb.class_id
            JOIN subjects s ON s.id = sb.subject_id
            WHERE sb.id = ?
        """, (block_id,)).fetchone()
        if not block:
            return RedirectResponse(url="/test-generation/question-bank", status_code=303)

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

        subject_direction = 'auto'
        default_urdu_font = ''
        default_english_font = ''
        subj_row = conn.execute("SELECT default_direction FROM subjects WHERE id = ?", (block["subject_id"],)).fetchone()
        if subj_row:
            subject_direction = subj_row["default_direction"]
        urdu_font = conn.execute("SELECT font_name FROM school_fonts WHERE font_type='urdu' AND is_default=1").fetchone()
        english_font = conn.execute("SELECT font_name FROM school_fonts WHERE font_type='english' AND is_default=1").fetchone()
        if urdu_font:
            default_urdu_font = urdu_font["font_name"]
        if english_font:
            default_english_font = english_font["font_name"]
    finally:
        conn.close()

    return templates.TemplateResponse("test_generation/question_bank_editor.html", {
        "request": request,
        "user": user,
        "block": block,
        "block_id": block_id,
        "questions_mcq": questions_mcq,
        "questions_short": questions_short,
        "questions_long": questions_long,
        "comprehensions": comprehensions_with_mcqs,
        "subject_direction": subject_direction,
        "default_urdu_font": default_urdu_font,
        "default_english_font": default_english_font,
    })

# ----------------------- Image Upload -----------------------
@router.post("/upload-image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    require_test_gen_access(request)
    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
        raise HTTPException(400, "Only PNG, JPG, JPEG, GIF allowed")
    contents = await file.read()
    if len(contents) > 500 * 1024:
        raise HTTPException(400, "Image size must be less than 500 KB")
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join("uploads", "question_images", filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(contents)
    return JSONResponse({"location": f"/{path}"})

# ----------------------- MCQ CRUD -----------------------
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
    require_test_gen_access(request)
    q = clean_html(question_text)
    a = clean_html(option_a)
    b = clean_html(option_b)
    c = clean_html(option_c)
    d = clean_html(option_d)
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO question_bank (syllabus_block_id, question_type, question_text,
                                      option_a, option_b, option_c, option_d, correct_answer)
            VALUES (?, 'mcq', ?, ?, ?, ?, ?, ?)
        """, (block_id, q, a, b, c, d, correct_answer))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=added", status_code=303)

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
    require_test_gen_access(request)
    q = clean_html(question_text)
    a = clean_html(option_a)
    b = clean_html(option_b)
    c = clean_html(option_c)
    d = clean_html(option_d)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("""
            UPDATE question_bank
            SET question_text=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_answer=?
            WHERE id=?
        """, (q, a, b, c, d, correct_answer, qid))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=updated", status_code=303)

@router.post("/question-bank/mcq/delete/{qid}")
def delete_mcq(qid: int, request: Request):
    require_test_gen_access(request)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=deleted", status_code=303)

# ----------------------- Short Questions CRUD -----------------------
@router.post("/question-bank/short/add")
def add_short(
    request: Request,
    block_id: int = Form(...),
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_test_gen_access(request)
    q = clean_html(question_text)
    ans = clean_html(answer_text) if answer_text else ""
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO question_bank (syllabus_block_id, question_type, question_text, answer_text)
            VALUES (?, 'short', ?, ?)
        """, (block_id, q, ans))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=added", status_code=303)

@router.post("/question-bank/short/edit/{qid}")
def edit_short(
    qid: int,
    request: Request,
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_test_gen_access(request)
    q = clean_html(question_text)
    ans = clean_html(answer_text) if answer_text else ""
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("UPDATE question_bank SET question_text=?, answer_text=? WHERE id=?", (q, ans, qid))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=updated", status_code=303)

@router.post("/question-bank/short/delete/{qid}")
def delete_short(qid: int, request: Request):
    require_test_gen_access(request)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=deleted", status_code=303)

# ----------------------- Long Questions CRUD -----------------------
@router.post("/question-bank/long/add")
def add_long(
    request: Request,
    block_id: int = Form(...),
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_test_gen_access(request)
    q = clean_html(question_text)
    ans = clean_html(answer_text) if answer_text else ""
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO question_bank (syllabus_block_id, question_type, question_text, answer_text)
            VALUES (?, 'long', ?, ?)
        """, (block_id, q, ans))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=added", status_code=303)

@router.post("/question-bank/long/edit/{qid}")
def edit_long(
    qid: int,
    request: Request,
    question_text: str = Form(...),
    answer_text: str = Form(""),
):
    require_test_gen_access(request)
    q = clean_html(question_text)
    ans = clean_html(answer_text) if answer_text else ""
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("UPDATE question_bank SET question_text=?, answer_text=? WHERE id=?", (q, ans, qid))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=updated", status_code=303)

@router.post("/question-bank/long/delete/{qid}")
def delete_long(qid: int, request: Request):
    require_test_gen_access(request)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=deleted", status_code=303)

# ----------------------- Comprehension CRUD -----------------------
@router.post("/question-bank/comprehension/add")
def add_comprehension(
    request: Request,
    block_id: int = Form(...),
    passage: str = Form(...),
):
    require_test_gen_access(request)
    p = clean_html(passage)
    conn = get_db()
    try:
        # Add empty question_text to satisfy NOT NULL constraint
        conn.execute("""
            INSERT INTO question_bank (syllabus_block_id, question_type, question_text, comprehension_passage)
            VALUES (?, 'comprehension', '', ?)
        """, (block_id, p))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=added", status_code=303)

@router.post("/question-bank/comprehension/edit/{pid}")
def edit_comprehension(
    pid: int,
    request: Request,
    passage: str = Form(...),
):
    require_test_gen_access(request)
    p = clean_html(passage)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (pid,)).fetchone()["syllabus_block_id"]
        conn.execute("UPDATE question_bank SET comprehension_passage=? WHERE id=?", (p, pid))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=updated", status_code=303)

@router.post("/question-bank/comprehension/delete/{pid}")
def delete_comprehension(pid: int, request: Request):
    require_test_gen_access(request)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (pid,)).fetchone()["syllabus_block_id"]
        conn.execute("DELETE FROM question_bank WHERE parent_comprehension_id = ?", (pid,))
        conn.execute("DELETE FROM question_bank WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=deleted", status_code=303)

# ----------------------- Comprehension MCQ CRUD -----------------------
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
    require_test_gen_access(request)
    q = clean_html(question_text)
    a = clean_html(option_a)
    b = clean_html(option_b)
    c = clean_html(option_c)
    d = clean_html(option_d)
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO question_bank (syllabus_block_id, question_type, parent_comprehension_id,
                                      question_text, option_a, option_b, option_c, option_d, correct_answer)
            VALUES (?, 'mcq', ?, ?, ?, ?, ?, ?, ?)
        """, (block_id, parent_id, q, a, b, c, d, correct_answer))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=added", status_code=303)

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
    require_test_gen_access(request)
    q = clean_html(question_text)
    a = clean_html(option_a)
    b = clean_html(option_b)
    c = clean_html(option_c)
    d = clean_html(option_d)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("""
            UPDATE question_bank
            SET question_text=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_answer=?
            WHERE id=?
        """, (q, a, b, c, d, correct_answer, qid))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=updated", status_code=303)

@router.post("/question-bank/comprehension/mcq/delete/{qid}")
def delete_comprehension_mcq(qid: int, request: Request):
    require_test_gen_access(request)
    conn = get_db()
    try:
        block_id = conn.execute("SELECT syllabus_block_id FROM question_bank WHERE id = ?", (qid,)).fetchone()["syllabus_block_id"]
        conn.execute("DELETE FROM question_bank WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url=f"/test-generation/question-bank/editor/{block_id}?msg=deleted", status_code=303)

# ----------------------- API Blocks -----------------------
@router.get("/api/blocks")
def api_blocks(request: Request, class_id: int, subject_id: int):
    user = require_test_gen_access(request)
    conn = get_db()
    try:
        blocks = conn.execute("""
            SELECT id, block_name FROM syllabus_blocks
            WHERE class_id = ? AND subject_id = ?
            ORDER BY block_name
        """, (class_id, subject_id)).fetchall()
    finally:
        conn.close()
    return JSONResponse([{"id": b["id"], "name": b["block_name"]} for b in blocks])