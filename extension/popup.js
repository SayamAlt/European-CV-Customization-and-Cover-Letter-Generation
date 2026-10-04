// =====================================================================
// CONFIGURATION — backend URL and API key are set via the options page
// (chrome.storage.local), never hardcoded here.
// =====================================================================
let BACKEND_URL = null;
let API_KEY = null;

async function loadConfig() {
  const res = await chrome.storage.local.get(['apiKey', 'backendUrl']);
  BACKEND_URL = res.backendUrl || null;
  API_KEY = res.apiKey || null;
  return Boolean(BACKEND_URL && API_KEY);
}

// =====================================================================
// DOM references
// =====================================================================
const cvCard         = document.getElementById('cvCard');
const clCard         = document.getElementById('clCard');
const actionCards    = document.getElementById('actionCards');
const progressPanel  = document.getElementById('progressPanel');
const progressTitle  = document.getElementById('progressTitle');
const progressSpinner= document.getElementById('progressSpinner');
const stepsContainer = document.getElementById('stepsContainer');
const resultPanel    = document.getElementById('resultPanel');
const resultTitle    = document.getElementById('resultTitle');
const resultSubtitle = document.getElementById('resultSubtitle');
const downloadBtn    = document.getElementById('downloadBtn');
const errorPanel     = document.getElementById('errorPanel');
const errorText      = document.getElementById('errorText');
const downloadLabel  = document.getElementById('downloadLabel');
const statusDot      = document.getElementById('statusDot');
const countrySelect  = document.getElementById('countrySelect');
const logoFlag       = document.getElementById('logoFlag');
const footerText     = document.getElementById('footerText');

let pendingContent   = null;
let pendingFilename  = null;
let pendingMimeType  = null;

// =====================================================================
// Country selector — fully dynamic. The list comes from the backend's
// /countries endpoint (itself auto-discovered from cv_<country>.md
// files), so a new country added to the backend shows up here with no
// extension update or redeploy required.
// =====================================================================
function updateBrandingForCountry(flag, name) {
  logoFlag.textContent = flag || '🌍';
  footerText.textContent = name ? `v3.0 · Tailored for ${name}` : 'v3.0';
}

async function loadCountries() {
  try {
    const res = await fetch(`${BACKEND_URL}/countries`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) throw new Error();
    const { countries } = await res.json();
    if (!countries || !countries.length) throw new Error();

    const { lastCountry } = await chrome.storage.local.get(['lastCountry']);
    const defaultSlug = countries.some(c => c.slug === lastCountry) ? lastCountry : countries[0].slug;

    countrySelect.innerHTML = '';
    countries.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.slug;
      opt.textContent = `${c.flag} ${c.name}`;
      opt.dataset.flag = c.flag;
      opt.dataset.name = c.name;
      countrySelect.appendChild(opt);
    });
    countrySelect.value = defaultSlug;
    countrySelect.disabled = false;

    const selected = countries.find(c => c.slug === defaultSlug) || countries[0];
    updateBrandingForCountry(selected.flag, selected.name);
  } catch (err) {
    countrySelect.innerHTML = '<option value="">Backend unreachable</option>';
    countrySelect.disabled = true;
  }
}

countrySelect.addEventListener('change', () => {
  const opt = countrySelect.selectedOptions[0];
  if (!opt) return;
  chrome.storage.local.set({ lastCountry: opt.value });
  updateBrandingForCountry(opt.dataset.flag, opt.dataset.name);
});

function selectedCountry() {
  return countrySelect.value || 'germany';
}

// =====================================================================
// Backend health check (dim the dot if unreachable or unconfigured)
// =====================================================================
loadConfig().then((configured) => {
  if (!configured) {
    statusDot.style.background = '#dc2626';
    statusDot.style.boxShadow = '0 0 6px #dc2626';
    statusDot.title = 'Not configured — click to open setup';
    statusDot.style.cursor = 'pointer';
    statusDot.addEventListener('click', () => chrome.runtime.openOptionsPage());
    return;
  }
  fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(4000) })
    .then(r => {
      if (!r.ok) throw new Error();
    })
    .catch(() => {
      statusDot.style.background = '#f59e0b';
      statusDot.style.boxShadow = '0 0 6px #f59e0b';
      statusDot.title = 'Backend unreachable';
    });
  loadCountries();
});

// =====================================================================
// Step definitions per mode
// =====================================================================
const STEPS = {
  cv: [
    { label: 'Extracting job description from page' },
    { label: 'Analyzing requirements and keywords' },
    { label: 'Rewriting bullet points (XYZ format)' },
    { label: 'Injecting ATS keywords into CV' },
    { label: 'Finalizing optimized CV' }
  ],
  cl: [
    { label: 'Extracting job description from page' },
    { label: 'Identifying tone, role, and company' },
    { label: 'Crafting opening paragraph' },
    { label: 'Weaving in real achievements' },
    { label: 'Polishing for human readability' }
  ]
};

// =====================================================================
// Step progress animation
// =====================================================================
let stepTimer = null;

function renderSteps(mode) {
  stepsContainer.innerHTML = '';
  STEPS[mode].forEach((s, i) => {
    const el = document.createElement('div');
    el.className = 'step';
    el.id = `step-${i}`;
    el.innerHTML = `
      <div class="step-dot" id="dot-${i}"></div>
      <span>${s.label}</span>
    `;
    stepsContainer.appendChild(el);
  });
}

function animateSteps(mode, durationMs) {
  const count = STEPS[mode].length;
  const interval = durationMs / count;
  let current = 0;

  function advance() {
    if (current > 0) {
      const prev = document.getElementById(`step-${current - 1}`);
      if (prev) {
        prev.classList.remove('active');
        prev.classList.add('done');
        document.getElementById(`dot-${current - 1}`).textContent = '✓';
      }
    }
    if (current < count) {
      const el = document.getElementById(`step-${current}`);
      if (el) el.classList.add('active');
      current++;
      stepTimer = setTimeout(advance, interval);
    }
  }

  advance();
}

function completeAllSteps(mode) {
  clearTimeout(stepTimer);
  STEPS[mode].forEach((_, i) => {
    const el = document.getElementById(`step-${i}`);
    const dot = document.getElementById(`dot-${i}`);
    if (el && dot) {
      el.classList.remove('active');
      el.classList.add('done');
      dot.textContent = '✓';
    }
  });
}

// =====================================================================
// UI state helpers
// =====================================================================
function showProgress(mode) {
  hideError();
  hideResult();
  // Disable both cards
  cvCard.classList.add('disabled');
  clCard.classList.add('disabled');

  progressTitle.textContent = mode === 'cv' ? 'your CV' : 'Cover Letter';
  progressSpinner.className = `spinner spinner-${mode}`;

  renderSteps(mode);
  progressPanel.classList.add('visible');

  // Animate steps over estimated 45s — we'll complete them forcibly on success
  animateSteps(mode, 42000);
}

function showResult(mode, content, filename, mimeType) {
  completeAllSteps(mode);
  pendingContent  = content;
  pendingFilename = filename;
  pendingMimeType = mimeType;

  // Brief delay so user sees the steps completing
  setTimeout(() => {
    progressPanel.classList.remove('visible');

    if (mode === 'cv') {
      resultTitle.textContent = 'CV Optimized Successfully!';
      resultSubtitle.textContent = 'ATS-ready · XYZ format · Keywords injected';
      downloadBtn.className = 'download-btn download-btn-cv';
      downloadLabel.textContent = 'Download Optimized CV (.pdf)';
    } else {
      resultTitle.textContent = 'Cover Letter Ready!';
      resultSubtitle.textContent = 'Human-written tone · No em/en dashes · Job-tailored';
      downloadBtn.className = 'download-btn download-btn-cl';
      downloadLabel.textContent = 'Download Cover Letter (.pdf)';
    }

    resultPanel.classList.add('visible');

    // Re-enable cards
    cvCard.classList.remove('disabled');
    clCard.classList.remove('disabled');
  }, 600);
}

function showError(msg) {
  clearTimeout(stepTimer);
  progressPanel.classList.remove('visible');
  resultPanel.classList.remove('visible');
  cvCard.classList.remove('disabled');
  clCard.classList.remove('disabled');

  errorText.textContent = msg;
  errorPanel.classList.add('visible');

  // Auto-hide error after 5s
  setTimeout(() => errorPanel.classList.remove('visible'), 5000);
}

function hideError() {
  errorPanel.classList.remove('visible');
}

function hideResult() {
  resultPanel.classList.remove('visible');
  pendingContent = null;
}

// =====================================================================
// JD extraction
// =====================================================================
async function extractJDText() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return new Promise((resolve, reject) => {
    chrome.scripting.executeScript(
      { target: { tabId: tab.id }, files: ['content.js'] },
      (results) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else if (!results || !results[0]) reject(new Error("Could not extract text from this page."));
        else resolve(results[0].result);
      }
    );
  });
}

// =====================================================================
// Backend call — both /optimize and /cover_letter return a raw PDF
// file, never JSON — always fetch as a blob. The filename (built from
// the company name the backend extracts from the job description) is
// decided server-side and carried in Content-Disposition, so every
// job posting downloads to a unique, correctly-named file instead of
// a generic one the extension would have to guess at.
// =====================================================================
function filenameFromContentDisposition(res, fallback) {
  const header = res.headers.get('Content-Disposition') || '';
  const match = header.match(/filename=([^;]+)/i);
  return match ? match[1].trim().replace(/^"|"$/g, '') : fallback;
}

async function callBackendPDF(endpoint, jdText, country, fallbackFilename) {
  if (!BACKEND_URL || !API_KEY) throw new Error('Not configured — open extension options and set your backend URL and API key.');
  const res = await fetch(`${BACKEND_URL}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY
    },
    body: JSON.stringify({ jd_text: jdText, country })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Server returned ${res.status}`);
  }
  const filename = filenameFromContentDisposition(res, fallbackFilename);
  const blob = await res.blob();
  return { blob, filename };
}

// =====================================================================
// Flow: Optimize CV
// =====================================================================
cvCard.addEventListener('click', async () => {
  if (cvCard.classList.contains('disabled') || countrySelect.disabled) return;
  showProgress('cv');

  try {
    const jdText  = await extractJDText();
    const country = selectedCountry();
    const { blob, filename } = await callBackendPDF('/optimize', jdText, country, 'Sayam-Kumar-CV.pdf');
    showResult('cv', blob, filename, 'application/pdf');
  } catch (err) {
    showError(err.message || 'Something went wrong. Check the backend is running.');
  }
});

// =====================================================================
// Flow: Generate Cover Letter
// =====================================================================
clCard.addEventListener('click', async () => {
  if (clCard.classList.contains('disabled') || countrySelect.disabled) return;
  showProgress('cl');

  try {
    const jdText  = await extractJDText();
    const country = selectedCountry();
    const { blob, filename } = await callBackendPDF('/cover_letter', jdText, country, 'Sayam-Kumar-Cover-Letter.pdf');
    showResult('cl', blob, filename, 'application/pdf');
  } catch (err) {
    showError(err.message || 'Something went wrong. Check the backend is running.');
  }
});

// =====================================================================
// Download handler
// =====================================================================
downloadBtn.addEventListener('click', () => {
  if (!pendingContent) return;

  const blob = pendingContent instanceof Blob
    ? pendingContent
    : new Blob([pendingContent], { type: pendingMimeType });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = pendingFilename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});
