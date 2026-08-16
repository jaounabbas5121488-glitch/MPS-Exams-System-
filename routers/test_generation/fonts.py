from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from main import require_admin, templates
from services.font_manager import get_fonts, upload_font, set_default_font, generate_font_css

router = APIRouter(prefix="/test-generation")

@router.get("/fonts", response_class=HTMLResponse)
def font_settings_page(request: Request):
    user = require_admin(request)
    fonts = get_fonts()
    urdu_fonts = [f for f in fonts if f["font_type"] == "urdu"]
    english_fonts = [f for f in fonts if f["font_type"] == "english"]
    return templates.TemplateResponse("test_generation/font_settings.html", {
        "request": request, "user": user, "urdu_fonts": urdu_fonts, "english_fonts": english_fonts,
    })

@router.post("/fonts/upload")
async def upload_font_route(request: Request, font_type: str = Form(...), file: UploadFile = File(...)):
    require_admin(request)
    if font_type not in ("urdu", "english"):
        raise HTTPException(400, "Invalid font type")
    try:
        upload_font(file, font_type)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(url="/test-generation/fonts?msg=uploaded", status_code=303)

@router.post("/fonts/set-default/{font_id}")
def set_default_font_route(font_id: int, request: Request):
    require_admin(request)
    set_default_font(font_id)
    return RedirectResponse(url="/test-generation/fonts?msg=default_updated", status_code=303)

@router.get("/fonts.css")
def fonts_css():
    return Response(content=generate_font_css(), media_type="text/css")