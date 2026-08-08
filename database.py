import hashlib
import sqlite3
from calendar import monthrange
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

DB_PATH = "mps_exams.db"
DEFAULT_CHECK_IN_TIME = "08:30"
PKT = ZoneInfo("Asia/Karachi")

# NOTE: there is no hardcoded session-start date anymore. The session's
# first day is derived dynamically -- see get_session_start() below.


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _add_column_if_missing(cur, table: str, column: str, definition: str):
    columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT setting_value FROM school_settings WHERE setting_key = ?",
        (key,),
    ).fetchone()
    return row["setting_value"] if row else default


def set_setting(conn, key: str, value: str):
    conn.execute(
        """
        INSERT INTO school_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value
        """,
        (key, value),
    )


def parse_time_hhmm(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def format_time_display(hhmm: str) -> str:
    return datetime.strptime(hhmm.strip(), "%H:%M").strftime("%I:%M %p")


def get_official_check_in_time(conn) -> str:
    return get_setting(conn, "official_check_in_time", DEFAULT_CHECK_IN_TIME)


def is_school_open(conn, day_str: str) -> bool:
    """
    A day is OPEN only if the admin has explicitly ticked it in the calendar
    (is_open = 1). Anything not marked is CLOSED by default. This is the
    single source of truth for the whole system -- no other endpoint should
    write school-open state.
    """
    row = conn.execute(
        "SELECT is_open FROM school_calendar WHERE date = ?",
        (day_str,),
    ).fetchone()
    if row is None:
        return False
    return bool(row["is_open"])


def get_school_open_status(conn, day: str) -> int:
    return 1 if is_school_open(conn, day) else 0


def bulk_set_school_days(conn, dates: dict):
    """
    dates: { "2026-09-05": True, "2026-09-06": False, ... }
    Handles single day, a dragged week, or a full vacation range in one go.

    IMPORTANT: if a date is being switched to CLOSED, any attendance already
    recorded by teachers for that date is deleted -- a day that is not a
    school day cannot have attendance against it.
    """
    for day_str, is_open in dates.items():
        try:
            date.fromisoformat(day_str)
        except (ValueError, TypeError):
            continue

        is_open_int = 1 if is_open else 0

        conn.execute(
            """
            INSERT INTO school_calendar (date, is_open)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET is_open = excluded.is_open
            """,
            (day_str, is_open_int),
        )

        if is_open_int == 0:
            conn.execute("DELETE FROM attendance WHERE date = ?", (day_str,))

    conn.commit()


def get_school_calendar_events(conn):
    """All saved overrides, for FullCalendar to color in."""
    rows = conn.execute("SELECT date, is_open FROM school_calendar ORDER BY date").fetchall()
    events = []
    for row in rows:
        events.append({
            "start": row["date"],
            "display": "background",
            "color": "#10b981" if row["is_open"] else "#334155",
            "title": "Open" if row["is_open"] else "Off",
        })
    return events


def get_session_start(conn) -> date | None:
    """
    The session's real start date -- the EARLIEST date the admin has ever
    marked as an open school day. No hardcoding: if the first day ever
    opened is 23 August, this returns 23 August. Returns None if no day
    has been marked open yet (session hasn't effectively started).
    """
    row = conn.execute("SELECT MIN(date) as d FROM school_calendar WHERE is_open = 1").fetchone()
    if row and row["d"]:
        return date.fromisoformat(row["d"])
    return None


def count_total_open_days_marked(conn, start: date | None = None, end: date | None = None) -> int:
    """
    Total school-open days marked -- every date in school_calendar with
    is_open = 1, no matter which month/year it falls in. Session-start
    filtering removed here: if admin marks a day open, it counts in the
    total, period.
    """
    query = "SELECT COUNT(*) as c FROM school_calendar WHERE is_open = 1"
    params = []
    if start:
        query += " AND date >= ?"
        params.append(start.isoformat())
    if end:
        query += " AND date <= ?"
        params.append(end.isoformat())
    row = conn.execute(query, params).fetchone()
    return row["c"] if row else 0


def determine_punctuality(check_in: datetime, official_hhmm: str) -> str:
    official = parse_time_hhmm(official_hhmm)
    official_dt = datetime.combine(check_in.date(), official, tzinfo=PKT)
    return "on-time" if check_in <= official_dt else "late"


def get_teacher_attendance(conn, teacher_email: str, day: str):
    return conn.execute(
        "SELECT * FROM attendance WHERE teacher_email = ? AND date = ?",
        (teacher_email, day),
    ).fetchone()


def record_check_in(conn, teacher_email: str, day: str):
    existing = get_teacher_attendance(conn, teacher_email, day)
    if existing:
        return existing

    now = datetime.now(PKT)
    official_time = get_official_check_in_time(conn)
    punctuality = determine_punctuality(now, official_time)

    conn.execute(
        """
        INSERT INTO attendance (teacher_email, date, check_in_time, check_in_ts, status, punctuality)
        VALUES (?, ?, ?, ?, 'present', ?)
        """,
        (
            teacher_email,
            day,
            now.strftime("%I:%M %p"),
            now.isoformat(timespec="seconds"),
            punctuality,
        ),
    )
    conn.commit()
    return get_teacher_attendance(conn, teacher_email, day)


def count_open_days_in_month(conn, year: int, month: int, up_to_day: int | None = None) -> int:
    """
    Single-query count of open days in a month, always read directly from
    school_calendar so it can never drift from the calendar UI's own state.
    """
    _, days_in_month = monthrange(year, month)
    last_day = up_to_day if up_to_day is not None else days_in_month
    start = date(year, month, 1).isoformat()
    end = date(year, month, last_day).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM school_calendar WHERE is_open = 1 AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()
    return row["c"] if row else 0


def get_progress_stats(conn, teacher_email: str, year: int | None = None, month: int | None = None) -> dict:
    today = date.today()
    year = year or today.year
    month = month or today.month

    month_start = date(year, month, 1).isoformat()
    if year == today.year and month == today.month:
        month_end = today.isoformat()
        up_to_day = today.day
    else:
        _, days_in_month = monthrange(year, month)
        month_end = date(year, month, days_in_month).isoformat()
        up_to_day = days_in_month

    total_open_days = count_open_days_in_month(conn, year, month, up_to_day)

    attendance_rows = conn.execute(
        """
        SELECT date, check_in_time, check_in_ts, punctuality
        FROM attendance
        WHERE teacher_email = ?
          AND date >= ?
          AND date <= ?
        ORDER BY date DESC
        """,
        (teacher_email, month_start, month_end),
    ).fetchall()

    present_days = len(attendance_rows)
    on_time_count = sum(1 for row in attendance_rows if row["punctuality"] == "on-time")
    late_count = sum(1 for row in attendance_rows if row["punctuality"] == "late")
    absent_days = max(total_open_days - present_days, 0)

    attendance_rate = round((present_days / total_open_days) * 100, 1) if total_open_days else 0
    punctuality_rate = round((on_time_count / present_days) * 100, 1) if present_days else 0

    return {
        "year": year,
        "month": month,
        "month_name": date(year, month, 1).strftime("%B %Y"),
        "total_open_days": total_open_days,
        "present_days": present_days,
        "absent_days": absent_days,
        "on_time_count": on_time_count,
        "late_count": late_count,
        "attendance_rate": attendance_rate,
        "punctuality_rate": punctuality_rate,
        "records": attendance_rows,
        "official_check_in_time": get_official_check_in_time(conn),
        "official_check_in_display": format_time_display(get_official_check_in_time(conn)),
    }


def get_session_progress_totals(conn, teacher_email: str) -> dict:
    """
    Fixed, always-accumulating totals for the WHOLE session so far
    (SESSION_START -> today) -- for the "Total Session" pie chart. This
    never resets and never shrinks; it only grows as the session goes on.
    """
    today = date.today()
    start = get_session_start(conn)

    if start is None or today < start:
        total_open_days = 0
        present_days = 0
    else:
        total_open_days = conn.execute(
            "SELECT COUNT(*) as c FROM school_calendar WHERE is_open = 1 AND date >= ? AND date <= ?",
            (start.isoformat(), today.isoformat()),
        ).fetchone()["c"]

        present_days = conn.execute(
            "SELECT COUNT(*) as c FROM attendance WHERE teacher_email = ? AND date >= ? AND date <= ?",
            (teacher_email, start.isoformat(), today.isoformat()),
        ).fetchone()["c"]

    absent_days = max(total_open_days - present_days, 0)
    attendance_rate = round((present_days / total_open_days) * 100, 1) if total_open_days else 0

    return {
        "total_open_days": total_open_days,
        "present_days": present_days,
        "absent_days": absent_days,
        "attendance_rate": attendance_rate,
    }


def get_monthly_progress_breakdown(conn, teacher_email: str):
    """
    One entry per session month (present/absent/rate) -- used to draw one
    pie chart per month. A new entry appears automatically once a new
    month begins, exactly like the admin calendar's month-wise counter.
    """
    breakdown = []
    for yy, mm in get_session_months(conn):
        stats = get_progress_stats(conn, teacher_email, yy, mm)
        breakdown.append({
            "month_name": stats["month_name"],
            "present_days": stats["present_days"],
            "absent_days": stats["absent_days"],
            "attendance_rate": stats["attendance_rate"],
        })
    return breakdown


def get_session_months(conn, upto: date | None = None):
    """
    All (year, month) pairs from the session's REAL start (earliest date
    ever marked open by the admin) up to today. Grows by itself every time
    a new month begins. Returns an empty list if no day has been opened
    yet at all.
    """
    session_start = get_session_start(conn)
    if session_start is None:
        return []
    upto = upto or date.today()
    if (upto.year, upto.month) < (session_start.year, session_start.month):
        return [(session_start.year, session_start.month)]
    months = []
    y, m = session_start.year, session_start.month
    while (y, m) <= (upto.year, upto.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def get_monthly_trend(conn, teacher_email: str):
    """
    Attendance % for EVERY month of the session so far (Sept 2026 -> current
    month). A new bar/point appears automatically once a new month starts.
    """
    labels, rates = [], []
    for yy, mm in get_session_months(conn):
        stats = get_progress_stats(conn, teacher_email, yy, mm)
        labels.append(stats["month_name"])
        rates.append(stats["attendance_rate"])
    return labels, rates


def get_monthly_attendance_time_trend(conn, teacher_email: str):
    """
    Average check-in TIME per month (in minutes past midnight, plus a
    human-readable version) for every month of the session so far.
    """
    labels, avg_minutes, avg_display = [], [], []
    for yy, mm in get_session_months(conn):
        label = date(yy, mm, 1).strftime("%b %Y")
        month_start = date(yy, mm, 1).isoformat()
        _, dim = monthrange(yy, mm)
        month_end = date(yy, mm, dim).isoformat()

        rows = conn.execute(
            """
            SELECT check_in_ts FROM attendance
            WHERE teacher_email = ? AND date >= ? AND date <= ? AND check_in_ts IS NOT NULL
            """,
            (teacher_email, month_start, month_end),
        ).fetchall()

        minutes_list = []
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["check_in_ts"])
                minutes_list.append(dt.hour * 60 + dt.minute)
            except Exception:
                continue

        labels.append(label)
        if minutes_list:
            avg = sum(minutes_list) / len(minutes_list)
            avg_minutes.append(round(avg, 1))
            hh, mmm = int(avg // 60), int(avg % 60)
            ampm = "AM" if hh < 12 else "PM"
            disp_hh = hh % 12 or 12
            avg_display.append(f"{disp_hh:02d}:{mmm:02d} {ampm}")
        else:
            avg_minutes.append(None)
            avg_display.append("—")

    return labels, avg_minutes, avg_display


def get_all_teachers_average_trend(conn):
    """Average monthly attendance % across all active teachers, whole session so far."""
    teachers = conn.execute(
        "SELECT email FROM users WHERE role = 'teacher' AND status = 'approved' AND is_active = 1"
    ).fetchall()

    if not teachers:
        return [], []

    labels = None
    sums = None
    count = 0
    for t in teachers:
        t_labels, t_rates = get_monthly_trend(conn, t["email"])
        if labels is None:
            labels = t_labels
            sums = [0.0] * len(t_rates)
        for i, v in enumerate(t_rates):
            sums[i] += v
        count += 1

    averages = [round(s / count, 1) if count else 0 for s in sums]
    return labels, averages


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            father_name TEXT NOT NULL,
            qualifications TEXT NOT NULL,
            experience TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'teacher',
            status TEXT NOT NULL DEFAULT 'pending'
        )
    """)

    _add_column_if_missing(cur, "users", "is_active", "INTEGER DEFAULT 1")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_email TEXT NOT NULL,
            date TEXT NOT NULL,
            check_in_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'present',
            UNIQUE(teacher_email, date)
        )
    """)

    _add_column_if_missing(cur, "attendance", "check_in_ts", "TEXT")
    _add_column_if_missing(cur, "attendance", "punctuality", "TEXT DEFAULT 'on-time'")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS school_calendar (
            date TEXT PRIMARY KEY,
            is_open INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS school_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS test_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            class_id INTEGER NOT NULL,
            UNIQUE(full_name, class_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS teacher_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_email TEXT NOT NULL,
            date TEXT NOT NULL,
            class_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            test_type_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            mark REAL,
            UNIQUE(teacher_email, date, class_id, subject_id, test_type_id, student_id)
        )
    """)

    # ------------------------------------------------------------
    # NEW TABLES FOR EXAM MARKS SYSTEM
    # ------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            test_type_id INTEGER NOT NULL,
            test_number TEXT NOT NULL,
            conduct_date TEXT NOT NULL,
            syllabus TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (class_id) REFERENCES classes(id),
            FOREIGN KEY (test_type_id) REFERENCES test_types(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_session_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            teacher_email TEXT NOT NULL DEFAULT '',
            total_marks REAL NOT NULL,
            passing_marks REAL NOT NULL DEFAULT 0,
            submitted INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES exam_sessions(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )
    """)

    _add_column_if_missing(cur, "exam_session_subjects", "syllabus", "TEXT DEFAULT ''")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_subject_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            marks_obtained REAL,
            FOREIGN KEY (session_subject_id) REFERENCES exam_session_subjects(id),
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS class_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL UNIQUE,
            template_filename TEXT NOT NULL,
            identity_columns TEXT NOT NULL DEFAULT '[]',
            extra_columns TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY (class_id) REFERENCES classes(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS student_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            father_name TEXT NOT NULL,
            extra_data TEXT NOT NULL DEFAULT '{}',
            UNIQUE(class_id, name, father_name),
            FOREIGN KEY (class_id) REFERENCES classes(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS session_result_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            overall_pass_count INTEGER NOT NULL DEFAULT 0,
            overall_fail_count INTEGER NOT NULL DEFAULT 0,
            total_students INTEGER NOT NULL DEFAULT 0,
            subject_details TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES exam_sessions(id),
            FOREIGN KEY (class_id) REFERENCES classes(id)
        )
    """)
    # ------------------------------------------------------------

    if not get_setting(conn, "official_check_in_time"):
        set_setting(conn, "official_check_in_time", DEFAULT_CHECK_IN_TIME)

    has_classes = cur.execute("SELECT id FROM classes LIMIT 1").fetchone()
    if not has_classes:
        cur.executemany("INSERT INTO classes (name) VALUES (?)", [("Class 1",), ("Class 2",)])

    has_subjects = cur.execute("SELECT id FROM subjects LIMIT 1").fetchone()
    if not has_subjects:
        cur.executemany("INSERT INTO subjects (name) VALUES (?)", [("Mathematics",), ("Science",)])

    has_tests = cur.execute("SELECT id FROM test_types LIMIT 1").fetchone()
    if not has_tests:
        cur.executemany("INSERT INTO test_types (name) VALUES (?)", [("Test 1",), ("Test 2",)])

    has_students = cur.execute("SELECT id FROM students LIMIT 1").fetchone()
    if not has_students:
        class_rows = cur.execute("SELECT id, name FROM classes ORDER BY name").fetchall()
        for class_row in class_rows:
            for i in range(1, 7):
                cur.execute(
                    "INSERT OR IGNORE INTO students (full_name, class_id) VALUES (?, ?)",
                    (f"{class_row['name']} - Student {i}", class_row["id"]),
                )

    admin_exists = cur.execute("SELECT id FROM users WHERE email = ?", ("admin@mps.com",)).fetchone()
    if not admin_exists:
        cur.execute(
            """
            INSERT INTO users
              (email, password, full_name, father_name, qualifications, experience, role, status, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            ("admin@mps.com", hash_password("admin123"), "Administrator", "N/A", "N/A", "N/A", "admin", "approved"),
        )

    conn.commit()
    conn.close()