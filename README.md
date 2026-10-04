# European CV Customization and Cover Letter Generation

I built this because I was tired of hand-editing my CV and cover letter for every job posting during my European job search. Paste a job description into the extension, pick a country, and it hands back a tailored CV and cover letter as PDFs. Both come from your real work history. Nothing in them is made up.

It's not a template filler. An LLM pipeline reads the job description, pulls out what the role actually wants, and rewrites your headline, profile summary, and bullet points to match it, using the XYZ format recruiters and ATS systems look for. Nothing gets invented. If your CV says you're fluent in English and native in Hindi, that's what gets written, even if the job posting is in German.

## How it's put together

Two pieces: a Chrome extension (the UI) and a FastAPI backend (the brain).

The extension reads the job page you're on, grabs the text, and sends it to the backend along with which country you're applying in. The backend runs it through a LangGraph pipeline:

1. **Extract requirements** — pull the core skills, responsibilities, and keywords out of the job description.
2. **Tailor** — rewrite the CV's headline, profile, and bullets against those requirements, or write a full cover letter from scratch, grounded only in facts from your real CV.
3. **Render** — turn the result into a PDF. The CV renderer auto-shrinks fonts and spacing across several passes until it fits on exactly one page, since tailored bullets can run longer than the original text.

Both the CV and cover letter are PDFs, never raw HTML or markdown, because that's what you can actually hand to an employer.

## Supported countries

Germany, Netherlands, United Kingdom, Ireland, Austria, Sweden, each with its own reference CV and cover letter tuned to that market. A few examples: UK and Ireland CVs carry a short visa-sponsorship line near the top so recruiters can self-select instead of rejecting you late in the process. Netherlands and Sweden lean toward tighter, no-frills summaries. None of them ever claim a skill or language you don't actually have, no matter what the local convention expects.

Adding a country is a file drop, not a code change. Save a `cv_<country>.md` next to `cv.md` in `backend/templates/cv/` (optionally a matching `cover_letter_template_<country>.html` in `backend/templates/cover letter/`), and the backend picks it up on the next deploy. The `/countries` endpoint and the extension's country dropdown both read this list live, so nothing needs to be hardcoded anywhere else.

## Project structure

```
backend/
  agent.py                      LangGraph pipeline, prompts, PDF rendering, country registry
  main.py                       local dev FastAPI entrypoint
  app.py                        Render production entrypoint (uvicorn app:app)
  templates/
    cv/                         cv.md (Germany) + cv_<country>.md + rendered reference PDFs
    cover letter/                cover_letter_template.html (Germany) + per-country variants
  Dockerfile
  requirements.txt

extension/
  popup.html / popup.js          the actual UI: country picker, optimize/generate buttons
  content.js                     scrapes job description text off the current page
  options.html / options.js      backend URL + API key setup
  manifest.json
```

## Running it locally

```bash
cd backend
pip install -r requirements.txt
# needs OPENAI_API_KEY and API_SECRET_KEY in a .env file
uvicorn main:app --reload
```

Then load `extension/` as an unpacked extension in Chrome, point it at `http://localhost:8000` and your API key through the options page, and you're set.

## API

- `GET /health` — status check
- `GET /countries` — every country currently supported, auto-discovered from the templates folder
- `POST /optimize` — `{ jd_text, country }` → tailored CV as a PDF
- `POST /cover_letter` — `{ jd_text, country }` → tailored cover letter as a PDF

`country` defaults to `"germany"` if you leave it out.

## A few things worth knowing if you're reading the code

Requirement extraction and the full CV/cover letter pipeline are both cached on `(job description, base CV)`. Hit the same job posting twice, or run both `/optimize` and `/cover_letter` on it, and the second call skips the LLM entirely.

The cover letter used to only see the old reference letter, and at one point it invented a German fluency claim that wasn't anywhere in my actual CV. That's fixed now: it gets a "verified candidate facts" block built straight from `cv.md`, with an explicit rule that nothing outside that block can be stated as fact. The reference letter template is there for structure and tone, never as a source of truth.

`render_cv_pdf` tries progressively smaller font scales, several passes, until the PDF fits on one page. That's how it handles the LLM occasionally writing bullets a bit longer than the originals, without any manual trimming.

`main.py` and `app.py` both exist because this started on Hugging Face Spaces and moved to Render later. They run identical route logic now. `app.py` is the one actually live in production, since Render's Dockerfile points `uvicorn` at it.

One limitation I haven't solved: the renderer can't do photos and can't produce a real two-page layout, which matters for a country like Austria where a photo is the norm. Every country gets the same compact, text-only, single-page treatment right now. It's within what every target country will accept, just not always what's locally preferred.
