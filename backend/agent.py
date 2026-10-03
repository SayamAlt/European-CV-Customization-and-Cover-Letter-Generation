import os
from io import BytesIO
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from dotenv import load_dotenv
from xhtml2pdf import pisa

load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

def html_to_pdf_bytes(html: str) -> bytes:
    """Render the generated cover-letter HTML into PDF bytes.

    The cover letter must always be delivered as a PDF, matching the
    reference CV's PDF format — never as raw HTML.
    """
    buf = BytesIO()
    result = pisa.CreatePDF(src=html, dest=buf)
    if result.err:
        raise RuntimeError("Failed to render cover letter HTML to PDF.")
    return buf.getvalue()

class AgentState(TypedDict):
    jd_text: str
    base_cv: Optional[str]
    base_cl: Optional[str]
    extracted_requirements: str
    optimized_cv: Optional[str]
    optimized_cl: Optional[str]

def extract_requirements(state: AgentState):
    """Analyzes JD and extracts core requirements."""
    prompt = PromptTemplate.from_template(
        "Analyze the following German job description carefully. "
        "Extract the core technical skills, soft skills, key responsibilities, "
        "and any important keywords that an applicant must demonstrate.\n\nJD:\n{jd_text}"
    )
    chain = prompt | llm
    res = chain.invoke({"jd_text": state["jd_text"]})
    return {"extracted_requirements": res.content}

def reframe_cv(state: AgentState):
    """Reframes the base CV bullet points into XYZ format based on JD."""
    prompt = PromptTemplate.from_template(
        "You are an expert career coach tailoring a German CV to a specific job description.\n"
        "Here are the core requirements of the job:\n{requirements}\n\n"
        "Here is the candidate's base CV:\n{base_cv}\n\n"
        "Your task:\n"
        "1. Rewrite work experience bullet points to strongly emphasize skills from the job description.\n"
        "2. Format ALL bullet points using the XYZ framework: "
        "'Accomplished [X] as measured by [Y], by doing [Z]'.\n"
        "3. Inject relevant keywords from the JD naturally into the bullet points.\n"
        "4. CRITICAL CONSTRAINT: Do NOT fabricate or invent any skills, metrics, or experiences "
        "not present in the base CV. Only reorder, reframe, and emphasize what already exists. "
        "If the candidate lacks a skill, do NOT add it.\n"
        "5. Keep the structure, sections, and formatting identical to the base CV.\n"
        "6. Output the complete finalized Markdown CV."
    )
    chain = prompt | llm
    res = chain.invoke({
        "requirements": state["extracted_requirements"],
        "base_cv": state["base_cv"]
    })
    return {"optimized_cv": res.content}

def write_cover_letter(state: AgentState):
    """Generates a tailored, human-sounding cover letter based on JD."""
    prompt = PromptTemplate.from_template(
        "You are writing a highly targeted cover letter for a German job posting. "
        "Your goal is to produce a letter that feels genuinely written by a thoughtful, "
        "motivated candidate and not by an AI.\n\n"
        "Here are the core requirements of the job:\n{requirements}\n\n"
        "Here is the reference cover letter (use this for structure, contact details, "
        "HTML format, and real experience only):\n{base_cl}\n\n"
        "Your task:\n"
        "1. Tailor the letter body completely to the job requirements above.\n"
        "2. Keep the letter short, clear, and direct. Max 4 short paragraphs.\n"
        "3. Mirror the HTML structure and CSS styling from the reference letter exactly. "
        "Only change the body text content.\n"
        "4. Update the company name, role title, and date (use today's date) in the header section.\n"
        "5. STRICT LANGUAGE RULES:\n"
        "   - NEVER use em dashes or en dashes. Replace with a comma or rewrite the sentence.\n"
        "   - Use simple, everyday language only. No complex or overly formal words.\n"
        "   - Write like a real person who genuinely wants this specific job.\n"
        "   - Be concrete: mention 1 or 2 real achievements from the reference that map to this role.\n"
        "   - Avoid filler phrases like 'I am excited to apply' or 'I believe I would be a great fit'.\n"
        "6. Do NOT fabricate experiences. Only use facts already present in the reference letter.\n"
        "7. Output the complete finalized HTML cover letter."
    )
    chain = prompt | llm
    res = chain.invoke({
        "requirements": state["extracted_requirements"],
        "base_cl": state["base_cl"]
    })
    return {"optimized_cl": res.content}

def process_cv(jd_text: str, base_cv: str) -> str:
    workflow = StateGraph(AgentState)
    workflow.add_node("extract", extract_requirements)
    workflow.add_node("reframe", reframe_cv)
    workflow.set_entry_point("extract")
    workflow.add_edge("extract", "reframe")
    workflow.add_edge("reframe", END)
    app = workflow.compile()
    result = app.invoke({
        "jd_text": jd_text,
        "base_cv": base_cv,
        "base_cl": None,
        "extracted_requirements": "",
        "optimized_cv": "",
        "optimized_cl": None
    })
    return result["optimized_cv"]

def process_cover_letter(jd_text: str, base_cl: str) -> str:
    workflow = StateGraph(AgentState)
    workflow.add_node("extract", extract_requirements)
    workflow.add_node("write_cl", write_cover_letter)
    workflow.set_entry_point("extract")
    workflow.add_edge("extract", "write_cl")
    workflow.add_edge("write_cl", END)
    app = workflow.compile()
    result = app.invoke({
        "jd_text": jd_text,
        "base_cv": None,
        "base_cl": base_cl,
        "extracted_requirements": "",
        "optimized_cv": None,
        "optimized_cl": ""
    })
    return result["optimized_cl"]
