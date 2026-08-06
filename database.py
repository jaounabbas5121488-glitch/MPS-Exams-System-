import hashlib
import sqlite3
from calendar import monthrange
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

DB_PATH = "mps_exams.db"
DEFAULT_CHECK_IN_TIME = "08:30"
PKT = ZoneInfo("Asia/Karachi")  # Pakistan Standard Time - fixes wrong attendance time


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
    Fully calendar-driven: a day is OPEN unless the admin has explicitly
    marked it CLOSED (is_open = 0) in school_calendar via the admin calendar tool.
    """
    row = conn.execute(
        "SELECT is_open FROM school_calendar WHERE date = ?",
        (day_str,),
    ).fetchone()
    if row is None:
        return True
    return bool(row["is_open"])


def get_school_open_status(conn, day: str) -> int:
    return 1 if is_school_open(conn, day) else 0


def bulk_set_school_days(conn, dates: dict):
    """
    dates: { "2026-09-05": True, "2026-09-06": False, ... }
    Used by the admin calendar (click / drag-select multiple days at once,
    e.g. a full week, or a whole vacation range).
    """
    for day_str, is_open in dates.items():
        try:
            date.fromisoformat(day_str)  # validate format, skip junk
        except (ValueError, TypeError):
            continue
        conn.execute(
            """
            INSERT INTO school_calendar (date, is_open)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET is_open = excluded.is_open
            """,
            (day_str, 1 if is_open else 0),
        )
    conn.commit()


def get_school_calendar_events(conn):
    """
    Returns saved calendar overrides formatted for FullCalendar background events.
    """
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
    _, days_in_month = monthrange(year, month)
    last_day = up_to_day if up_to_day is not None else days_in_month
    open_days = 0
    for day_num in range(1, last_day + 1):
        day_str = date(year, month, day_num).isoformat()
        if is_school_open(conn, day_str):
            open_days += 1
    return open_days


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


def get_monthly_trend(conn, teacher_email: str, months: int = 6):
    """
    Last N months (including current) attendance-rate trend for ONE teacher.
    Used to draw the same progress-report style graph on the admin panel.
    """
    today = date.today()
    seq = []
    y, m = today.year, today.month
    for i in range(months - 1, -1, -1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        seq.append((yy, mm))

    labels, rates = [], []
    for yy, mm in seq:
        stats = get_progress_stats(conn, teacher_email, yy, mm)
        labels.append(stats["month_name"])
        rates.append(stats["attendance_rate"])
    return labels, rates


def get_all_teachers_average_trend(conn, months: int = 6):
    """
    Average monthly attendance-rate trend across ALL active approved teachers,
    for the overview graph shown directly on the admin dashboard.
    """
    teachers = conn.execute(
        "SELECT email FROM users WHERE role = 'teacher' AND status = 'approved' AND is_active = 1"
    ).fetchall()

    if not teachers:
        return [], []

    labels = None
    sums = None
    count = 0
    for t in teachers:
        t_labels, t_rates = get_monthly_trend(conn, t["email"], months)
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
            is_open INTEGER NOT NULL DEFAULT 1
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

    if not get_setting(conn, "official_check_in_time"):
        set_setting(conn, "official_check_in_time", DEFAULT_CHECK_IN_TIME)

    has_classes = cur.execute("SELECT id FROM classes LIMIT 1").fetchone()
    if not has_classes:
        cur.executemany(
            "INSERT INTO classes (name) VALUES (?)",
            [("Class 1",), ("Class 2",)],
        )

    has_subjects = cur.execute("SELECT id FROM subjects LIMIT 1").fetchone()
    if not has_subjects:
        cur.executemany(
            "INSERT INTO subjects (name) VALUES (?)",
            [("Mathematics",), ("Science",)],
        )

    has_tests = cur.execute("SELECT id FROM test_types LIMIT 1").fetchone()
    if not has_tests:
        cur.executemany(
            "INSERT INTO test_types (name) VALUES (?)",
            [("Test 1",), ("Test 2",)],
        )

    has_students = cur.execute("SELECT id FROM students LIMIT 1").fetchone()
    if not has_students:
        class_rows = cur.execute("SELECT id, name FROM classes ORDER BY name").fetchall()
        for class_row in class_rows:
            for i in range(1, 7):
                cur.execute(
                    "INSERT OR IGNORE INTO students (full_name, class_id) VALUES (?, ?)",
                    (f"{class_row['name']} - Student {i}", class_row["id"]),
                )

    admin_exists = cur.execute(
        "SELECT id FROM users WHERE email = ?",
        ("admin@mps.com",),
    ).fetchone()
    if not admin_exists:
        cur.execute(
            """
            INSERT INTO users
              (email, password, full_name, father_name, qualifications, experience, role, status, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "admin@mps.com",
                hash_password("admin123"),
                "Administrator",
                "N/A",
                "N/A",
                "N/A",
                "admin",
                "approved",
            ),
        )

    conn.commit()
    conn.close()