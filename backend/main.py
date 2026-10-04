import os
from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import Response
from pydantic import BaseModel
from agent import (
    process_cv,
    process_cover_letter,
    html_to_pdf_bytes,
    cv_filename_for_country,
    cl_filename_for_country,
    canonical_country_slug,
    country_display_name,
    discover_countries,
)

app = FastAPI()

# CORS — allow Chrome extension to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple bearer-token auth to prevent public abuse
API_KEY = os.environ.get("API_SECRET_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_key(key: str = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return key

# Paths to bundled reference files (copied into Docker image).
# CV_DIR holds cv.md (Germany) plus any cv_<country>.md files, and
# CL_DIR holds the matching cover_letter_template[_<country>].html
# files — dropping a new pair in here is the entire onboarding step
# for a new country; nothing below needs to change to support it.
BASE_DIR = os.path.dirname(__file__)
CV_DIR = os.path.join(BASE_DIR, "templates", "cv")
CL_DIR = os.path.join(BASE_DIR, "templates", "cover letter")

class JDRequest(BaseModel):
    jd_text: str
    country: str = "germany"

def _resolve_base_cv_path(country: str) -> str:
    filename = cv_filename_for_country(country)
    path = os.path.join(CV_DIR, filename)
    if not os.path.exists(path):
        available = ", ".join(c["slug"] for c in discover_countries(CV_DIR))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported country '{country}'. Available: {available}."
        )
    return path

def _resolve_base_cl_path(country: str) -> str:
    """Falls back to the shared Germany template if a country-specific
    one hasn't been added yet — the cover letter template is a
    structure/CSS/tone reference only (never a source of facts), so
    this fallback never produces an incorrect letter, just a less
    country-flavored example for the LLM to riff on."""
    path = os.path.join(CL_DIR, cl_filename_for_country(country))
    if os.path.exists(path):
        return path
    return os.path.join(CL_DIR, "cover_letter_template.html")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/countries")
def list_countries():
    """Self-describing country list — the extension (or any client)
    calls this instead of hardcoding country options, so a new
    cv_<country>.md dropped into templates/cv shows up automatically."""
    return {"countries": discover_countries(CV_DIR)}

# Plain `def`, not `async def`: process_cv/process_cover_letter block on
# network calls to the LLM. FastAPI runs sync route functions in its
# threadpool, so one slow request no longer stalls the whole event loop
# and other requests keep being served concurrently.
@app.post("/optimize")
def optimize_cv(request: JDRequest, _: str = Security(verify_key)):
    jd = request.jd_text.strip()
    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job Description too short.")

    base_cv_path = _resolve_base_cv_path(request.country)
    with open(base_cv_path, "r", encoding="utf-8") as f:
        base_cv = f.read()

    pdf_bytes = process_cv(jd, base_cv)
    country_name = country_display_name(canonical_country_slug(request.country))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Sayam-Kumar-CV-{country_name.replace(' ', '-')}-Optimized.pdf"}
    )

@app.post("/cover_letter")
def generate_cover_letter(request: JDRequest, _: str = Security(verify_key)):
    jd = request.jd_text.strip()
    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job Description too short.")

    base_cv_path = _resolve_base_cv_path(request.country)
    base_cl_path = _resolve_base_cl_path(request.country)
    if not os.path.exists(base_cl_path):
        raise HTTPException(status_code=500, detail="Cover letter template not found in container.")

    with open(base_cl_path, "r", encoding="utf-8") as f:
        base_cl = f.read()
    with open(base_cv_path, "r", encoding="utf-8") as f:
        base_cv = f.read()

    result = process_cover_letter(jd, base_cl, base_cv)
    pdf_bytes = html_to_pdf_bytes(result)
    country_name = country_display_name(canonical_country_slug(request.country))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Sayam-Kumar-Cover-Letter-{country_name.replace(' ', '-')}-Optimized.pdf"}
    )
