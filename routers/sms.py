from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from database import get_db, get_setting, set_setting
import urllib.parse
import urllib.request

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ─── Table Creation ───
def init_sms_tables():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            min_percent REAL NOT NULL,
            max_percent REAL NOT NULL,
            message TEXT NOT NULL
        )
    """)
    count = cur.execute("SELECT COUNT(*) FROM sms_templates").fetchone()[0]
    if count == 0:
        defaults = [
            (80, 100, "Mashallah! Bohat acha result. Aapke bache ne shandar performance di."),
            (70, 79.99, "Shabash! Acha result. Thori aur mehnat karein."),
            (50, 69.99, "Mehnat ki zaroorat hai. Taakay agla result behtar ho."),
            (0, 49.99, "Fail hone par parents se raabta karein."),
        ]
        cur.executemany("INSERT INTO sms_templates (min_percent, max_percent, message) VALUES (?, ?, ?)", defaults)
        conn.commit()
    conn.close()


# ─── SMS Templates CRUD ───
@router.get("/admin/sms-templates", response_class=HTMLResponse)
def sms_templates_page(request: Request):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()
    templates_list = conn.execute("SELECT * FROM sms_templates ORDER BY min_percent").fetchall()
    conn.close()
    return templates.TemplateResponse("admin_sms_templates.html", {
        "request": request,
        "user": user,
        "sms_templates": templates_list,
    })


@router.post("/admin/sms-templates/add")
def add_sms_template(
    request: Request,
    min_percent: float = Form(...),
    max_percent: float = Form(...),
    message: str = Form(...),
):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()
    conn.execute(
        "INSERT INTO sms_templates (min_percent, max_percent, message) VALUES (?, ?, ?)",
        (min_percent, max_percent, message.strip()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/sms-templates", status_code=303)


@router.post("/admin/sms-templates/delete/{template_id}")
def delete_sms_template(template_id: int, request: Request):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()
    conn.execute("DELETE FROM sms_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/sms-templates", status_code=303)


# ─── SMS Gateway Settings ───
@router.get("/admin/sms-settings", response_class=HTMLResponse)
def sms_settings_page(request: Request):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()
    sms_gateway_url = get_setting(conn, "sms_gateway_url", "")
    conn.close()
    return templates.TemplateResponse("admin_sms_settings.html", {
        "request": request,
        "user": user,
        "sms_gateway_url": sms_gateway_url,
    })


@router.post("/admin/sms-settings")
def save_sms_settings(request: Request, sms_gateway_url: str = Form(...)):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()
    url = sms_gateway_url.strip().rstrip("/")
    set_setting(conn, "sms_gateway_url", url)
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/sms-settings?msg=saved", status_code=303)


# ─── Helper: Get SMS gateway URL ───
def get_sms_gateway_url(conn) -> str:
    return get_setting(conn, "sms_gateway_url", "").strip().rstrip("/")


# ─── Helper: Send SMS via gateway ───
def send_sms_via_gateway(phone: str, message: str, gateway_url: str) -> bool:
    """
    Uses the SMS Gateway app's HTTP API. The app expects:
    GET {gateway_url}/send?phone=<number>&text=<message>
    """
    try:
        params = urllib.parse.urlencode({"phone": phone, "text": message})
        url = f"{gateway_url}/send?{params}"
        urllib.request.urlopen(url, timeout=10)
        return True
    except Exception as e:
        print(f"SMS sending failed for {phone}: {e}")
        return False


# ─── List confirmed sessions for SMS ───
@router.get("/admin/sms-send", response_class=HTMLResponse)
def sms_send_list(request: Request):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()
    sessions = conn.execute("""
        SELECT es.id, c.name as class_name, tt.name as test_type_name,
               es.test_number, es.conduct_date, es.status
        FROM exam_sessions es
        JOIN classes c ON c.id = es.class_id
        JOIN test_types tt ON tt.id = es.test_type_id
        WHERE es.status = 'confirmed'
        ORDER BY es.conduct_date DESC
    """).fetchall()
    conn.close()
    return templates.TemplateResponse("admin_sms_send_list.html", {
        "request": request,
        "user": user,
        "sessions": sessions,
    })


# ─── Preview SMS before sending ───
@router.get("/admin/sms-send/{session_id}", response_class=HTMLResponse)
def sms_send_preview(request: Request, session_id: int):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()

    session = conn.execute("""
        SELECT es.*, c.name as class_name, tt.name as test_type_name
        FROM exam_sessions es
        JOIN classes c ON c.id = es.class_id
        JOIN test_types tt ON tt.id = es.test_type_id
        WHERE es.id = ?
    """, (session_id,)).fetchone()
    if not session:
        conn.close()
        return RedirectResponse(url="/admin/sms-send", status_code=303)

    gateway_url = get_sms_gateway_url(conn)
    if not gateway_url:
        conn.close()
        return RedirectResponse(url="/admin/sms-settings?error=no_url", status_code=303)

    subjects = conn.execute("""
        SELECT ess.id, ess.subject_id, ess.total_marks, ess.passing_marks, s.name as subject_name
        FROM exam_session_subjects ess
        JOIN subjects s ON s.id = ess.subject_id
        WHERE ess.session_id = ?
        ORDER BY s.name
    """, (session_id,)).fetchall()

    students = conn.execute("""
        SELECT * FROM student_records WHERE class_id = ? ORDER BY name
    """, (session["class_id"],)).fetchall()

    preview_data = []
    for student in students:
        if not student["phone"] or student["phone"].strip() == "":
            continue

        total_obtained = 0
        sum_passing = 0
        marks_lines = []
        for subj in subjects:
            mark_row = conn.execute("""
                SELECT marks_obtained FROM exam_marks
                WHERE session_subject_id = ? AND student_id = ?
            """, (subj["id"], student["id"])).fetchone()
            obtained = mark_row["marks_obtained"] if mark_row and mark_row["marks_obtained"] is not None else 0
            total_obtained += obtained
            sum_passing += subj["passing_marks"]
            pass_fail = "Pass" if obtained >= subj["passing_marks"] else "Fail"
            marks_lines.append(f"{subj['subject_name']}: {obtained}/{subj['total_marks']} ({pass_fail})")

        total_marks = sum(s["total_marks"] for s in subjects)
        percentage = round((total_obtained / total_marks) * 100, 2) if total_marks else 0
        overall = "PASS" if total_obtained >= sum_passing else "FAIL"

        custom_message = ""
        tpl_row = conn.execute("""
            SELECT message FROM sms_templates
            WHERE min_percent <= ? AND max_percent >= ?
            ORDER BY min_percent
        """, (percentage, percentage)).fetchone()
        if tpl_row:
            custom_message = tpl_row["message"]

        sms_text = (
            f"MPS Result:\n"
            f"Student: {student['name']}\n"
            + "\n".join(marks_lines) +
            f"\nTotal: {total_obtained}/{total_marks}\n"
            f"Percentage: {percentage}%\n"
            f"Overall: {overall}\n"
            f"{custom_message}"
        )

        preview_data.append({
            "student_id": student["id"],
            "name": student["name"],
            "phone": student["phone"],
            "percentage": percentage,
            "sms_text": sms_text,
        })

    conn.close()
    return templates.TemplateResponse("admin_sms_send_preview.html", {
        "request": request,
        "user": user,
        "session": dict(session),
        "preview_data": preview_data,
        "gateway_url": gateway_url,
    })


# ─── Actually send SMS ───
@router.post("/admin/sms-send/{session_id}")
def sms_send_action(request: Request, session_id: int):
    from main import require_admin
    user = require_admin(request)
    conn = get_db()

    session = conn.execute("SELECT * FROM exam_sessions WHERE id = ?", (session_id,)).fetchone()
    if not session or session["status"] != "confirmed":
        conn.close()
        return RedirectResponse(url="/admin/sms-send", status_code=303)

    gateway_url = get_sms_gateway_url(conn)
    if not gateway_url:
        conn.close()
        return RedirectResponse(url="/admin/sms-settings?error=no_url", status_code=303)

    subjects = conn.execute("""
        SELECT ess.id, ess.subject_id, ess.total_marks, ess.passing_marks, s.name as subject_name
        FROM exam_session_subjects ess
        JOIN subjects s ON s.id = ess.subject_id
        WHERE ess.session_id = ?
    """, (session_id,)).fetchall()

    students = conn.execute("""
        SELECT * FROM student_records WHERE class_id = ? ORDER BY name
    """, (session["class_id"],)).fetchall()

    success_count = 0
    fail_count = 0
    failed_numbers = []

    for student in students:
        phone = student["phone"].strip()
        if not phone:
            continue

        total_obtained = 0
        sum_passing = 0
        marks_lines = []
        for subj in subjects:
            mark_row = conn.execute("""
                SELECT marks_obtained FROM exam_marks
                WHERE session_subject_id = ? AND student_id = ?
            """, (subj["id"], student["id"])).fetchone()
            obtained = mark_row["marks_obtained"] if mark_row and mark_row["marks_obtained"] is not None else 0
            total_obtained += obtained
            sum_passing += subj["passing_marks"]
            pass_fail = "Pass" if obtained >= subj["passing_marks"] else "Fail"
            marks_lines.append(f"{subj['subject_name']}: {obtained}/{subj['total_marks']} ({pass_fail})")

        total_marks = sum(s["total_marks"] for s in subjects)
        percentage = round((total_obtained / total_marks) * 100, 2) if total_marks else 0
        overall = "PASS" if total_obtained >= sum_passing else "FAIL"

        custom_message = ""
        tpl_row = conn.execute("""
            SELECT message FROM sms_templates
            WHERE min_percent <= ? AND max_percent >= ?
            ORDER BY min_percent
        """, (percentage, percentage)).fetchone()
        if tpl_row:
            custom_message = tpl_row["message"]

        sms_text = (
            f"MPS Result:\n"
            f"Student: {student['name']}\n"
            + "\n".join(marks_lines) +
            f"\nTotal: {total_obtained}/{total_marks}\n"
            f"Percentage: {percentage}%\n"
            f"Overall: {overall}\n"
            f"{custom_message}"
        )

        if send_sms_via_gateway(phone, sms_text, gateway_url):
            success_count += 1
        else:
            fail_count += 1
            failed_numbers.append(phone)

    conn.close()

    return templates.TemplateResponse("admin_sms_send_result.html", {
        "request": request,
        "user": user,
        "session_id": session_id,
        "success_count": success_count,
        "fail_count": fail_count,
        "failed_numbers": failed_numbers,
    })