/**
 * app.js - Fast, vanilla JavaScript client for FlashForge Creator Pro Web Server.
 * Lightweight, zero-dependency, optimized for Raspberry Pi Zero W backend.
 */

// Application state cache
let lastStatus = null;
let pollTimer = null;
let isUploading = false;

document.addEventListener("DOMContentLoaded", () => {
  initDropzone();
  initControls();
  fetchStatus();
  fetchQueue();
  // Poll status every 1.5 seconds
  pollTimer = setInterval(fetchStatus, 1500);
});

/* --- API Functions --- */

async function fetchStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    lastStatus = data;
    renderStatus(data);
  } catch (err) {
    console.warn("Status fetch failed:", err);
    renderOffline();
  }
}

async function fetchQueue() {
  try {
    const res = await fetch("/api/queue");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderQueue(data);
  } catch (err) {
    console.warn("Queue fetch failed:", err);
  }
}

async function clearBed() {
  const btn = document.getElementById("btn-clear-bed");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/clear-bed", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast(data.message || "Bed clearance confirmed! Next job starting.", "success");
      await fetchStatus();
      await fetchQueue();
    } else {
      showToast(data.message || "Failed to clear bed.", "error");
    }
  } catch (err) {
    showToast("Network error while clearing bed: " + err, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function emergencyAbort() {
  if (!confirm("EMERGENCY ABORT:\nAre you sure you want to immediately halt the print, shut down heaters, and park the toolhead?")) {
    return;
  }

  try {
    const res = await fetch("/api/cancel", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast("Emergency abort sent! Printer cooling down.", "danger");
      await fetchStatus();
      await fetchQueue();
    } else {
      showToast("Abort command failed: " + data.message, "error");
    }
  } catch (err) {
    showToast("Error sending abort command: " + err, "error");
  }
}

async function queueAction(jobId, action) {
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action })
    });
    const data = await res.json();
    if (data.success) {
      await fetchQueue();
      await fetchStatus();
    } else {
      showToast(data.message || "Action failed", "error");
    }
  } catch (err) {
    showToast("Queue action error: " + err, "error");
  }
}

async function clearHistory() {
  if (!confirm("Clear all completed print history?")) return;
  try {
    await fetch("/api/queue/clear-history", { method: "POST" });
    await fetchQueue();
  } catch (err) {
    showToast("Failed to clear history: " + err, "error");
  }
}

async function sendManualGcode() {
  const input = document.getElementById("gcode-input");
  const cmd = input.value.trim();
  if (!cmd) return;

  const terminal = document.getElementById("terminal-output");
  terminal.textContent += `\n> ${cmd}\n`;
  input.value = "";

  try {
    const res = await fetch("/api/printer/send-gcode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gcode: cmd })
    });
    const data = await res.json();
    terminal.textContent += (data.response || "ok") + "\n";
    terminal.scrollTop = terminal.scrollHeight;
  } catch (err) {
    terminal.textContent += `Error: ${err}\n`;
  }
}

/* --- Render Functions --- */

function renderStatus(data) {
  // Update State Badge
  const stateBadge = document.getElementById("printer-state-badge");
  const stateText = document.getElementById("printer-state-text");
  const state = data.state || "Offline";
  const isConnected = Boolean(data.connected);

  stateBadge.className = `badge badge-${state.toLowerCase().replace(/\s+/g, "-")}`;
  stateText.textContent = state;

  // Update Connection Status Message
  const connMsgElem = document.getElementById("connection-status-msg");
  if (connMsgElem) {
    if (isConnected) {
      connMsgElem.textContent = "● Connected";
      connMsgElem.className = "connection-status-msg text-connected";
    } else {
      connMsgElem.textContent = "● Not Connected (Waiting for printer)";
      connMsgElem.className = "connection-status-msg text-disconnected";
    }
  }

  // Show/Hide Safety Gate Banner
  const safetyBanner = document.getElementById("safety-gate-banner");
  if (state === "Awaiting Bed Clearance") {
    safetyBanner.style.display = "flex";
  } else {
    safetyBanner.style.display = "none";
  }

  // Update Temperatures (only show real values when connected)
  const temps = data.temperatures || {};
  renderTemp("temp-t0", temps.tool0, isConnected);
  renderTemp("temp-t1", temps.tool1, isConnected);
  renderTemp("temp-bed", temps.bed, isConnected);

  // Update Active Job Section & Abort Button
  const activeJob = data.active_job;
  const progressSection = document.getElementById("active-job-section");
  const noActiveMsg = document.getElementById("no-active-job");
  const btnAbort = document.getElementById("btn-abort");

  if ((state === "Printing" || state === "Heating") && activeJob) {
    progressSection.style.display = "flex";
    noActiveMsg.style.display = "none";
    btnAbort.style.display = "inline-flex";

    const prog = data.progress || {};
    document.getElementById("active-filename").textContent = activeJob ? activeJob.filename : "Active Print";
    document.getElementById("active-percent").textContent = `${prog.percent || 0}%`;
    document.getElementById("progress-fill").style.width = `${prog.percent || 0}%`;

    document.getElementById("stat-lines").textContent = `${prog.lines_sent || 0} / ${prog.total_lines || 0}`;
    document.getElementById("stat-elapsed").textContent = formatSeconds(prog.elapsed_seconds || 0);

    // Calculate remaining
    if (activeJob && activeJob.estimated_seconds && prog.elapsed_seconds) {
      const rem = Math.max(0, activeJob.estimated_seconds - prog.elapsed_seconds);
      document.getElementById("stat-remaining").textContent = formatSeconds(rem);
    } else if (prog.percent > 0 && prog.elapsed_seconds > 0) {
      const totalEst = (prog.elapsed_seconds / prog.percent) * 100;
      const rem = Math.max(0, Math.round(totalEst - prog.elapsed_seconds));
      document.getElementById("stat-remaining").textContent = formatSeconds(rem);
    } else {
      document.getElementById("stat-remaining").textContent = activeJob ? activeJob.estimated_time : "Calculating...";
    }
  } else {
    progressSection.style.display = "none";
    noActiveMsg.style.display = "block";
    btnAbort.style.display = "none";
  }
}

function renderTemp(elemId, tData, isConnected) {
  const container = document.getElementById(elemId);
  if (!container) return;

  const actualElem = container.querySelector(".temp-actual");
  const targetElem = container.querySelector(".temp-target-val");

  if (!isConnected || !tData || tData.actual === null || tData.actual === undefined) {
    container.classList.add("offline");
    container.classList.remove("heating");
    if (actualElem) actualElem.textContent = "--";
    if (targetElem) targetElem.textContent = "--";
    return;
  }

  container.classList.remove("offline");
  const actual = Math.round(tData.actual);
  const target = Math.round(tData.target || 0);

  if (actualElem) actualElem.textContent = actual;
  if (targetElem) targetElem.textContent = target;

  if (target > 0 && actual < target - 2) {
    container.classList.add("heating");
  } else {
    container.classList.remove("heating");
  }
}

function renderOffline() {
  const stateBadge = document.getElementById("printer-state-badge");
  const stateText = document.getElementById("printer-state-text");
  stateBadge.className = "badge badge-offline";
  stateText.textContent = "Offline";
  document.getElementById("btn-abort").style.display = "none";

  const connMsgElem = document.getElementById("connection-status-msg");
  if (connMsgElem) {
    connMsgElem.textContent = "● Web server unreachable";
    connMsgElem.className = "connection-status-msg text-disconnected";
  }

  renderTemp("temp-t0", null, false);
  renderTemp("temp-t1", null, false);
  renderTemp("temp-bed", null, false);
}

function renderQueue(data) {
  const pending = data.pending || [];
  const history = data.history || [];

  // Update Pending List
  const pendingContainer = document.getElementById("pending-queue-list");
  const pendingCountBadge = document.getElementById("pending-count");
  pendingCountBadge.textContent = pending.length;

  if (pending.length === 0) {
    pendingContainer.innerHTML = `<li class="empty-state">No jobs in queue. Drag & drop a .gcode file above to start!</li>`;
  } else {
    pendingContainer.innerHTML = pending.map((job, index) => {
      const temps = job.target_temps || {};
      const t0 = temps.tool0 ? `T0: ${temps.tool0}°C` : "";
      const t1 = temps.tool1 ? `T1: ${temps.tool1}°C` : "";
      const bed = temps.bed ? `Bed: ${temps.bed}°C` : "";
      const tempSummary = [t0, t1, bed].filter(Boolean).join(" | ");

      return `
        <li class="queue-item">
          <div class="queue-order">#${index + 1}</div>
          <div class="queue-info">
            <div class="queue-filename" title="${escapeHtml(job.filename)}">${escapeHtml(job.filename)}</div>
            <div class="queue-meta">
              <span class="tag tag-tool">${escapeHtml(job.tool || "Right (T0)")}</span>
              ${tempSummary ? `<span class="tag tag-temp">${escapeHtml(tempSummary)}</span>` : ""}
              <span class="tag tag-time">⏱ ${escapeHtml(job.estimated_time || "Unknown")}</span>
              <span>👤 ${escapeHtml(job.owner || "Operator")}</span>
            </div>
          </div>
          <div class="queue-actions">
            ${index > 0 ? `<button class="btn-icon" title="Move Up" onclick="queueAction('${job.id}', 'up')">▲</button>` : ""}
            ${index < pending.length - 1 ? `<button class="btn-icon" title="Move Down" onclick="queueAction('${job.id}', 'down')">▼</button>` : ""}
            <button class="btn-icon" style="color: var(--accent-blue);" title="Start Now" onclick="queueAction('${job.id}', 'start_now')">▶</button>
            <button class="btn-icon" style="color: var(--accent-red);" title="Remove" onclick="queueAction('${job.id}', 'delete')">✖</button>
          </div>
        </li>
      `;
    }).join("");
  }

  // Update History List
  const historyContainer = document.getElementById("history-list");
  if (history.length === 0) {
    historyContainer.innerHTML = `<li class="empty-state">No recent print history.</li>`;
  } else {
    historyContainer.innerHTML = history.slice(-5).reverse().map(job => {
      const statusClass = job.status === "completed" ? "badge-printing" : "badge-cancelled";
      return `
        <li class="history-item">
          <div>
            <strong>${escapeHtml(job.filename)}</strong>
            <div style="font-size: 0.75rem; color: var(--text-muted);">
              ${job.completed_at || job.uploaded_at} • ${job.duration_seconds ? formatSeconds(job.duration_seconds) : job.estimated_time}
            </div>
          </div>
          <span class="badge ${statusClass}">${escapeHtml(job.status)}</span>
        </li>
      `;
    }).join("");
  }
}

/* --- Drag and Drop Upload --- */

function initDropzone() {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");

  if (!dropzone || !fileInput) return;

  dropzone.addEventListener("click", () => fileInput.click());

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files.length > 0) {
      handleFileUpload(fileInput.files[0]);
    }
  });
}

async function handleFileUpload(file) {
  if (!file.name.toLowerCase().endsWith(".gcode")) {
    showToast("Invalid file: Please upload a .gcode file.", "error");
    return;
  }

  if (isUploading) return;
  isUploading = true;

  const dropzoneTitle = document.querySelector(".dropzone-title");
  const origTitle = dropzoneTitle.textContent;
  dropzoneTitle.textContent = `Uploading ${file.name}...`;

  const ownerInput = document.getElementById("owner-input");
  const owner = ownerInput ? ownerInput.value.trim() : "Operator";

  const formData = new FormData();
  formData.append("file", file);
  formData.append("owner", owner);

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData
    });

    const data = await res.json();
    if (data.success) {
      showToast(`Added ${file.name} to print queue!`, "success");
      await fetchQueue();
      await fetchStatus();
    } else {
      showToast("Upload failed: " + (data.message || "Unknown error"), "error");
    }
  } catch (err) {
    showToast("Upload error: " + err, "error");
  } finally {
    isUploading = false;
    dropzoneTitle.textContent = origTitle;
    document.getElementById("file-input").value = "";
  }
}

/* --- UI Controls & Utilities --- */

function initControls() {
  document.getElementById("btn-clear-bed")?.addEventListener("click", clearBed);
  document.getElementById("btn-abort")?.addEventListener("click", emergencyAbort);
  document.getElementById("btn-clear-history")?.addEventListener("click", clearHistory);
  document.getElementById("btn-send-gcode")?.addEventListener("click", sendManualGcode);
  document.getElementById("gcode-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendManualGcode();
  });
}

function formatSeconds(sec) {
  if (!sec || sec <= 0) return "0s";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(msg, type = "info") {
  const existing = document.getElementById("toast-notification");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "toast-notification";
  toast.textContent = msg;

  let bg = "#21262d";
  let border = "#30363d";
  if (type === "success") { bg = "#1b4725"; border = "#2ea043"; }
  else if (type === "danger" || type === "error") { bg = "#491817"; border = "#f85149"; }

  toast.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: ${bg};
    border: 1px solid ${border};
    color: #fff;
    padding: 12px 20px;
    border-radius: 8px;
    font-size: 0.9rem;
    font-weight: 600;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    z-index: 1000;
    animation: slide-up 0.3s ease;
  `;

  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.5s ease";
    setTimeout(() => toast.remove(), 500);
  }, 3500);
}
