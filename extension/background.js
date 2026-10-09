// Keeps the Render backend warm so it never spins down (free tier spins
// down after ~15 min idle, and cold-starting back up takes 30-50+ seconds).
// Pinging well inside that window means the popup almost never hits a
// cold start — popup.js's own retry loop is the fallback for when Chrome
// itself was fully closed for the idle period and this alarm couldn't run.
const ALARM_NAME = 'keepBackendWarm';
const PING_INTERVAL_MINUTES = 10;
const PING_TIMEOUT_MS = 8000;

async function pingBackend() {
  const { backendUrl } = await chrome.storage.local.get(['backendUrl']);
  if (!backendUrl) return;
  try {
    await fetch(`${backendUrl}/health`, { signal: AbortSignal.timeout(PING_TIMEOUT_MS) });
  } catch (_) {
    // Backend may be cold-starting or briefly unreachable; the next alarm
    // tick (or the popup's own retry loop when opened) will catch it.
  }
}

function ensureAlarm() {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: PING_INTERVAL_MINUTES });
}

chrome.runtime.onInstalled.addListener(() => {
  ensureAlarm();
  pingBackend();
});
chrome.runtime.onStartup.addListener(() => {
  ensureAlarm();
  pingBackend();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) pingBackend();
});
