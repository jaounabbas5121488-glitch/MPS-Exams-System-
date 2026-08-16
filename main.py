import json
from datetime import date
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from database import init_db

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="mps-secret-key-2026")
templates = Jinja2Templates(directory="templates")

# Serve static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")
# Serve uploaded files (question images, etc.)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.on_event("startup")
def startup():
    init_db()
    # initialize test generation tables
    from services.test_generation_db import init_test_generation_db
    init_test_generation_db()
    # initialize SMS templates table
    from routers.sms import init_sms_tables
    init_sms_tables()


# ─── Helper functions (used by routers) ───
def current_user(request: Request):
    return request.session.get("user")


def require_login(request: Request):
    user = current_user(request)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def shift_month(year: int, month: int, delta: int):
    m = month + delta
    y = year
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return y, m


# ─── Include routers ───
from routers import auth, admin, teacher, test_generation
from routers import sms  # ADDED: SMS router

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(teacher.router)
app.include_router(test_generation.router)
app.include_router(sms.router)  # ADDED: SMS router include