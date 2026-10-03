import os
import re
import html as html_lib
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pypdf import PdfReader
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from xhtml2pdf import pisa

load_dotenv()

# timeout + bounded retries: don't hang a request forever on a flaky
# upstream call, but tolerate one transient network blip.
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, timeout=30, max_retries=2)

# Requirement extraction only needs a short keyword/skill list, not prose.
# Capping output tokens cuts cost and latency with no loss of the signal
# the downstream tailoring step actually uses.
extract_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, timeout=30, max_retries=2, max_tokens=300)

def html_to_pdf_bytes(html: str) -> bytes:
    """Render generated HTML into PDF bytes.

    Both the CV and the cover letter must always be delivered as a
    PDF — never as raw HTML, Markdown, or JSON text.
    """
    buf = BytesIO()
    result = pisa.CreatePDF(src=html, dest=buf)
    if result.err:
        raise RuntimeError("Failed to render HTML to PDF.")
    return buf.getvalue()

# =====================================================================
# cv.md parsing — deterministic, not LLM-dependent, so the structure
# feeding the PDF template never drifts from cv.md's actual sections.
# =====================================================================

def _clean_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.strip()).rstrip("/")

def _section(md_text: str, name: str) -> str:
    m = re.search(rf"##\s+{name}\s*\n(.*?)(?=\n---|\n##\s|\Z)", md_text, re.DOTALL)
    return m.group(1).strip() if m else ""

def parse_base_cv(md_text: str) -> dict:
    """Parse cv.md's fixed structure into a plain dict the PDF template renders."""
    data = {}

    name_m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    data["name"] = name_m.group(1).strip() if name_m else ""

    title_m = re.search(r"\*\*(.+?)\*\*\s*\n\n\*\*Contact\*\*", md_text, re.DOTALL)
    data["title"] = title_m.group(1).strip() if title_m else ""

    contact = {}
    contact_m = re.search(r"\*\*Contact\*\*\s*\n((?:- .+\n?)+)", md_text)
    if contact_m:
        for line in contact_m.group(1).strip().split("\n"):
            m = re.match(r"-\s*(\w+):\s*(.+)", line.strip())
            if m:
                contact[m.group(1).lower()] = m.group(2).strip()
    data["contact"] = contact

    data["summary"] = _section(md_text, "SUMMARY")

    skills = {}
    for block in re.split(r"\n{2,}", _section(md_text, "SKILLS")):
        m = re.match(r"\*\*(.+?):\*\*\s*\n?(.*)", block.strip(), re.DOTALL)
        if m:
            skills[m.group(1).strip()] = re.sub(r"\s+", " ", m.group(2)).strip()
    data["skills"] = skills

    experience = []
    for block in re.split(r"\n{2,}(?=\*\*)", _section(md_text, "WORK EXPERIENCE")):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"\*\*(.+?)\*\*\s*—\s*(.+?)\s*\n\*(.+?)\*\s*\|\s*(.+)", block)
        bullets = re.findall(r"^-\s+(.+)$", block, re.MULTILINE)
        if m:
            experience.append({
                "company": m.group(1).strip(),
                "location": m.group(2).strip(),
                "role": m.group(3).strip(),
                "dates": m.group(4).strip(),
                "bullets": bullets,
            })
    data["experience"] = experience

    education = []
    for block in re.split(r"\n{2,}(?=\*\*)", _section(md_text, "EDUCATION")):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"\*\*(.+?)\*\*\s*—\s*(.+?)\s*\n\*(.+?)\*\s*\|\s*(.+)", block)
        if m:
            degree = re.sub(r"\s*[–-]\s*", ", ", m.group(3).strip(), count=1)
            education.append({
                "institution": m.group(1).strip(),
                "location": m.group(2).strip(),
                "degree": degree,
                "dates": m.group(4).strip(),
            })
    data["education"] = education

    languages = []
    for line in _section(md_text, "LANGUAGES").split("\n"):
        m = re.match(r"(.+?)\s*—\s*(.+)", line.strip())
        if m:
            languages.append((m.group(1).strip(), m.group(2).strip()))
    data["languages"] = languages

    certificates = []
    for line in _section(md_text, "CERTIFICATIONS").split("\n"):
        line = line.strip().lstrip("- ").strip()
        if not line:
            continue
        m = re.match(r"(.+?)\s*[:——]\s*(.+)", line)
        certificates.append((m.group(1).strip(), m.group(2).strip()) if m else (line, ""))
    data["certificates"] = certificates

    publications = []
    for block in re.split(r"\n{2,}(?=\*\*)", _section(md_text, "PUBLICATIONS")):
        lines_ = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if not lines_:
            continue
        title_m = re.match(r"\*\*(.+?)\*\*", lines_[0])
        publications.append({
            "title": title_m.group(1).strip() if title_m else lines_[0],
            "venue": lines_[1] if len(lines_) > 1 else "",
        })
    data["publications"] = publications

    if not data["experience"]:
        raise RuntimeError("Failed to parse WORK EXPERIENCE from cv.md — check its format.")

    return data

# =====================================================================
# LLM tailoring — only the profile paragraph and bullet wording are
# rewritten per job description. Job titles, companies, dates,
# education, skills, languages, certificates, publications are never
# touched by the LLM, so the rendered PDF's structure can never drift.
# =====================================================================

class TailoredExperience(BaseModel):
    bullets: list[str] = Field(description="Tailored bullet points for this role, same count as the original.")

class TailoredCV(BaseModel):
    title: str = Field(description="2-5 word professional headline that matches the exact job title/role stated in the JD (or the closest fitting title if the JD states none). Must change to fit each JD, never default to the candidate's original headline.")
    profile: str = Field(description="Tailored 2-3 sentence profile summary.")
    experience: list[TailoredExperience] = Field(description="One entry per job, same order as given, same bullet count per job as the original.")

tailor_llm = llm.with_structured_output(TailoredCV)

class AgentState(TypedDict):
    jd_text: str
    base_cv: Optional[str]
    base_cl: Optional[str]
    extracted_requirements: str
    optimized_cv: Optional[bytes]
    optimized_cl: Optional[str]

_EXTRACT_PROMPT = PromptTemplate.from_template(
    "Analyze the following German job description carefully. "
    "Extract the core technical skills, soft skills, key responsibilities, "
    "and any important keywords that an applicant must demonstrate. "
    "Respond ONLY as a compact bullet list, no preamble, no commentary, "
    "under 150 words.\n\nJD:\n{jd_text}"
)
_extract_chain = _EXTRACT_PROMPT | extract_llm

@lru_cache(maxsize=256)
def _extract_requirements_cached(jd_text: str) -> str:
    """Same JD text always yields the same requirements (temperature=0),
    so a repeat JD — from a retry, or a user hitting both /optimize and
    /cover_letter for one posting — skips this LLM call entirely."""
    return _extract_chain.invoke({"jd_text": jd_text}).content

def extract_requirements(state: AgentState):
    """Analyzes JD and extracts core requirements."""
    return {"extracted_requirements": _extract_requirements_cached(state["jd_text"])}

def _tailor_and_render_cv(requirements: str, jd_text: str, base_cv_md: str) -> bytes:
    data = parse_base_cv(base_cv_md)

    jobs_summary = "\n\n".join(
        f"Job {i+1}: {job['role']} at {job['company']}\nOriginal bullets:\n" +
        "\n".join(f"- {b}" for b in job["bullets"])
        for i, job in enumerate(data["experience"])
    )

    # Static instructions and the candidate's (per-deployment-constant)
    # profile/experience come first, JD-derived requirements last. OpenAI
    # caches a matching prompt prefix automatically — since this entire
    # prefix is identical on every single request (same base_cv), every
    # call after the first pays full price only for the short dynamic
    # tail, cutting input cost and latency on the bulk of the prompt.
    prompt = PromptTemplate.from_template(
        "You are an expert career coach tailoring a German CV to a specific job description.\n\n"
        "Your task:\n"
        "1. Write a new 2-5 word professional headline (the line directly under the "
        "candidate's name, e.g. 'Senior Data Scientist' or 'AI Engineer & MLOps Specialist'). "
        "This headline MUST ALWAYS change to match the exact job title stated in the JD below. "
        "If the JD states no explicit title, infer the closest fitting 2-5 word title from its "
        "responsibilities. NEVER reuse the candidate's original headline verbatim unless it "
        "happens to be an exact match for the JD's title — a generic or stale headline is a failure.\n"
        "2. Rewrite the profile summary to emphasize fit for this specific role. It MUST read as "
        "written specifically for this JD, not a generic summary — reference the role's actual "
        "focus areas from the requirements below.\n"
        "3. Rewrite each job's bullet points, keeping the EXACT SAME NUMBER of bullets per job, "
        "formatted using the XYZ framework: 'Accomplished [X] as measured by [Y], by doing [Z]'.\n"
        "4. Inject relevant keywords from the JD naturally into the bullet points.\n"
        "5. CRITICAL CONSTRAINT: Do NOT fabricate or invent any skills, metrics, or experiences "
        "not present in the original bullets. Only reorder, reframe, and emphasize what already exists. "
        "If the candidate lacks a skill, do NOT add it. The new headline must stay within the "
        "candidate's actual domain of expertise (as shown by the work experience below) — adapt "
        "wording and emphasis to the JD, do not claim an unrelated profession.\n"
        "6. Do NOT change company names, dates, locations, or the role titles inside each past "
        "work experience entry — only the headline (item 1), profile text, and bullet wording "
        "change.\n\n"
        "Here is the candidate's current professional headline:\n{title}\n\n"
        "Here is the candidate's current profile summary:\n{summary}\n\n"
        "Here are the candidate's work experience entries with their original bullet points:\n{jobs_summary}\n\n"
        "Here are the core requirements of the job:\n{requirements}\n\n"
        "Here is the full original job description, to read the exact job title from:\n{jd_text}"
    )
    chain = prompt | tailor_llm
    tailored: TailoredCV = chain.invoke({
        "requirements": requirements,
        "jd_text": jd_text,
        "title": data["title"],
        "summary": data["summary"],
        "jobs_summary": jobs_summary,
    })

    data["title"] = tailored.title
    data["summary"] = tailored.profile
    for i, job in enumerate(data["experience"]):
        if i < len(tailored.experience) and tailored.experience[i].bullets:
            job["bullets"] = tailored.experience[i].bullets

    return render_cv_pdf(data)

def reframe_cv(state: AgentState):
    """Tailors headline + profile + bullets to the JD and renders the final CV PDF."""
    pdf_bytes = _tailor_and_render_cv(state["extracted_requirements"], state["jd_text"], state["base_cv"])
    return {"optimized_cv": pdf_bytes}

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n|\n?```\s*$", re.MULTILINE)

def _strip_code_fences(text: str) -> str:
    """Defensively remove any ``` fences the model wraps around HTML output."""
    return _CODE_FENCE_RE.sub("", text.strip()).strip()

def write_cover_letter(state: AgentState):
    """Generates a tailored, human-sounding cover letter based on JD."""
    # Static instructions + the reference letter (identical every request,
    # same template file) first, JD-derived requirements last — same
    # prefix-caching rationale as the CV tailoring prompt above.
    prompt = PromptTemplate.from_template(
        "You are writing a highly targeted cover letter for a German job posting. "
        "Your goal is to produce a letter that feels genuinely written by a thoughtful, "
        "motivated candidate and not by an AI.\n\n"
        "Your task:\n"
        "1. Tailor the letter body completely to the job requirements given below.\n"
        "2. Keep the letter short, clear, and direct. Max 4 short paragraphs.\n"
        "3. Mirror the reference letter's HTML structure and CSS styling EXACTLY: same "
        "divs/classes, same order of sections (header, date, recipient-block with company "
        "and role-line, re-line, re-rule, body paragraphs, closing). There is NO "
        "salutation line ('Dear Hiring Manager' etc.) — do not add one, the reference "
        "has none and the body paragraphs start immediately after the re-rule.\n"
        "4. In the date div, output the literal placeholder text {{TODAY}} verbatim and "
        "nothing else — do NOT write an actual date or guess today's date, it will be "
        "substituted automatically. Writing any date yourself (real or placeholder-like) "
        "other than exactly {{TODAY}} is a failure.\n"
        "5. Replace the company name, role title, requisition number (only if the JD "
        "states one, otherwise omit the '(Req. #...)' part entirely), and team/department "
        "(only if the JD states one, otherwise write 'Re: {{role title}}' with no dash/team) "
        "to match this specific job.\n"
        "6. Do NOT change the contact-row (phone, email, LinkedIn, portfolio, location) — "
        "copy it from the reference exactly, unchanged.\n"
        "7. STRICT LANGUAGE RULES:\n"
        "   - NEVER use em dashes or en dashes. Replace with a comma or rewrite the sentence.\n"
        "   - Use simple, everyday language only. No complex or overly formal words.\n"
        "   - Write like a real person who genuinely wants this specific job.\n"
        "   - Be concrete: mention 1 or 2 real achievements from the reference that map to this role.\n"
        "   - Avoid filler phrases like 'I am excited to apply' or 'I believe I would be a great fit'.\n"
        "8. Do NOT fabricate experiences. Only use facts already present in the reference letter.\n"
        "9. Output ONLY the complete finalized HTML cover letter — no markdown code "
        "fences (no ``` anywhere), no commentary before or after the HTML.\n\n"
        "Here is the reference cover letter (use this for structure, CSS, and contact "
        "details — real experience only):\n{base_cl}\n\n"
        "Here are the core requirements of the job:\n{requirements}"
    )
    chain = prompt | llm
    res = chain.invoke({
        "requirements": state["extracted_requirements"],
        "base_cl": state["base_cl"]
    })
    # Leave {{TODAY}} as a literal placeholder here — the result gets
    # cached below, and substituting a real date into a cached response
    # would freeze that date for every future cache hit. The date is
    # filled in fresh on every call, cached or not, in process_cover_letter.
    return {"optimized_cl": _strip_code_fences(res.content)}

# Graphs are pure wiring with no per-request state — compiling them on
# every call (as before) is wasted work. Build each one once at import
# time and reuse it for every request.
_cv_workflow = StateGraph(AgentState)
_cv_workflow.add_node("extract", extract_requirements)
_cv_workflow.add_node("reframe", reframe_cv)
_cv_workflow.set_entry_point("extract")
_cv_workflow.add_edge("extract", "reframe")
_cv_workflow.add_edge("reframe", END)
_cv_app = _cv_workflow.compile()

_cl_workflow = StateGraph(AgentState)
_cl_workflow.add_node("extract", extract_requirements)
_cl_workflow.add_node("write_cl", write_cover_letter)
_cl_workflow.set_entry_point("extract")
_cl_workflow.add_edge("extract", "write_cl")
_cl_workflow.add_edge("write_cl", END)
_cl_app = _cl_workflow.compile()

@lru_cache(maxsize=64)
def _process_cv_cached(jd_text: str, base_cv: str) -> bytes:
    """Full CV pipeline is deterministic (temperature=0) for a given JD +
    base CV, both LLM calls included. A repeat JD — a retry, or the same
    posting run again later — returns instantly with zero LLM calls."""
    result = _cv_app.invoke({
        "jd_text": jd_text,
        "base_cv": base_cv,
        "base_cl": None,
        "extracted_requirements": "",
        "optimized_cv": None,
        "optimized_cl": None
    })
    return result["optimized_cv"]

def process_cv(jd_text: str, base_cv: str) -> bytes:
    return _process_cv_cached(jd_text, base_cv)

@lru_cache(maxsize=64)
def _process_cl_cached(jd_text: str, base_cl: str) -> str:
    """Same caching as the CV pipeline. Returns HTML with the {{TODAY}}
    placeholder still literal — date substitution happens after the cache
    lookup so cached results never carry a stale date."""
    result = _cl_app.invoke({
        "jd_text": jd_text,
        "base_cv": None,
        "base_cl": base_cl,
        "extracted_requirements": "",
        "optimized_cv": None,
        "optimized_cl": ""
    })
    return result["optimized_cl"]

def process_cover_letter(jd_text: str, base_cl: str) -> str:
    html = _process_cl_cached(jd_text, base_cl)
    today = datetime.now().strftime("%B %d, %Y")
    return re.sub(r"\{\{\s*TODAY\s*\}\}", today, html, flags=re.IGNORECASE)

# =====================================================================
# Fixed CV PDF template — mirrors cv-sayam-kumar-german.pdf's exact
# layout (header, section rules, entry structure, bolded metrics) so
# every generated CV matches it regardless of what the LLM wrote.
# =====================================================================

_METRIC_RE = re.compile(r"(\d+(?:\.\d+)?%|\d+K\+|\d+\+)")

def _highlight_metrics(text: str) -> str:
    escaped = html_lib.escape(text)
    return _METRIC_RE.sub(r"<strong>\1</strong>", escaped)

def _esc(text: str) -> str:
    return html_lib.escape(text or "")

def render_cv_html(data: dict, scale: float = 1.0) -> str:
    def pt(base: float) -> str:
        return f"{round(base * scale, 2)}pt"

    def px(base: float) -> str:
        return f"{round(base * scale, 2)}px"

    contact = data["contact"]
    contact_parts = []
    if contact.get("phone"):
        contact_parts.append(_esc(contact["phone"]))
    if contact.get("email"):
        contact_parts.append(_esc(contact["email"]))
    if contact.get("linkedin"):
        contact_parts.append(_esc(_clean_url(contact["linkedin"])))
    if contact.get("portfolio"):
        contact_parts.append(_esc(_clean_url(contact["portfolio"])))
    if contact.get("location"):
        contact_parts.append(_esc(contact["location"]))
    contact_line = " | ".join(contact_parts)

    experience_html = ""
    for job in data["experience"]:
        bullets_html = "".join(f"<li>{_highlight_metrics(b)}</li>" for b in job["bullets"])
        experience_html += f"""
        <div class="entry-meta">{_esc(job['location']).upper()} &middot; {_esc(job['dates']).upper()}</div>
        <div class="role">{_esc(job['role'])}</div>
        <div class="company">{_esc(job['company'])}</div>
        <ul class="bullets">{bullets_html}</ul>
        """

    education_html = ""
    for edu in data["education"]:
        education_html += f"""
        <div class="entry-meta">{_esc(edu['location']).upper()} &middot; {_esc(edu['dates']).upper()}</div>
        <div class="role">{_esc(edu['degree'])}</div>
        <div class="institution">{_esc(edu['institution'])}</div>
        """

    skills_html = "".join(
        f'<p class="skill-line"><strong>{_esc(cat)}:</strong> {_esc(items)}</p>'
        for cat, items in data["skills"].items()
    )

    lang_html = "&nbsp;&nbsp;&nbsp;&nbsp;".join(
        f"<strong>{_esc(name)}</strong> &mdash; {_esc(level)}" for name, level in data["languages"]
    )

    certs_html = "".join(
        f"<li><strong>{_esc(name)}</strong> &mdash; {_esc(org)}</li>" for name, org in data["certificates"]
    )

    pubs_html = "".join(
        f"<li><strong>{_esc(p['title'])}</strong> &mdash; {_esc(p['venue'])}</li>" for p in data["publications"]
    )

    optional_sections = ""
    if data["languages"]:
        optional_sections += f'<div class="section-title">LANGUAGES</div><p class="lang-line">{lang_html}</p>'
    if data["certificates"]:
        optional_sections += f'<div class="section-title">CERTIFICATES &amp; COURSES</div><ul class="plain-list">{certs_html}</ul>'
    if data["publications"]:
        optional_sections += f'<div class="section-title">PUBLICATIONS</div><ul class="plain-list">{pubs_html}</ul>'

    return f"""<html><head><style>
        @page {{ size: A4; margin: {px(10)} {px(28)}; }}
        body {{ font-family: Helvetica, Arial, sans-serif; color: #1a1a1a; font-size: {pt(9.5)}; }}
        .name {{ font-size: {pt(20)}; font-weight: bold; color: #1F4E79; margin-bottom: 1px; }}
        .title {{ font-size: {pt(11)}; font-weight: bold; color: #444; margin-bottom: 2px; }}
        .contact {{ font-size: {pt(8)}; color: #555; margin-bottom: {px(5)}; }}
        .section-title {{
            font-size: {pt(9.5)}; font-weight: bold; letter-spacing: 0.5px; color: #1F4E79;
            border-bottom: 1.5px solid #1F4E79; padding-bottom: 1px;
            margin-top: {px(6)}; margin-bottom: {px(3)};
            -pdf-keep-with-next: true;
        }}
        .summary {{ font-size: {pt(9)}; line-height: 1.25; margin-bottom: 1px; }}
        .entry-meta {{ font-size: {pt(7.5)}; color: #777; margin-top: {px(3)}; }}
        .role {{ font-size: {pt(9.5)}; font-weight: bold; margin-top: 0px; }}
        .company {{ font-size: {pt(9.5)}; font-weight: bold; color: #333; margin-bottom: 1px; }}
        .institution {{ font-size: {pt(9)}; color: #333; margin-bottom: 1px; }}
        ul.bullets {{ margin: 1px 0 1px 16px; padding: 0; }}
        ul.bullets li {{ font-size: {pt(8.5)}; line-height: 1.2; margin-bottom: 0px; }}
        .skill-line {{ font-size: {pt(8.5)}; line-height: 1.25; margin: 0px; }}
        .lang-line {{ font-size: {pt(8.5)}; margin: 0px; }}
        ul.plain-list {{ margin: 1px 0 1px 16px; padding: 0; }}
        ul.plain-list li {{ font-size: {pt(8.5)}; line-height: 1.2; margin-bottom: 0px; }}
    </style></head>
    <body>
        <div class="name">{_esc(data['name'])}</div>
        <div class="title">{_esc(data['title'])}</div>
        <div class="contact">{contact_line}</div>

        <div class="section-title">PROFILE</div>
        <p class="summary">{_esc(data['summary'])}</p>

        <div class="section-title">EXPERIENCE</div>
        {experience_html}

        <div class="section-title">EDUCATION</div>
        {education_html}

        <div class="section-title">SKILLS</div>
        {skills_html}

        {optional_sections}
    </body></html>"""

def _pdf_page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(BytesIO(pdf_bytes)).pages)

def render_cv_pdf(data: dict) -> bytes:
    """Render the CV, auto-shrinking fonts/spacing until it fits on one
    page. The CV must always be exactly one page — tailored bullets can
    run longer than the reference text, so a fixed font size alone
    cannot guarantee this every time.
    """
    last_pdf_bytes = None
    for scale in (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65):
        pdf_bytes = html_to_pdf_bytes(render_cv_html(data, scale=scale))
        last_pdf_bytes = pdf_bytes
        if _pdf_page_count(pdf_bytes) <= 1:
            return pdf_bytes
    return last_pdf_bytes
