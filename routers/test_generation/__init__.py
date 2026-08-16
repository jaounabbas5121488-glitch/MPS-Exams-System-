from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from main import templates
from .utils import require_test_gen_access

router = APIRouter()

# ===================== LANDING PAGE =====================
@router.get("/test-generation", response_class=HTMLResponse)
def test_generation_index(request: Request):
    user = require_test_gen_access(request)
    return templates.TemplateResponse("test_generation/index.html", {
        "request": request,
        "user": user,
    })

# ===================== INCLUDE SUB-ROUTERS =====================
from .syllabus import router as syllabus_router
from .question_bank import router as qb_router
from .generate import router as generate_router
from .permissions_history import router as ph_router
from .fonts import router as fonts_router
from .backup import router as backup_router

router.include_router(syllabus_router)
router.include_router(qb_router)
router.include_router(generate_router)
router.include_router(ph_router)
router.include_router(fonts_router)
router.include_router(backup_router)