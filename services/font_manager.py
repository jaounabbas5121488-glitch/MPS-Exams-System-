import os
import shutil
from fastapi import UploadFile

from database import get_db

FONT_DIR = "static/fonts"

def ensure_font_dir():
    os.makedirs(FONT_DIR, exist_ok=True)

def upload_font(file: UploadFile, font_type: str = "urdu") -> dict:
    """Save uploaded font file and add record."""
    if not file.filename.lower().endswith(('.ttf', '.otf')):
        raise ValueError("Only TTF/OTF files allowed")
    ensure_font_dir()
    filename = file.filename
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(FONT_DIR, safe_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO school_fonts (font_name, file_path, font_type, is_default)
        VALUES (?, ?, ?, 0)
    """, (safe_filename, file_path, font_type))
    conn.commit()
    conn.close()
    return {"font_name": safe_filename, "file_path": file_path}

def get_fonts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM school_fonts ORDER BY font_type, font_name").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def set_default_font(font_id: int):
    conn = get_db()
    # Reset current default for same type
    row = conn.execute("SELECT font_type FROM school_fonts WHERE id = ?", (font_id,)).fetchone()
    if not row:
        conn.close()
        return
    font_type = row["font_type"]
    conn.execute("UPDATE school_fonts SET is_default = 0 WHERE font_type = ?", (font_type,))
    conn.execute("UPDATE school_fonts SET is_default = 1 WHERE id = ?", (font_id,))
    conn.commit()
    conn.close()

def generate_font_css():
    """Return CSS @font-face rules for all fonts."""
    fonts = get_fonts()
    css = []
    for font in fonts:
        font_name_clean = font["font_name"].replace(" ", "_")
        css.append(f"""
@font-face {{
    font-family: '{font_name_clean}';
    src: url('/{font['file_path']}') format('truetype');
    font-weight: normal;
    font-style: normal;
}}
""")
    return "\n".join(css)