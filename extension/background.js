// Vivaldi Session Autosaver — MV3 background service worker.

const NM_HOST = "com.cukabeka.vivaldi_session_autosaver";
const STALE_ALARM = "stale-check";
const STALE_FACTOR = 3; // no ping in 3× interval → stale

let port = null;
let reconnectDelay = 1000;

function connect() {
  if (port) return;
  try {
    port = chrome.runtime.connectNative(NM_HOST);
  } catch {
    scheduleReconnect();
    return;
  }
  reconnectDelay = 1000;
  port.onMessage.addListener(onMessage);
  port.onDisconnect.addListener(() => {
    port = null;
    const err = chrome.runtime.lastError?.message ?? "";
    if (err && !err.includes("Native host has exited")) {
      console.warn("NM disconnect:", err);
    }
    scheduleReconnect();
  });
  // Ask for current status on connect.
  port.postMessage({ type: "status" });
}

function scheduleReconnect() {
  setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 60000);
}

function onMessage(msg) {
  if (msg?.type === "status" && msg.status) {
    persistStatus(msg.status);
  } else if (msg?.type === "error") {
    console.warn("helper error:", msg.message);
  }
}

async function persistStatus(status) {
  await chrome.storage.local.set({
    lastStatus: status,
    lastPingAt: Date.now(),
  });
  updateBadge();
}

async function updateBadge() {
  const { lastStatus, lastPingAt } = await chrome.storage.local.get([
    "lastStatus",
    "lastPingAt",
  ]);
  if (!lastStatus?.last_backup || !lastPingAt) {
    await setBadge("?", "#9e9e9e"); // unknown / not installed
    return;
  }
  const ageMin = (Date.now() - lastPingAt) / 60000;
  // Contact is refreshed every ~5 min by the watchdog alarm while Vivaldi
  // runs; no ping beyond that window means the helper is not connected.
  if (ageMin > 15) {
    await setBadge("!", "#d32f2f"); // stale — no contact
    return;
  }
  const sinceBackupMin =
    (Date.now() - Date.parse(lastStatus.last_backup.replace("Z", "+00:00"))) / 60000;
  const text = sinceBackupMin < 60 ? `${Math.floor(sinceBackupMin)}m` : `${Math.floor(sinceBackupMin / 60)}h`;
  const color = sinceBackupMin < 30 ? "#2e7d32" : sinceBackupMin < 90 ? "#f9a825" : "#d32f2f";
  await setBadge(text, color);
}

async function setBadge(text, color) {
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
}

// Staleness watchdog. Also wakes the service worker and reconnects the
// native messaging port — the host takes a snapshot on each connection,
// so this doubles as the periodic backup trigger while Vivaldi runs.
chrome.alarms.create(STALE_ALARM, { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === STALE_ALARM) {
    connect();
    updateBadge();
  }
});

// Popup asks for a fresh backup.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "backup_now") {
    if (port) {
      port.postMessage({ type: "backup_now" });
      sendResponse({ ok: true });
    } else {
      connect();
      sendResponse({ ok: false, error: "helper not connected" });
    }
  } else if (msg?.type === "refresh") {
    if (port) port.postMessage({ type: "status" });
    sendResponse({ ok: !!port });
  }
  return true;
});

connect();
updateBadge();
