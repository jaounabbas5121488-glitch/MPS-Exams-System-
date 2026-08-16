from fastapi import Request, HTTPException
from database import get_db
from main import require_login

def require_test_gen_access(request: Request):
    """Allow admin or teacher (if permission is enabled)."""
    user = require_login(request)
    if user.get("role") == "admin":
        return user
    conn = get_db()
    perm = conn.execute("SELECT allow_teachers FROM test_permissions WHERE id = 1").fetchone()
    conn.close()
    if perm and perm["allow_teachers"]:
        return user
    raise HTTPException(status_code=303, headers={"Location": "/dashboard"})