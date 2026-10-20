# European CV Customization and Cover Letter Generation

I built this because I was tired of hand-editing my CV and cover letter for every job posting during my European job search. Paste a job description into the extension, pick a country, and it hands back a tailored CV and cover letter as PDFs. Both come from your real work history. Nothing in them is made up.

It's not a template filler. An LLM pipeline reads the job description, pulls out what the role actually wants, and rewrites your headline, profile summary, and bullet points to match it, using the XYZ format recruiters and ATS systems look for. Nothing gets invented. If your CV says you're fluent in English and native in Hindi, that's what gets written, even if the job posting is in German.

## How it's put together

Two pieces: a Chrome extension (the UI) and a FastAPI backend (the brain).

The extension reads the job page you're on, grabs the text, and sends it to the backend along with which country you're applying in. The backend runs it through a LangGraph pipeline:

1. **Extract requirements** — pull the core skills, responsibilities, and keywords out of the job description.
2. **Tailor** — rewrite the CV's headline, profile, and bullets against those requirements, or write a full cover letter from scratch, grounded only in facts from your real CV, with tone and length guidance specific to the target country's hiring culture.
3. **Render** — turn the result into a PDF. The CV renderer auto-shrinks fonts and spacing across several passes until it fits on exactly one page, since tailored bullets can run longer than the original text. The cover letter is checked the same way: if it comes back longer than one page, the pipeline asks the model to condense the existing letter (never regenerate it from scratch, so facts and structure can't drift) and re-renders, up to a few attempts. Both documents are one page, deterministically, not just because the prompt asked nicely.

Both the CV and cover letter are PDFs, never raw HTML or markdown, because that's what you can actually hand to an employer.

## Supported countries

Germany, Netherlands, United Kingdom, Ireland, Austria, Sweden, Denmark, Finland, and Canada, each with its own reference CV and cover letter tuned to that market, its own accent color, and its own tone guidance baked into the LLM prompts (not just which file gets loaded). A few examples: UK and Ireland CVs carry a short visa-sponsorship line near the top so recruiters can self-select instead of rejecting you late in the process; the Canada CV instead states existing Canadian work-permit authorization (no sponsorship needed) for the same self-selection reason; UK/Ireland CVs favor a tight, action-verb-first style; Netherlands, Sweden, and Finland lean toward tighter, understated, no-frills summaries; Denmark leans direct and informal, reflecting its flat, non-hierarchical workplace culture; Canada follows standard North American reverse-chronological resume conventions; and cover letter tone shifts country to country too (warmer and more conversational for Ireland, modest and down-to-earth for Sweden and Finland, friendly and straightforward for Canada, warm and direct for Denmark, and so on). None of them ever claim a skill or language you don't actually have, no matter what the local convention expects, and none of them carry personal/demographic data (no photo, no date of birth, no marital status, no nationality field) by default — that's a deliberate modern, ATS-first standard applied everywhere, including the non-European markets (Canada) now supported.

Germany is the one deliberate exception, and it's opt-in, not hardcoded. `cv.md` declares `Photo`, `Nationality`, and `Visa` contact fields and CEFR-coded language levels (`English: C1`, not `English — Fluent`); the renderer picks those up purely because the fields exist, not because of a country name check anywhere in the code. Any other country's `cv_<slug>.md` simply doesn't declare them, so nothing bleeds across — add a `Photo:` field to a future `cv_<slug>.md` and that country gets a headshot too, no code change required.

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
  background.js                  pings the backend every 10 min to keep Render's free-tier instance warm
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

Requirement extraction and the full CV/cover letter pipeline are both cached on `(job description, base CV, country)`. Hit the same job posting twice, or run both `/optimize` and `/cover_letter` on it, and the second call skips the LLM entirely.

The cover letter used to only see the old reference letter, and at one point it invented a German fluency claim that wasn't anywhere in my actual CV. That's fixed now: it gets a "verified candidate facts" block built straight from `cv.md`, with an explicit rule that nothing outside that block can be stated as fact. The reference letter template is there for structure and tone, never as a source of truth.

`render_cv_pdf` tries progressively smaller font scales, several passes, until the PDF fits on one page. That's how it handles the LLM occasionally writing bullets a bit longer than the originals, without any manual trimming.

`main.py` and `app.py` both exist because this started on Hugging Face Spaces and moved to Render later. They run identical route logic now. `app.py` is the one actually live in production, since Render's Dockerfile points `uvicorn` at it.

Render's free tier spins the backend down after about 15 minutes idle, and waking it back up can take 30-50+ seconds — far longer than a single health-check timeout. The extension handles this two ways: `background.js` pings `/health` every 10 minutes while Chrome is running so the instance rarely gets the chance to spin down in the first place, and `popup.js` polls `/health` with backoff for up to 75 seconds on open (showing a "waking up" state) instead of failing once and leaving you stuck until you manually reload the extension.

One limitation still open: every country gets the same single-column, single-photo-at-most layout. There's no real multi-column or two-page format, so a market with a genuinely different CV layout convention (not just photo/personal-data differences, which are handled) would need actual template work, not just a new `cv_<country>.md`.
