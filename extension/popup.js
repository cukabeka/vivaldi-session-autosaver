// Vivaldi Session Autosaver — popup logic.

const $ = (id) => document.getElementById(id);

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${units[i]}`;
}

function parseStamp(iso) {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(iso);
  if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
  const d = new Date(iso?.replace("Z", "+00:00"));
  return isNaN(d) ? null : d;
}

function fmtTime(iso) {
  const d = parseStamp(iso);
  return d ? d.toLocaleString() : (iso || "—");
}

// GFS bucket label for a snapshot age in hours.
function bucketLabel(ageH) {
  if (ageH < 24) return "hourly";
  if (ageH < 24 * 7) return "daily";
  if (ageH < 24 * 7 * 5) return "weekly";
  return "old";
}

function renderSnaps(snapshots) {
  const list = $("snapList");
  if (!snapshots?.length) {
    list.innerHTML = '<div class="snap"><span class="when" style="color:var(--text2)">No snapshots yet</span></div>';
    return;
  }
  const now = Date.now();
  list.innerHTML = snapshots.map((s) => {
    const d = parseStamp(s.captured_at || s.name);
    const ageH = d ? (now - d.getTime()) / 3600000 : 0;
    return `<div class="snap">
      <span class="when">${fmtTime(s.captured_at || s.name)}</span>
      <span class="right">
        <span class="size">${fmtBytes(s.bytes)}</span>
        <span class="bucket">${bucketLabel(ageH)}</span>
      </span>
    </div>`;
  }).join("");
}

async function render() {
  const { lastStatus, lastPingAt } = await chrome.storage.local.get([
    "lastStatus",
    "lastPingAt",
  ]);

  const connected = lastPingAt && Date.now() - lastPingAt < 15 * 60 * 1000;
  $("setup").classList.toggle("hidden", connected);
  $("statusDot").className = `status-dot${connected ? " ok" : ""}`;
  const contact = $("contact");
  contact.textContent = connected ? "connected" : "not connected";
  contact.className = `value ${connected ? "ok" : "bad"}`;

  if (lastStatus) {
    $("lastBackup").textContent = fmtTime(lastStatus.last_backup);
    $("size").textContent = fmtBytes(lastStatus.total_bytes);
    renderSnaps(lastStatus.snapshots);
    const err = $("lastError");
    if (lastStatus.last_error) {
      err.textContent = `Last error: ${lastStatus.last_error}`;
      err.classList.remove("hidden");
    } else {
      err.classList.add("hidden");
    }
  }
}

// Setup hint: install.sh generates the key, derives the extension ID and
// registers everything — no manual ID handling needed.
$("installCmd").textContent = `git clone https://github.com/cukabeka/vivaldi-session-autosaver
cd vivaldi-session-autosaver && ./install.sh`;

$("copyBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("installCmd").textContent);
  $("copyBtn").textContent = "Copied!";
  setTimeout(() => ($("copyBtn").textContent = "Copy command"), 1500);
});

$("backupBtn").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "backup_now" });
  setTimeout(render, 1500);
});

// Open the helper-generated recovery report in a new tab.
// The absolute path is injected at runtime by the helper via the status
// message (report_path) — no local paths are stored in this repo.
$("reportBtn").addEventListener("click", async () => {
  const { lastStatus } = await chrome.storage.local.get(["lastStatus"]);
  const p = lastStatus?.report_path;
  if (!p) return;
  chrome.tabs.create({ url: "file://" + p });
});

render();
setInterval(render, 5000);
