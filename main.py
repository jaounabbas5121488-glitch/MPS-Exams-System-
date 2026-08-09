import json
from datetime import date
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from database import init_db

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="mps-secret-key-2026")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    init_db()


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
from routers import auth, admin, teacher

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(teacher.router)