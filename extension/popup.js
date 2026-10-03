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

let pendingContent   = null;
let pendingFilename  = null;
let pendingMimeType  = null;

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
      downloadLabel.textContent = 'Download Optimized CV (.md)';
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
// Backend call
// =====================================================================
async function callBackend(endpoint, jdText) {
  if (!BACKEND_URL || !API_KEY) throw new Error('Not configured — open extension options and set your backend URL and API key.');
  const res = await fetch(`${BACKEND_URL}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY
    },
    body: JSON.stringify({ jd_text: jdText })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Server returned ${res.status}`);
  }
  return res.json();
}

// /cover_letter now returns a raw PDF file, not JSON — fetch it as a blob.
async function callBackendPDF(endpoint, jdText) {
  if (!BACKEND_URL || !API_KEY) throw new Error('Not configured — open extension options and set your backend URL and API key.');
  const res = await fetch(`${BACKEND_URL}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY
    },
    body: JSON.stringify({ jd_text: jdText })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Server returned ${res.status}`);
  }
  return res.blob();
}

// =====================================================================
// Flow: Optimize CV
// =====================================================================
cvCard.addEventListener('click', async () => {
  if (cvCard.classList.contains('disabled')) return;
  showProgress('cv');

  try {
    const jdText = await extractJDText();
    const data   = await callBackend('/optimize', jdText);

    if (data.status === 'success' && data.cv_content) {
      showResult('cv', data.cv_content, 'Sayam-Kumar-CV-German-Optimized.md', 'text/markdown');
    } else {
      showError(data.detail || 'Unexpected response from backend.');
    }
  } catch (err) {
    showError(err.message || 'Something went wrong. Check the backend is running.');
  }
});

// =====================================================================
// Flow: Generate Cover Letter
// =====================================================================
clCard.addEventListener('click', async () => {
  if (clCard.classList.contains('disabled')) return;
  showProgress('cl');

  try {
    const jdText = await extractJDText();
    const blob   = await callBackendPDF('/cover_letter', jdText);
    showResult('cl', blob, 'Sayam-Kumar-Cover-Letter-German-Optimized.pdf', 'application/pdf');
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
