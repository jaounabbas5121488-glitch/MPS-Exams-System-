from datetime import date
from io import BytesIO

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from openpyxl import Workbook
from openpyxl.styles import Font

from database import (
    get_db,
    hash_password,
    init_db,
    get_official_check_in_time,
    format_time_display,
    get_school_open_status,
    is_school_open,
    get_teacher_attendance,
    record_check_in,
    get_progress_stats,
    set_setting,
)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="mps-secret-key-2026")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    init_db()


def current_user(request: Request):
    return request.session.get("user")


def require_login(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        user = current_user(request)
        return RedirectResponse(url="/admin" if user["role"] == "admin" else "/dashboard")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ? AND password = ?",
        (email.strip().lower(), hash_password(password)),
    ).fetchone()
    conn.close()

    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})
    if user["status"] == "pending":
        return templates.TemplateResponse("login.html", {"request": request, "error": "Your account is pending admin approval."})
    if user["status"] == "rejected":
        return templates.TemplateResponse("login.html", {"request": request, "error": "Your account has been rejected."})

    request.session["user"] = {
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "father_name": user["father_name"],
        "qualifications": user["qualifications"],
        "experience": user["experience"],
        "role": user["role"],
        "status": user["status"],
    }

    if user["role"] == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "error": None, "success": None})


@app.post("/signup", response_class=HTMLResponse)
def signup_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    father_name: str = Form(...),
    qualifications: str = Form(...),
    experience: str = Form(...),
):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if existing:
        conn.close()
        return templates.TemplateResponse("signup.html", {
            "request": request, "error": "Email already registered.", "success": None
        })
    conn.execute(
        "INSERT INTO users (email, password, full_name, father_name, qualifications, experience, role, status) VALUES (?, ?, ?, ?, ?, ?, 'teacher', 'pending')",
        (email.strip().lower(), hash_password(password), full_name.strip(), father_name.strip(), qualifications.strip(), experience.strip()),
    )
    conn.commit()
    conn.close()
    return templates.TemplateResponse("signup.html", {
        "request": request,
        "error": None,
        "success": "Account created! Please wait for admin approval before logging in.",
    })


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=303)

    conn = get_db()
    pending = conn.execute("SELECT * FROM users WHERE status = 'pending'").fetchall()
    approved = conn.execute("SELECT * FROM users WHERE role = 'teacher' AND status = 'approved'").fetchall()
    today = date.today().isoformat()
    is_open = get_school_open_status(conn, today)
    official_check_in_time = get_official_check_in_time(conn)
    official_check_in_display = format_time_display(official_check_in_time)
    conn.close()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "pending": pending,
        "approved": approved,
        "today": today,
        "is_open": is_open,
        "official_check_in_time": official_check_in_time,
        "official_check_in_display": official_check_in_display,
        "user": user,
        "settings_saved": request.query_params.get("settings_saved"),
    })


@app.post("/admin/settings/check-in-time")
def update_check_in_time(request: Request, official_check_in_time: str = Form(...)):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=303)

    conn = get_db()
    set_setting(conn, "official_check_in_time", official_check_in_time.strip())
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin?settings_saved=1", status_code=303)


@app.post("/admin/approve/{teacher_id}")
def approve_teacher(teacher_id: int, request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=303)
    conn = get_db()
    conn.execute("UPDATE users SET status = 'approved' WHERE id = ?", (teacher_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/reject/{teacher_id}")
def reject_teacher(teacher_id: int, request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=303)
    conn = get_db()
    conn.execute("UPDATE users SET status = 'rejected' WHERE id = ?", (teacher_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/calendar")
def set_calendar(request: Request, is_open: int = Form(...)):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=303)
    today = date.today().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO school_calendar (date, is_open) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET is_open = excluded.is_open",
        (today, is_open),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
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
    conn.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "today": today,
        "is_open": is_open,
        "attendance": attendance,
        "official_check_in_display": official_check_in_display,
    })


@app.post("/attendance/checkin")
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


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user.get("role") == "admin":
        return RedirectResponse(url="/admin", status_code=303)

    conn = get_db()
    stats = get_progress_stats(conn, user["email"])
    conn.close()

    return templates.TemplateResponse("progress.html", {
        "request": request,
        "user": user,
        "stats": stats,
    })


@app.get("/test-marks", response_class=HTMLResponse)
def test_marks(
    request: Request,
    class_id: int | None = None,
    subject_id: int | None = None,
    test_type_id: int | None = None,
    teacher_email: str | None = None,
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    conn = get_db()
    classes = conn.execute("SELECT * FROM classes ORDER BY name").fetchall()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    tests = conn.execute("SELECT * FROM test_types ORDER BY name").fetchall()

    selected_class_id = class_id or (classes[0]["id"] if classes else None)
    selected_subject_id = subject_id or (subjects[0]["id"] if subjects else None)
    selected_test_type_id = test_type_id or (tests[0]["id"] if tests else None)

    if selected_class_id is None or selected_subject_id is None or selected_test_type_id is None:
        conn.close()
        return templates.TemplateResponse("coming_soon.html", {"request": request, "user": user, "title": "Test Marks"})

    if user.get("role") == "admin":
        approved_teachers = conn.execute(
            "SELECT email, full_name FROM users WHERE role = 'teacher' AND status = 'approved' ORDER BY full_name"
        ).fetchall()
        if teacher_email:
            selected_teacher_email = teacher_email.strip().lower()
        else:
            selected_teacher_email = approved_teachers[0]["email"] if approved_teachers else None
        teachers = approved_teachers
    else:
        selected_teacher_email = user["email"]
        teachers = []

    if not selected_teacher_email:
        conn.close()
        return templates.TemplateResponse("coming_soon.html", {"request": request, "user": user, "title": "Test Marks"})

    today = date.today().isoformat()
    class_row = conn.execute("SELECT * FROM classes WHERE id = ?", (selected_class_id,)).fetchone()
    subject_row = conn.execute("SELECT * FROM subjects WHERE id = ?", (selected_subject_id,)).fetchone()
    test_row = conn.execute("SELECT * FROM test_types WHERE id = ?", (selected_test_type_id,)).fetchone()
    students = conn.execute(
        "SELECT * FROM students WHERE class_id = ? ORDER BY full_name",
        (selected_class_id,),
    ).fetchall()
    marks_rows = conn.execute(
        """
        SELECT student_id, mark
        FROM teacher_marks
        WHERE teacher_email = ?
          AND date = ?
          AND class_id = ?
          AND subject_id = ?
          AND test_type_id = ?
        """,
        (selected_teacher_email, today, selected_class_id, selected_subject_id, selected_test_type_id),
    ).fetchall()
    marks_map = {row["student_id"]: row["mark"] for row in marks_rows}
    conn.close()

    return templates.TemplateResponse("marks_module1.html", {
        "request": request,
        "user": user,
        "today": today,
        "classes": classes,
        "subjects": subjects,
        "tests": tests,
        "teachers": teachers,
        "selected_teacher_email": selected_teacher_email,
        "selected_class_id": selected_class_id,
        "selected_subject_id": selected_subject_id,
        "selected_test_type_id": selected_test_type_id,
        "selected_class_name": class_row["name"] if class_row else "",
        "selected_subject_name": subject_row["name"] if subject_row else "",
        "selected_test_type_name": test_row["name"] if test_row else "",
        "students": students,
        "marks_map": marks_map,
    })


@app.post("/test-marks/save")
async def save_test_marks(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    class_id = int(form["class_id"])
    subject_id = int(form["subject_id"])
    test_type_id = int(form["test_type_id"])
    teacher_email = form.get("teacher_email")
    if user.get("role") != "admin":
        teacher_email = user["email"]
    else:
        teacher_email = (teacher_email or "").strip().lower()

    today = date.today().isoformat()
    conn = get_db()

    student_ids = []
    for key in form.keys():
        if str(key).startswith("mark_"):
            try:
                student_ids.append(int(str(key).split("mark_")[1]))
            except Exception:
                pass
    student_ids = sorted(set(student_ids))

    for student_id in student_ids:
        raw = form.get(f"mark_{student_id}")
        mark_value = None if raw is None or str(raw).strip() == "" else float(raw)
        conn.execute(
            """
            INSERT INTO teacher_marks
              (teacher_email, date, class_id, subject_id, test_type_id, student_id, mark)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(teacher_email, date, class_id, subject_id, test_type_id, student_id)
            DO UPDATE SET mark = excluded.mark
            """,
            (teacher_email, today, class_id, subject_id, test_type_id, student_id, mark_value),
        )

    conn.commit()
    conn.close()

    redirect_url = f"/test-marks?msg=saved&class_id={class_id}&subject_id={subject_id}&test_type_id={test_type_id}"
    if user.get("role") == "admin":
        redirect_url += f"&teacher_email={teacher_email}"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.post("/test-marks/export")
async def export_test_marks(request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    class_id = int(form["class_id"])
    subject_id = int(form["subject_id"])
    test_type_id = int(form["test_type_id"])
    teacher_email = (form["teacher_email"] or "").strip().lower()
    today = date.today().isoformat()

    conn = get_db()
    class_row = conn.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
    subject_row = conn.execute("SELECT name FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    test_row = conn.execute("SELECT name FROM test_types WHERE id = ?", (test_type_id,)).fetchone()
    students = conn.execute(
        "SELECT id, full_name FROM students WHERE class_id = ? ORDER BY full_name",
        (class_id,),
    ).fetchall()
    marks_rows = conn.execute(
        """
        SELECT student_id, mark
        FROM teacher_marks
        WHERE teacher_email = ?
          AND date = ?
          AND class_id = ?
          AND subject_id = ?
          AND test_type_id = ?
        """,
        (teacher_email, today, class_id, subject_id, test_type_id),
    ).fetchall()
    marks_map = {row["student_id"]: row["mark"] for row in marks_rows}
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Marks"
    bold = Font(bold=True)

    ws.append(["MPS Exams System - Test Marks (Module 1)"])
    ws.append([f"Date: {today}"])
    ws.append([f"Class: {class_row['name'] if class_row else ''}"])
    ws.append([f"Subject: {subject_row['name'] if subject_row else ''}"])
    ws.append([f"Test Type: {test_row['name'] if test_row else ''}"])
    ws.append([f"Teacher: {teacher_email}"])
    ws.append([])
    ws.append(["#", "Student", "Mark"])
    for cell in ws[ws.max_row]:
        cell.font = bold

    for idx, student in enumerate(students, start=1):
        ws.append([idx, student["full_name"], marks_map.get(student["id"], None)])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"marks_module1_class{class_id}_subject{subject_id}_test{test_type_id}_{today}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
