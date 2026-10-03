"""
app.py — HF Spaces entry point (Gradio SDK, free tier)

HF Spaces with Gradio SDK looks for a file named app.py.
We mount our FastAPI routes onto a minimal Gradio Blocks app so the
extension's /optimize and /cover_letter endpoints work unchanged.

File strategy:
  cv.md and cover_letter_template.html are stored in the HF Storage
  Bucket mounted at /data (Read & Write, Private). The Space code
  (app.py, agent.py) lives in the Space repository. This means updating
  your CV only requires updating the file in the bucket — no Space
  redeploy needed.

Reference CV:
  cv.md is the single canonical source CV the AI pipeline runs on top
  of. Gradio SDK Spaces cannot accept binary PDF uploads through the
  bucket, so the pipeline reads plain Markdown text directly — never a
  PDF or any other resume file.
"""

import os
import gradio as gr
from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import Response
from pydantic import BaseModel
from agent import process_cv, process_cover_letter, html_to_pdf_bytes

# This Space is CPU-only (FastAPI routing + OpenAI API calls, no local
# inference). If Space hardware is pinned to ZeroGPU and can't be
# switched to CPU basic, HF refuses to start unless it finds at least
# one @spaces.GPU-decorated function. This dummy satisfies that check;
# it is never invoked. Remove this block entirely once hardware is on
# CPU basic.
try:
    import spaces

    @spaces.GPU
    def _zerogpu_startup_stub():
        return None
except ImportError:
    pass

CV_FILENAME = "cv.md"

# ---- FastAPI app with all routes ----
fastapi_app = FastAPI()

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.environ.get("API_SECRET_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_key(key: str = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return key

# Storage Bucket is mounted at /data (configured during Space creation).
# Falls back to the local Space directory for local development.
BUCKET_DIR = "/data"
LOCAL_DIR  = os.path.dirname(__file__)

def _resolve(filename: str) -> str:
    """Return path to a data file, preferring the bucket mount at /data."""
    bucket_path = os.path.join(BUCKET_DIR, filename)
    local_path  = os.path.join(LOCAL_DIR, filename)
    if os.path.exists(bucket_path):
        return bucket_path
    return local_path  # fallback for local dev

class JDRequest(BaseModel):
    jd_text: str

@fastapi_app.get("/health")
def health():
    cv_ok = os.path.exists(_resolve(CV_FILENAME))
    cl_ok = os.path.exists(_resolve("cover_letter_template.html"))
    return {
        "status": "ok",
        "cv_md_found": cv_ok,
        "cover_letter_template_found": cl_ok,
        "data_source": "bucket (/data)" if os.path.exists(f"/data/{CV_FILENAME}") else "local (Space files)"
    }

@fastapi_app.post("/optimize")
async def optimize_cv(request: JDRequest, _: str = Security(verify_key)):
    jd = request.jd_text.strip()
    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job Description too short.")
    cv_path = _resolve(CV_FILENAME)
    if not os.path.exists(cv_path):
        raise HTTPException(status_code=500, detail=f"{CV_FILENAME} not found. Upload it to the Storage Bucket at /data/.")
    with open(cv_path, "r", encoding="utf-8") as f:
        base_cv = f.read()
    pdf_bytes = process_cv(jd, base_cv)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Sayam-Kumar-CV-German-Optimized.pdf"}
    )

@fastapi_app.post("/cover_letter")
async def generate_cover_letter(request: JDRequest, _: str = Security(verify_key)):
    jd = request.jd_text.strip()
    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job Description too short.")
    cl_path = _resolve("cover_letter_template.html")
    if not os.path.exists(cl_path):
        raise HTTPException(status_code=500, detail="cover_letter_template.html not found. Upload it to the Storage Bucket at /data/.")
    cv_path = _resolve(CV_FILENAME)
    if not os.path.exists(cv_path):
        raise HTTPException(status_code=500, detail=f"{CV_FILENAME} not found. Upload it to the Storage Bucket at /data/.")
    with open(cl_path, "r", encoding="utf-8") as f:
        base_cl = f.read()
    with open(cv_path, "r", encoding="utf-8") as f:
        base_cv = f.read()
    result = process_cover_letter(jd, base_cl, base_cv)
    pdf_bytes = html_to_pdf_bytes(result)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Sayam-Kumar-Cover-Letter-German-Optimized.pdf"}
    )

# ---- Minimal Gradio UI (required by Gradio SDK) ----
cv_status = f"✅ found at /data/{CV_FILENAME}" if os.path.exists(f"/data/{CV_FILENAME}") else "⚠️ not in bucket yet — upload via HF Files"
cl_status = "✅ found at /data/cover_letter_template.html" if os.path.exists("/data/cover_letter_template.html") else "⚠️ not in bucket yet — upload via HF Files"

with gr.Blocks(title="German CV Optimizer API") as demo:
    gr.Markdown(
        f"""
        ## German CV Optimizer — Backend API
        This Space powers the **German CV Customizer** Chrome Extension.
        Use the extension in your browser to optimize your CV and generate cover letters.

        **API Endpoints:**
        - `GET /health` — Check server status + bucket file presence
        - `POST /optimize` — Optimize CV for a job description
        - `POST /cover_letter` — Generate tailored cover letter

        **Storage Bucket Status (mounted at `/data`):**
        | File | Status |
        |---|---|
        | `{CV_FILENAME}` | {cv_status} |
        | `cover_letter_template.html` | {cl_status} |

        > To update your CV without redeploying, simply replace `{CV_FILENAME}` in the bucket.
        """
    )

# ---- Mount FastAPI onto Gradio (Gradio SDK serves this as `app`) ----
app = gr.mount_gradio_app(fastapi_app, demo, path="/ui")
