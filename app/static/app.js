// ---------------- Toast ----------------
function toast(msg, kind = "ok", ms = 4000) {
  const el = document.getElementById("syncToast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast show " + kind;
  setTimeout(() => { el.className = "toast " + kind; }, ms);
}

// ---------------- Sync ----------------
const syncBtn = document.getElementById("syncBtn");
if (syncBtn) {
  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncBtn.textContent = "Sincronizzo…";
    toast("Sincronizzazione in corso… (può richiedere qualche minuto)", "ok", 60000);
    try {
      const res = await fetch("/sync", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        toast("Errore: " + (data.error || "sync fallita"), "err", 6000);
      } else {
        const s = data.synced;
        toast(`Sync OK · attività ${s.activities}, wellness ${s.wellness}, sonno ${s.sleep}, training ${s.training}, corpo ${s.body}`, "ok", 4000);
        setTimeout(() => location.reload(), 1200);
      }
    } catch (e) {
      toast("Errore di rete: " + e.message, "err", 6000);
    } finally {
      syncBtn.disabled = false;
      syncBtn.textContent = "↻ Sincronizza";
    }
  });
}

// ---------------- Stato sync in sidebar ----------------
function relativeTime(iso) {
  if (!iso) return "mai";
  const diffMin = Math.round((Date.now() - new Date(iso + "Z").getTime()) / 60000);
  if (diffMin < 1) return "adesso";
  if (diffMin < 60) return `${diffMin} min fa`;
  const h = Math.round(diffMin / 60);
  if (h < 24) return `${h} ${h === 1 ? "ora" : "ore"} fa`;
  const d = Math.round(h / 24);
  return `${d} ${d === 1 ? "giorno" : "giorni"} fa`;
}

async function refreshSyncStatus() {
  const el = document.getElementById("syncStatus");
  if (!el) return;
  try {
    const res = await fetch("/api/sync-status");
    if (!res.ok) return;
    const data = await res.json();
    let text = `Ultima sync: ${relativeTime(data.last_sync_at)}`;
    if (data.sync_failures > 0) {
      text += ` · ${data.sync_failures} tentativi falliti`;
      el.classList.add("warn");
    } else {
      el.classList.remove("warn");
    }
    el.textContent = text;
    if (data.next_scheduled_sync) {
      const next = new Date(data.next_scheduled_sync);
      el.title = "Prossima sync automatica: " + next.toLocaleString("it-IT");
    }
  } catch (e) { /* la sidebar non deve rompersi se l'endpoint non risponde */ }
}
refreshSyncStatus();

// ---------------- Chart.js defaults ----------------
if (window.Chart) {
  Chart.defaults.color = "#8b949e";
  Chart.defaults.borderColor = "#1e2530";
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif";
}

const PALETTE = {
  blue: "#2f81f7", green: "#3fb950", amber: "#d29922",
  red: "#f85149", purple: "#a371f7", cyan: "#39c5cf", pink: "#db61a2",
};

// Linea singola o multipla
function lineChart(id, labels, datasets, opts = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  new Chart(el, {
    type: "line",
    data: {
      labels,
      datasets: datasets.map((d) => ({
        label: d.label,
        data: d.data,
        borderColor: d.color,
        backgroundColor: (d.color || "#2f81f7") + "22",
        fill: d.fill ?? false,
        tension: 0.35,
        spanGaps: true,
        pointRadius: d.data && d.data.length > 30 ? 0 : 2,
        borderWidth: 2,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: datasets.length > 1, labels: { boxWidth: 12 } } },
      scales: { y: { beginAtZero: opts.beginAtZero ?? false } },
    },
  });
}

function barChart(id, labels, datasets, stacked = false) {
  const el = document.getElementById(id);
  if (!el) return;
  new Chart(el, {
    type: "bar",
    data: {
      labels,
      datasets: datasets.map((d) => ({
        label: d.label, data: d.data,
        backgroundColor: d.color, borderRadius: 4,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: datasets.length > 1, labels: { boxWidth: 12 } } },
      scales: { x: { stacked }, y: { stacked, beginAtZero: true } },
    },
  });
}

function doughnutChart(id, labels, data, colors) {
  const el = document.getElementById(id);
  if (!el) return;
  new Chart(el, {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "62%",
      plugins: { legend: { position: "right", labels: { boxWidth: 12 } } },
    },
  });
}

window.GC = { lineChart, barChart, doughnutChart, PALETTE };
