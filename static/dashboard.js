"use strict";

// Shared across advice IIFEs
let sharedAdviceRaw = "";
let refreshAdviceCalendar = null;

// --- State ---
const state = {
  days: 30,
  charts: {},
};

// --- Auth ---
async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    showLoginModal();
    throw new Error("Unauthorized");
  }
  return res;
}

// --- Helpers ---
function todayStr() {
  return new Date().toISOString().slice(0, 10);
}
function daysAgoStr(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}
function scoreColor(v) {
  if (v == null) return "#6b7280";
  if (v >= 80) return "#22c55e";
  if (v >= 60) return "#eab308";
  return "#ef4444";
}
function scoreClass(v) {
  if (v == null) return "score-neutral";
  if (v >= 80) return "score-green";
  if (v >= 60) return "score-yellow";
  return "score-red";
}
function setStatus(msg, isError = false) {
  const el = document.getElementById("status-bar");
  el.textContent = msg;
  el.className = isError ? "error" : "";
}

// --- Chart defaults ---
Chart.defaults.color = "#6b7280";
Chart.defaults.borderColor = "#2a2d3a";
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.font.size = 12;

const TIME_SCALE = {
  type: "time",
  time: { unit: "day", tooltipFormat: "yyyy-MM-dd" },
  grid: { color: "#2a2d3a" },
  ticks: { maxTicksLimit: 8 },
};
const TIME_SCALE_MINUTE = {
  type: "time",
  time: { unit: "hour", tooltipFormat: "yyyy-MM-dd HH:mm" },
  grid: { color: "#2a2d3a" },
  ticks: { maxTicksLimit: 10 },
};

function makeChart(id, config) {
  if (state.charts[id]) {
    state.charts[id].destroy();
  }
  const ctx = document.getElementById(id).getContext("2d");
  state.charts[id] = new Chart(ctx, config);
  return state.charts[id];
}

function lineDataset(label, records, color, yField = "score") {
  return {
    label,
    data: records.map((r) => ({ x: r.day, y: r[yField] ?? null })),
    borderColor: color,
    backgroundColor: color + "22",
    pointBackgroundColor: records.map((r) => scoreColor(r[yField])),
    pointRadius: 3,
    tension: 0.3,
    spanGaps: true,
    fill: false,
  };
}

// --- Score cards ---
function updateCard(prefix, records, valueField = "score", formatter = (v) => v == null ? "—" : Math.round(v)) {
  if (!records || records.length === 0) return;
  const last = records[records.length - 1];
  const v = last[valueField] ?? last.score ?? null;
  const el = document.getElementById(`card-${prefix}`);
  const dateEl = document.getElementById(`card-${prefix}-date`);
  if (!el) return;
  el.textContent = formatter(v);
  el.className = `card-value ${scoreClass(v)}`;
  if (dateEl) dateEl.textContent = last.day || "";
}

// --- Render all charts ---
function renderAll(data, hrData) {
  const { sleep = [], readiness = [], activity = [], stress = [],
          spo2 = [], temperature = [], resilience = [],
          vo2_max = [], cardiovascular_age = [] } = data;

  // Cards
  updateCard("sleep", sleep);
  updateCard("readiness", readiness);
  updateCard("activity", activity);
  updateCard("stress", stress, "stress_high",
    (v) => v == null ? "—" : `${Math.round(v)}m`);
  updateCard("spo2", spo2, "score",
    (v) => v == null ? "—" : `${v.toFixed(1)}%`);
  updateCard("temp", temperature, "temperature_deviation",
    (v) => v == null ? "—" : (v > 0 ? `+${v.toFixed(2)}` : v.toFixed(2)));

  // Scores line chart
  makeChart("chart-scores", {
    type: "line",
    data: {
      datasets: [
        lineDataset("Sleep", sleep, "#6366f1"),
        lineDataset("Readiness", readiness, "#22c55e"),
        lineDataset("Activity", activity, "#f59e0b"),
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: TIME_SCALE,
        y: { min: 0, max: 100, grid: { color: "#2a2d3a" } },
      },
      plugins: { legend: { position: "top" } },
    },
  });

  // Stress bar chart
  makeChart("chart-stress", {
    type: "bar",
    data: {
      datasets: [{
        label: "High stress (min)",
        data: stress.map((r) => ({ x: r.day, y: r.stress_high ?? null })),
        backgroundColor: "#ef444488",
        borderColor: "#ef4444",
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      scales: { x: TIME_SCALE, y: { grid: { color: "#2a2d3a" } } },
      plugins: { legend: { display: false } },
    },
  });

  // SpO2
  const spo2Scores = spo2.map((r) => {
    let v = r.spo2_percentage;
    if (typeof v === "object" && v !== null) v = v.average;
    return { x: r.day, y: v ?? r.score ?? null };
  });
  makeChart("chart-spo2", {
    type: "line",
    data: {
      datasets: [{
        label: "SpO2 (%)",
        data: spo2Scores,
        borderColor: "#38bdf8",
        backgroundColor: "#38bdf822",
        tension: 0.3,
        spanGaps: true,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      scales: {
        x: TIME_SCALE,
        y: { min: 90, max: 100, grid: { color: "#2a2d3a" } },
      },
      plugins: { legend: { display: false } },
    },
  });

  // Temperature deviation
  makeChart("chart-temp", {
    type: "line",
    data: {
      datasets: [{
        label: "Temp deviation (°C)",
        data: temperature.map((r) => ({ x: r.day, y: r.temperature_deviation ?? null })),
        borderColor: "#fb923c",
        backgroundColor: "#fb923c22",
        tension: 0.3,
        spanGaps: true,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      scales: {
        x: TIME_SCALE,
        y: {
          grid: { color: "#2a2d3a" },
          ticks: { callback: (v) => (v > 0 ? `+${v}` : v) },
        },
      },
      plugins: {
        legend: { display: false },
        annotation: undefined,
      },
    },
  });

  // Heart rate
  makeChart("chart-hr", {
    type: "line",
    data: {
      datasets: [{
        label: "BPM",
        data: hrData.map((r) => ({ x: r.timestamp, y: r.bpm })),
        borderColor: "#f43f5e",
        backgroundColor: "#f43f5e11",
        tension: 0.1,
        pointRadius: 0,
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true,
      scales: {
        x: TIME_SCALE_MINUTE,
        y: { grid: { color: "#2a2d3a" } },
      },
      plugins: { legend: { display: false } },
      animation: false,
    },
  });

  // Resilience (ordinal)
  const RESILIENCE_LABELS = { 1: "limited", 2: "adequate", 3: "solid", 4: "strong", 5: "exceptional" };
  makeChart("chart-resilience", {
    type: "line",
    data: {
      datasets: [{
        label: "Resilience",
        data: resilience.map((r) => ({ x: r.day, y: r.score ?? null })),
        borderColor: "#a78bfa",
        backgroundColor: "#a78bfa22",
        tension: 0.3,
        spanGaps: true,
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true,
      scales: {
        x: TIME_SCALE,
        y: {
          min: 0, max: 6,
          grid: { color: "#2a2d3a" },
          ticks: { stepSize: 1, callback: (v) => RESILIENCE_LABELS[v] || "" },
        },
      },
      plugins: { legend: { display: false } },
    },
  });

  // VO2 Max
  makeChart("chart-vo2", {
    type: "line",
    data: {
      datasets: [{
        label: "VO2 Max",
        data: vo2_max.map((r) => ({ x: r.day, y: r.vo2_max ?? r.score ?? null })),
        borderColor: "#34d399",
        backgroundColor: "#34d39922",
        tension: 0.3,
        spanGaps: true,
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true,
      scales: { x: TIME_SCALE, y: { grid: { color: "#2a2d3a" } } },
      plugins: { legend: { display: false } },
    },
  });

  // Cardiovascular Age
  makeChart("chart-cardio", {
    type: "line",
    data: {
      datasets: [{
        label: "Vascular Age",
        data: cardiovascular_age.map((r) => ({ x: r.day, y: r.vascular_age ?? r.score ?? null })),
        borderColor: "#fb7185",
        backgroundColor: "#fb718522",
        tension: 0.3,
        spanGaps: true,
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true,
      scales: { x: TIME_SCALE, y: { grid: { color: "#2a2d3a" } } },
      plugins: { legend: { display: false } },
    },
  });
}

// --- Sync status table ---
async function loadSyncStatus() {
  try {
    const res = await apiFetch("/api/sync/status");
    const status = await res.json();
    const table = document.getElementById("sync-status-table");
    table.innerHTML = Object.entries(status)
      .map(([m, v]) =>
        `<tr><td>${m}</td><td>${v.last_day || "never"}</td><td>${v.rows} rows</td></tr>`
      ).join("");
  } catch (_) {}
}

// --- Main load ---
async function loadData() {
  const end = todayStr();
  const start = daysAgoStr(state.days);
  const hrStart = daysAgoStr(7);

  setStatus("Loading...");

  try {
    const [metricsRes, hrRes] = await Promise.all([
      apiFetch(`/api/metrics?start=${start}&end=${end}`),
      apiFetch(`/api/heartrate?start=${hrStart}&end=${end}`),
    ]);

    if (!metricsRes.ok) throw new Error(`Metrics fetch failed: ${metricsRes.status}`);
    const data = await metricsRes.json();
    const hrData = hrRes.ok ? await hrRes.json() : [];

    renderAll(data, hrData);
    setStatus("");
    await loadSyncStatus();
  } catch (e) {
    setStatus(`Error: ${e.message}`, true);
  }
}

// --- Sync button ---
document.getElementById("sync-btn").addEventListener("click", async () => {
  const btn = document.getElementById("sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing...";
  setStatus("Syncing with Oura API...");

  try {
    const res = await apiFetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
    const result = await res.json();

    if (!res.ok) {
      setStatus(`Sync failed (${res.status}): ${result.error || res.statusText}`, true);
      return;
    }

    const synced = result.synced || {};
    const total = Object.values(synced).reduce((a, b) => a + b, 0);
    const errors = result.errors || {};
    const errMetrics = Object.keys(errors);

    let msg = total > 0 ? `Sync complete: ${total} new records fetched.` : "Sync complete: already up to date.";

    if (errMetrics.length > 0) {
      const errDetails = errMetrics.map(m => `${m}: ${errors[m].split("\n")[0]}`).join("; ");
      msg += ` | Failed — ${errDetails}`;
      setStatus(msg, true);
    } else {
      setStatus(msg);
    }
    await loadData();
  } catch (e) {
    setStatus(`Sync error: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync";
  }
});

// --- Range buttons ---
document.querySelectorAll(".range-btns button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".range-btns button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.days = parseInt(btn.dataset.days, 10);
    loadData();
  });
});

// --- Advice button ---
(function () {
  const adviceBtn = document.getElementById("advice-btn");
  const overlay = document.getElementById("advice-overlay");
  const closeBtn = document.getElementById("advice-close-btn");
  const copyBtn = document.getElementById("advice-copy-btn");
  const contentEl = document.getElementById("advice-content");
  const periodEl = document.getElementById("advice-period");
  const confirmOverlay = document.getElementById("confirm-overlay");
  const confirmOkBtn = document.getElementById("confirm-ok-btn");
  const confirmCancelBtn = document.getElementById("confirm-cancel-btn");
  function openModal() { overlay.classList.remove("hidden"); }
  function closeModal() { overlay.classList.add("hidden"); }
  function openConfirm() { confirmOverlay.classList.remove("hidden"); }
  function closeConfirm() { confirmOverlay.classList.add("hidden"); }

  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });
  confirmOverlay.addEventListener("click", (e) => { if (e.target === confirmOverlay) closeConfirm(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!confirmOverlay.classList.contains("hidden")) closeConfirm();
      else if (!overlay.classList.contains("hidden")) closeModal();
    }
  });
  closeBtn.addEventListener("click", closeModal);
  confirmCancelBtn.addEventListener("click", closeConfirm);

  copyBtn.disabled = true;
  copyBtn.addEventListener("click", async () => {
    if (!sharedAdviceRaw) return;
    let success = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(sharedAdviceRaw);
        success = true;
      } catch (_) {}
    }
    if (!success) {
      try {
        const ta = document.createElement("textarea");
        ta.value = sharedAdviceRaw;
        ta.readOnly = true;
        ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none;";
        document.body.appendChild(ta);
        ta.focus({ preventScroll: true });
        ta.select();
        ta.setSelectionRange(0, ta.value.length);
        success = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (_) {}
    }
    copyBtn.textContent = success ? "コピー済" : "失敗";
    setTimeout(() => { copyBtn.textContent = "コピー"; }, 2000);
  });

  adviceBtn.addEventListener("click", () => openConfirm());

  confirmOkBtn.addEventListener("click", async () => {
    closeConfirm();
    adviceBtn.disabled = true;
    adviceBtn.textContent = "分析中...";
    periodEl.textContent = "";
    copyBtn.disabled = true;
    copyBtn.textContent = "コピー";
    sharedAdviceRaw = "";
    contentEl.innerHTML = `
      <div class="advice-loading">
        <div class="advice-spinner"></div>
        <span>Claudeが健康データを分析しています...</span>
      </div>`;
    openModal();

    try {
      const res = await apiFetch("/api/advice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();

      if (!res.ok) {
        contentEl.innerHTML = `<p style="color:var(--red)">エラー: ${data.error || res.status}</p>`;
        return;
      }

      if (data.period) {
        periodEl.textContent = `分析期間: ${data.period.start} 〜 ${data.period.end}`;
      }
      sharedAdviceRaw = data.advice || "";
      contentEl.innerHTML = marked.parse(sharedAdviceRaw);
      copyBtn.disabled = false;
      if (typeof refreshAdviceCalendar === "function") refreshAdviceCalendar();
    } catch (e) {
      contentEl.innerHTML = `<p style="color:var(--red)">ネットワークエラー: ${e.message}</p>`;
    } finally {
      adviceBtn.disabled = false;
      adviceBtn.textContent = "Advice";
    }
  });
})();

// --- Advice History Calendar ---
(function () {
  const calState = { year: 0, month: 0, adviceDates: new Set() };

  const prevBtn    = document.getElementById("cal-prev-btn");
  const nextBtn    = document.getElementById("cal-next-btn");
  const monthLabel = document.getElementById("cal-month-label");
  const grid       = document.getElementById("calendar-grid");
  const overlay    = document.getElementById("advice-overlay");
  const contentEl  = document.getElementById("advice-content");
  const periodEl   = document.getElementById("advice-period");
  const copyBtn    = document.getElementById("advice-copy-btn");

  function openModal() { overlay.classList.remove("hidden"); }

  async function loadAdviceDates() {
    try {
      const res = await apiFetch("/api/advice/history");
      if (!res.ok) return;
      const list = await res.json();
      calState.adviceDates = new Set(list.map(e => e.day));
      renderCalendar();
    } catch (_) {}
  }

  function renderCalendar() {
    const MONTHS = ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"];
    const DAYS   = ["S","M","T","W","T","F","S"];

    monthLabel.textContent = `${calState.year}年 ${MONTHS[calState.month]}`;

    const today        = new Date().toISOString().slice(0, 10);
    const firstDow     = new Date(calState.year, calState.month, 1).getDay();
    const daysInMonth  = new Date(calState.year, calState.month + 1, 0).getDate();
    const prevMonthEnd = new Date(calState.year, calState.month, 0).getDate();

    let html = DAYS.map(d => `<div class="cal-day-name">${d}</div>`).join("");

    for (let i = firstDow - 1; i >= 0; i--) {
      html += `<div class="cal-day other-month">${prevMonthEnd - i}</div>`;
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const iso = `${calState.year}-${String(calState.month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      const isToday   = iso === today;
      const hasAdvice = calState.adviceDates.has(iso);
      let cls = "cal-day";
      if (isToday)   cls += " today";
      if (hasAdvice) cls += " has-advice";
      html += `<div class="${cls}"${hasAdvice ? ` data-date="${iso}"` : ""}>${d}</div>`;
    }

    const remainder = (firstDow + daysInMonth) % 7;
    if (remainder !== 0) {
      for (let d = 1; d <= 7 - remainder; d++) {
        html += `<div class="cal-day other-month">${d}</div>`;
      }
    }

    grid.innerHTML = html;
  }

  async function openSavedAdvice(isoDate) {
    sharedAdviceRaw = "";
    copyBtn.disabled = true;
    copyBtn.textContent = "コピー";
    periodEl.textContent = "";
    contentEl.innerHTML = `
      <div class="advice-loading">
        <div class="advice-spinner"></div>
        <span>アドバイスを読み込んでいます...</span>
      </div>`;
    openModal();

    try {
      const res  = await apiFetch(`/api/advice/history/${isoDate}`);
      const data = await res.json();
      if (!res.ok) {
        contentEl.innerHTML = `<p style="color:var(--red)">エラー: ${data.error || res.status}</p>`;
        return;
      }
      if (data.period) {
        periodEl.textContent = `分析期間: ${data.period.start} 〜 ${data.period.end}　（保存日: ${isoDate}）`;
      }
      sharedAdviceRaw = data.advice || "";
      contentEl.innerHTML = marked.parse(sharedAdviceRaw);
      copyBtn.disabled = false;
    } catch (e) {
      contentEl.innerHTML = `<p style="color:var(--red)">ネットワークエラー: ${e.message}</p>`;
    }
  }

  grid.addEventListener("click", (e) => {
    const cell = e.target.closest(".has-advice");
    if (cell) openSavedAdvice(cell.dataset.date);
  });

  prevBtn.addEventListener("click", () => {
    if (calState.month === 0) { calState.year--; calState.month = 11; }
    else calState.month--;
    renderCalendar();
  });

  nextBtn.addEventListener("click", () => {
    if (calState.month === 11) { calState.year++; calState.month = 0; }
    else calState.month++;
    renderCalendar();
  });

  const now = new Date();
  calState.year  = now.getFullYear();
  calState.month = now.getMonth();
  loadAdviceDates();
  refreshAdviceCalendar = loadAdviceDates;
})();

// --- Login modal ---
(function () {
  const overlay   = document.getElementById("login-overlay");
  const input     = document.getElementById("login-password-input");
  const submitBtn = document.getElementById("login-submit-btn");
  const errorEl   = document.getElementById("login-error");

  function showLoginModal() {
    errorEl.textContent = "";
    overlay.classList.remove("hidden");
    setTimeout(() => input.focus(), 50);
  }

  function hideLoginModal() {
    overlay.classList.add("hidden");
    input.value = "";
    errorEl.textContent = "";
  }

  window.showLoginModal = showLoginModal;

  async function doLogin() {
    const password = input.value;
    if (!password) return;
    submitBtn.disabled = true;
    submitBtn.textContent = "...";
    errorEl.textContent = "";
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        hideLoginModal();
        loadData();
        if (typeof refreshAdviceCalendar === "function") refreshAdviceCalendar();
      } else {
        const data = await res.json();
        errorEl.textContent = data.error || "パスワードが違います";
        input.value = "";
        input.focus();
      }
    } catch (e) {
      errorEl.textContent = "ネットワークエラー";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "ログイン";
    }
  }

  submitBtn.addEventListener("click", doLogin);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
})();

// --- Logout button ---
document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  showLoginModal();
});

// --- Init ---
loadData();
