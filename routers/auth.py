from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db, hash_password

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse(url="/login")

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    from main import current_user, templates
    if current_user(request):
        user = current_user(request)
        return RedirectResponse(url="/admin" if user["role"] == "admin" else "/dashboard")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login", response_class=HTMLResponse)
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    from main import templates
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
    if user["is_active"] == 0:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Your account has been deactivated. Contact the admin."})

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

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    from main import templates
    return templates.TemplateResponse("signup.html", {"request": request, "error": None, "success": None})

@router.post("/signup", response_class=HTMLResponse)
def signup_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    father_name: str = Form(...),
    qualifications: str = Form(...),
    experience: str = Form(...),
):
    from main import templates
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