const apiKeyInput    = document.getElementById('apiKey');
const backendUrlInput = document.getElementById('backendUrl');
const saveBtn         = document.getElementById('save');
const statusEl        = document.getElementById('status');

chrome.storage.local.get(['apiKey', 'backendUrl'], (res) => {
  if (res.apiKey) apiKeyInput.value = res.apiKey;
  if (res.backendUrl) backendUrlInput.value = res.backendUrl;
});

saveBtn.addEventListener('click', () => {
  const apiKey     = apiKeyInput.value.trim();
  const backendUrl = backendUrlInput.value.trim().replace(/\/$/, '');

  if (!apiKey || !backendUrl) {
    statusEl.textContent = 'Both fields are required.';
    statusEl.style.color = '#dc2626';
    return;
  }

  chrome.storage.local.set({ apiKey, backendUrl }, () => {
    statusEl.textContent = 'Saved.';
    statusEl.style.color = '#16a34a';
    setTimeout(() => { statusEl.textContent = ''; }, 2000);
  });
});
