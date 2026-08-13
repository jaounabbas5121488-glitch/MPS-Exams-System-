import json
from datetime import date
from collections import defaultdict
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from database import (
    get_db,
    is_school_open,
    get_teacher_attendance,
    record_check_in,
    get_official_check_in_time,
    format_time_display,
    get_school_open_status,
    get_progress_stats,
    get_session_progress_totals,
    get_monthly_progress_breakdown,
)

router = APIRouter()

from main import current_user, require_login, templates


# =================== TEACHER DASHBOARD & ATTENDANCE ===================
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user.get("role") == "admin":
        return RedirectResponse(url="/admin", status_code=303)

    today = date.today().isoformat()
    conn = get_db()
    is_open = get_school_open_status(conn, today)
    attendance = get_teacher_attendance(conn, user["email"], today)
    official_check_in_display = format_time_display(get_official_check_in_time(conn))

    # ────── Teacher Quick Stats ──────
    total_tests = conn.execute("""
        SELECT COUNT(DISTINCT es.id)
        FROM exam_sessions es
        JOIN exam_session_subjects ess ON ess.session_id = es.id
        WHERE ess.teacher_email = ? AND es.status = 'confirmed'
    """, (user["email"],)).fetchone()[0] or 0

    total_students = conn.execute("""
        SELECT COUNT(DISTINCT em.student_id)
        FROM exam_marks em
        JOIN exam_session_subjects ess ON ess.id = em.session_subject_id
        JOIN exam_sessions es ON es.id = ess.session_id
        WHERE ess.teacher_email = ? AND es.status = 'confirmed'
    """, (user["email"],)).fetchone()[0] or 0

    avg_pass = conn.execute("""
        SELECT AVG(pass_pct) FROM (
            SELECT 
                (CAST(SUM(CASE WHEN em.marks_obtained >= ess.passing_marks THEN 1 ELSE 0 END) AS REAL) * 100.0 / COUNT(*)) as pass_pct
            FROM exam_session_subjects ess
            JOIN exam_marks em ON em.session_subject_id = ess.id
            JOIN exam_sessions es ON es.id = ess.session_id
            WHERE ess.teacher_email = ? AND es.status = 'confirmed'
            GROUP BY ess.id
        )
    """, (user["email"],)).fetchone()[0]
    avg_pass = round(avg_pass, 1) if avg_pass else 0

    # Test Generation permission
    perm = conn.execute("SELECT allow_teachers FROM test_permissions WHERE id = 1").fetchone()
    test_gen_allowed = bool(perm and perm["allow_teachers"])

    conn.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "today": today,
        "is_open": is_open,
        "attendance": attendance,
        "official_check_in_display": official_check_in_display,
        "total_tests": total_tests,
        "total_students": total_students,
        "avg_pass": avg_pass,
        "test_gen_allowed": test_gen_allowed,
    })


@router.post("/attendance/checkin")
def checkin(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today().isoformat()
    conn = get_db()

    if not is_school_open(conn, today):
        conn.close()
        return RedirectResponse(url="/dashboard?msg=closed", status_code=303)

    existing = get_teacher_attendance(conn, user["email"], today)
    if existing:
        conn.close()
        return RedirectResponse(
            url=f"/dashboard?msg=already_checked&punctuality={existing['punctuality']}",
            status_code=303,
        )

    attendance = record_check_in(conn, user["email"], today)
    conn.close()
    return RedirectResponse(
        url=f"/dashboard?msg=checked_in&punctuality={attendance['punctuality']}",
        status_code=303,
    )


@router.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


# ===================== TEACHER EXAM MARKS ENTRY =====================
@router.get("/test-marks", response_class=HTMLResponse)
def teacher_exam_marks_home(request: Request, session_id: int | None = None):
    user = require_login(request)
    if user.get("role") == "admin":
        return RedirectResponse(url="/admin/exam-status", status_code=303)

    conn = get_db()
    sessions = conn.execute("""
        SELECT DISTINCT es.id, c.name as class_name, tt.name as test_type_name,
               es.test_number, es.conduct_date
        FROM exam_sessions es
        JOIN exam_session_subjects ess ON ess.session_id = es.id
        JOIN classes c ON c.id = es.class_id
        JOIN test_types tt ON tt.id = es.test_type_id
        WHERE es.is_visible = 1
        ORDER BY es.conduct_date DESC
    """).fetchall()

    selected_session_id = session_id or (sessions[0]["id"] if sessions else None)
    session_subjects = []
    if selected_session_id:
        session_subjects = conn.execute("""
            SELECT ess.*, s.name as subject_name
            FROM exam_session_subjects ess
            JOIN subjects s ON s.id = ess.subject_id
            WHERE ess.session_id = ?
        """, (selected_session_id,)).fetchall()
    conn.close()
    return templates.TemplateResponse("teacher_exam_marks.html", {
        "request": request,
        "user": user,
        "sessions": sessions,
        "selected_session_id": selected_session_id,
        "session_subjects": session_subjects,
    })


@router.get("/test-marks/entry", response_class=HTMLResponse)
def marks_entry_grid(request: Request, session_subject_id: int):
    user = require_login(request)
    conn = get_db()
    ss = conn.execute("""
        SELECT ess.*, s.name as subject_name, es.class_id, es.test_type_id, es.test_number,
               tt.name as test_type_name, c.name as class_name
        FROM exam_session_subjects ess
        JOIN exam_sessions es ON es.id = ess.session_id
        JOIN subjects s ON s.id = ess.subject_id
        JOIN test_types tt ON tt.id = es.test_type_id
        JOIN classes c ON c.id = es.class_id
        WHERE ess.id = ?
    """, (session_subject_id,)).fetchone()
    if not ss:
        conn.close()
        return RedirectResponse(url="/test-marks?error=not_found", status_code=303)

    tpl = conn.execute("SELECT * FROM class_templates WHERE class_id = ?", (ss["class_id"],)).fetchone()
    extra_columns = json.loads(tpl["extra_columns"]) if tpl else []
    identity_columns = json.loads(tpl["identity_columns"]) if tpl else ["Name", "Father Name"]

    students_raw = conn.execute("""
        SELECT sr.*, em.marks_obtained as mark
        FROM student_records sr
        LEFT JOIN exam_marks em ON em.student_id = sr.id
            AND em.session_subject_id = ?
        WHERE sr.class_id = ?
        ORDER BY sr.name
    """, (session_subject_id, ss["class_id"])).fetchall()

    conn.close()

    students = []
    for s in students_raw:
        s_dict = dict(s)
        try:
            s_dict['extra_data'] = json.loads(s_dict['extra_data']) if s_dict['extra_data'] else {}
        except (json.JSONDecodeError, TypeError):
            s_dict['extra_data'] = {}
        students.append(s_dict)

    return templates.TemplateResponse("teacher_exam_marks_grid.html", {
        "request": request,
        "user": user,
        "session_subject_id": session_subject_id,
        "session_id": ss["session_id"],
        "subject_name": ss["subject_name"],
        "class_name": ss["class_name"],
        "test_info": f"{ss['test_type_name']} {ss['test_number']}",
        "total_marks": ss["total_marks"],
        "passing_marks": ss["passing_marks"],
        "students": students,
        "extra_columns": extra_columns,
    })


@router.post("/test-marks/save-new")
async def save_marks_new(request: Request):
    user = require_login(request)
    form = await request.form()
    session_subject_id = int(form["session_subject_id"])
    session_id = int(form["session_id"])

    conn = get_db()
    ss = conn.execute("SELECT * FROM exam_session_subjects WHERE id = ?",
                      (session_subject_id,)).fetchone()
    if not ss:
        conn.close()
        return RedirectResponse(url="/test-marks?error=not_found", status_code=303)

    if not ss["teacher_email"] or ss["teacher_email"].strip() == "":
        conn.execute("UPDATE exam_session_subjects SET teacher_email = ? WHERE id = ?",
                     (user["email"], session_subject_id))

    conn.execute("DELETE FROM exam_marks WHERE session_subject_id = ?", (session_subject_id,))

    for key in form.keys():
        if key.startswith("mark_"):
            student_id = int(key.split("_")[1])
            raw = form[key]
            if raw and str(raw).strip():
                marks_obtained = float(raw)
                conn.execute("INSERT INTO exam_marks (session_subject_id, student_id, marks_obtained) VALUES (?, ?, ?)",
                             (session_subject_id, student_id, marks_obtained))

    conn.execute("UPDATE exam_session_subjects SET submitted = 1 WHERE id = ?", (session_subject_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/test-marks?msg=saved&session_id={session_id}", status_code=303)


# ===================== TEACHER PROGRESS (with test‑wise grouped exam results) =====================
@router.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    user = require_login(request)
    if user.get("role") == "admin":
        return RedirectResponse(url="/admin", status_code=303)

    conn = get_db()
    stats = get_progress_stats(conn, user["email"])
    session_totals = get_session_progress_totals(conn, user["email"])
    monthly_breakdown = get_monthly_progress_breakdown(conn, user["email"])

    # ─── Aggregated exam results (grouped by test type + test number) ───
    session_subjects = conn.execute("""
        SELECT ess.id as ss_id, ess.session_id, ess.subject_id, ess.passing_marks, ess.total_marks,
               s.name as subject_name, es.test_number, es.conduct_date, tt.name as test_type_name,
               tt.id as test_type_id, c.name as class_name
        FROM exam_session_subjects ess
        JOIN exam_sessions es ON es.id = ess.session_id
        JOIN subjects s ON s.id = ess.subject_id
        JOIN classes c ON c.id = es.class_id
        JOIN test_types tt ON tt.id = es.test_type_id
        WHERE ess.teacher_email = ? AND es.status = 'confirmed'
        ORDER BY es.conduct_date DESC
    """, (user["email"],)).fetchall()

    group_summary = defaultdict(lambda: {
        "test_type_name": "",
        "test_number": "",
        "classes": defaultdict(lambda: {"subjects": []})
    })

    for ss in session_subjects:
        marks = conn.execute("SELECT marks_obtained FROM exam_marks WHERE session_subject_id = ?", (ss["ss_id"],)).fetchall()
        total = len(marks)
        pass_cnt = sum(1 for m in marks if m["marks_obtained"] and m["marks_obtained"] >= ss["passing_marks"])
        fail_cnt = total - pass_cnt
        pass_percent = round((pass_cnt / total) * 100, 1) if total else 0

        key = (ss["test_type_id"], ss["test_number"])
        group = group_summary[key]
        group["test_type_name"] = ss["test_type_name"]
        group["test_number"] = ss["test_number"]
        class_group = group["classes"][ss["class_name"]]
        class_group["subjects"].append({
            "subject_name": ss["subject_name"],
            "total_marks": ss["total_marks"],
            "passing_marks": ss["passing_marks"],
            "total_students": total,
            "pass_count": pass_cnt,
            "fail_count": fail_cnt,
            "pass_percent": pass_percent,
        })

    grouped_exam_results = []
    for key, grp in group_summary.items():
        classes_list = []
        for cls_name, cls_data in grp["classes"].items():
            classes_list.append({"class_name": cls_name, "subjects": cls_data["subjects"]})
        grouped_exam_results.append({
            "test_type_name": grp["test_type_name"],
            "test_number": grp["test_number"],
            "classes": classes_list
        })

    conn.close()

    return templates.TemplateResponse("progress.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "session_totals": session_totals,
        "monthly_breakdown": monthly_breakdown,
        "grouped_exam_results": grouped_exam_results,
    })