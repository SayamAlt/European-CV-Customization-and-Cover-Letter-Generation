import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import Response
from pydantic import BaseModel
from agent import (
    process_cv,
    process_cover_letter,
    render_cover_letter_pdf,
    cv_filename_for_country,
    cl_filename_for_country,
    discover_countries,
    extract_company_name,
    sanitize_filename_segment,
)

app = FastAPI()

# CORS — allow Chrome extension to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Content-Disposition carries the LLM-derived filename (company name) —
    # without exposing it, the extension's fetch() can see the PDF bytes
    # but not the filename the backend chose for them.
    expose_headers=["Content-Disposition"],
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

# Shared pool for running the (jd_text-only, fully independent) company-name
# extraction concurrently with the CV/cover-letter pipeline instead of after
# it — see optimize_cv/generate_cover_letter below. Sized generously since
# each submitted task just blocks on one short LLM call, never CPU-bound.
_executor = ThreadPoolExecutor(max_workers=16)

# Template files (cv_<country>.md, cover_letter_template_<country>.html)
# never change while this process is running — a real edit only takes
# effect via a redeploy, which restarts the process and drops this cache
# naturally. Caching their content removes a disk read from every single
# request's critical path with zero risk of ever serving stale content.
@lru_cache(maxsize=64)
def _read_template(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

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
    base_cv = _read_template(base_cv_path)

    # extract_company_name depends only on jd_text — fully independent of
    # the CV tailoring pipeline below. Running it concurrently instead of
    # after overlaps its LLM round trip with process_cv's, shaving a full
    # sequential call off the critical path for every request.
    company_future = _executor.submit(extract_company_name, jd)
    pdf_bytes = process_cv(jd, base_cv, request.country)
    company = sanitize_filename_segment(company_future.result())
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Sayam-Kumar-CV-{company}.pdf"}
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

    base_cl = _read_template(base_cl_path)
    base_cv = _read_template(base_cv_path)

    # Same overlap trick as /optimize — company name only needs jd_text.
    company_future = _executor.submit(extract_company_name, jd)
    result = process_cover_letter(jd, base_cl, base_cv, request.country)
    pdf_bytes = render_cover_letter_pdf(result)
    company = sanitize_filename_segment(company_future.result())
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Sayam-Kumar-Cover-Letter-{company}.pdf"}
    )