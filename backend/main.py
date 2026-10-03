import os
from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import Response
from pydantic import BaseModel
from agent import process_cv, process_cover_letter, html_to_pdf_bytes

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

# Paths to bundled reference files (copied into Docker image)
# cv.md is the single canonical reference CV the AI pipeline runs on
# top of — never substitute a PDF or any other resume file here.
BASE_CV_PATH = os.path.join(os.path.dirname(__file__), "cv.md")
BASE_CL_PATH = os.path.join(os.path.dirname(__file__), "cover_letter_template.html")

class JDRequest(BaseModel):
    jd_text: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/optimize")
async def optimize_cv(request: JDRequest, _: str = Security(verify_key)):
    jd = request.jd_text.strip()
    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job Description too short.")
    if not os.path.exists(BASE_CV_PATH):
        raise HTTPException(status_code=500, detail="Base CV not found in container.")

    with open(BASE_CV_PATH, "r", encoding="utf-8") as f:
        base_cv = f.read()

    pdf_bytes = process_cv(jd, base_cv)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Sayam-Kumar-CV-German-Optimized.pdf"}
    )

@app.post("/cover_letter")
async def generate_cover_letter(request: JDRequest, _: str = Security(verify_key)):
    jd = request.jd_text.strip()
    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job Description too short.")
    if not os.path.exists(BASE_CL_PATH):
        raise HTTPException(status_code=500, detail="Cover letter template not found in container.")

    with open(BASE_CL_PATH, "r", encoding="utf-8") as f:
        base_cl = f.read()

    result = process_cover_letter(jd, base_cl)
    pdf_bytes = html_to_pdf_bytes(result)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Sayam-Kumar-Cover-Letter-German-Optimized.pdf"}
    )
