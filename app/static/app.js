// ---------------- Toast ----------------
function toast(msg, kind = "ok", ms = 4000) {
  const el = document.getElementById("syncToast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast show " + kind;
  setTimeout(() => { el.className = "toast " + kind; }, ms);
}

// ---------------- Copia link ----------------
// L'invito si manda, non si detta: il bottone mette in appunti il link intero.
document.querySelectorAll(".copy-link").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const link = btn.dataset.link;
    try {
      await navigator.clipboard.writeText(link);
      const before = btn.textContent;
      btn.textContent = "Copiato";
      setTimeout(() => { btn.textContent = before; }, 1600);
    } catch (e) {
      // Senza permesso per gli appunti (o fuori da HTTPS) resta la selezione
      // manuale: il campo e' gia' un input, basta un ctrl-C.
      const input = btn.previousElementSibling;
      if (input && input.select) { input.select(); }
      toast("Copia con Ctrl+C: il link e' selezionato", "ok", 3000);
    }
  });
});

// ---------------- Sync ----------------
const syncBtn = document.getElementById("syncBtn");

// Il testo sta in uno <span> accanto all'icona. Scrivere su textContent del
// bottone cancellerebbe l'SVG: dopo la prima sync restava solo la parola.
function syncLabel(text) {
  const span = syncBtn && syncBtn.querySelector("span");
  if (span) span.textContent = text;
}

async function runSync({ firstTime = false } = {}) {
  if (syncBtn) syncBtn.disabled = true;
  syncLabel("Sincronizzo\u2026");
  toast(
    firstTime
      ? "Scarico i tuoi dati per la prima volta\u2026 pu\u00f2 richiedere qualche minuto."
      : "Sincronizzazione in corso\u2026 (pu\u00f2 richiedere qualche minuto)",
    "ok", 120000
  );
  try {
    const res = await fetch("/sync", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      toast("Errore: " + (data.error || "sync fallita"), "err", 8000);
      return;
    }
    const s = data.synced || {};
    const parts = [
      [s.activities, "attivit\u00e0"], [s.zones, "zone FC"], [s.wellness, "wellness"],
      [s.sleep, "sonno"], [s.training, "training"], [s.body, "corpo"],
    ].filter(([n]) => n).map(([n, label]) => `${label} ${n}`);
    toast(parts.length ? "Sincronizzato \u00b7 " + parts.join(", ") : "Niente di nuovo da scaricare", "ok", 4000);
    setTimeout(() => location.reload(), 1200);
  } catch (e) {
    toast("Errore di rete: " + e.message, "err", 8000);
  } finally {
    if (syncBtn) syncBtn.disabled = false;
    syncLabel("Sincronizza");
  }
}

if (syncBtn) syncBtn.addEventListener("click", () => runSync());

// Appena collegata una sorgente la prima sincronizzazione parte da sola:
// arrivare su un'app vuota dopo aver collegato l'orologio la fa sembrare
// rotta, e nessuno dovrebbe dover scoprire che esiste un bottone da premere.
if (new URLSearchParams(location.search).has("nuova_sorgente")) {
  // Via il parametro dall'indirizzo, o un F5 farebbe ripartire tutto.
  history.replaceState({}, "", location.pathname);
  runSync({ firstTime: true });
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
// I colori li legge dal CSS: un solo posto in cui vive la palette, così i
// grafici non vanno mai fuori tono rispetto al resto della pagina.
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

if (window.Chart) {
  Chart.defaults.color = cssVar("--text-soft", "#8b949e");
  Chart.defaults.borderColor = cssVar("--hairline", "#1e2530");
  Chart.defaults.font.family = cssVar("--font-sans", "system-ui, sans-serif");
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.displayColors = false;
}

const PALETTE = {
  accent: cssVar("--accent", "#3ddc97"),
  blue: cssVar("--blue", "#5b9dff"),
  green: cssVar("--good", "#3ddc97"),
  amber: cssVar("--warn", "#e5a33d"),
  red: cssVar("--bad", "#ef6461"),
  purple: cssVar("--purple", "#a78bfa"),
  cyan: cssVar("--cyan", "#4fd1c5"),
  pink: cssVar("--pink", "#e879a8"),
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

// Performance Management Chart: barre del carico giornaliero più le due medie
// mobili. Un asse solo per fitness e fatica (stessa unità), uno separato per la
// forma, che oscilla intorno allo zero e verrebbe schiacciata sullo stesso.
function pmcChart(id, data) {
  const el = document.getElementById(id);
  if (!el) return;
  new Chart(el, {
    data: {
      labels: data.labels,
      datasets: [
        // Le barre hanno un asse tutto loro, nascosto. Sulla stessa scala
        // delle medie mobili basta una singola uscita lunga per schiacciare
        // fitness e fatica sulla riga dello zero: le barre servono a vedere
        // *quando* ci si è allenati, le linee a leggere quanto pesa.
        {
          type: "bar", label: "Carico del giorno", data: data.load,
          backgroundColor: PALETTE.blue + "40", borderRadius: 2,
          yAxisID: "yLoad", order: 3,
        },
        {
          type: "line", label: "Fitness", data: data.ctl,
          borderColor: PALETTE.accent, borderWidth: 2.5, tension: 0.4,
          pointRadius: 0, yAxisID: "y", order: 1,
        },
        {
          type: "line", label: "Fatica", data: data.atl,
          borderColor: PALETTE.amber, borderWidth: 1.5, tension: 0.4,
          pointRadius: 0, yAxisID: "y", order: 2,
        },
        {
          type: "line", label: "Forma", data: data.tsb,
          borderColor: PALETTE.purple, borderWidth: 1.5, borderDash: [4, 4],
          tension: 0.4, pointRadius: 0, yAxisID: "y1", order: 0,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { boxWidth: 10, usePointStyle: true, pointStyle: "line" } },
        tooltip: { displayColors: true },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
        y: { beginAtZero: true, title: { display: false } },
        y1: { position: "right", grid: { display: false } },
        yLoad: { display: false, beginAtZero: true },
      },
    },
  });
}

window.GC = { lineChart, barChart, doughnutChart, pmcChart, PALETTE };

// ---------------- Pannello "Altro" (navigazione mobile) ----------------
(() => {
  const moreBtn = document.getElementById("moreBtn");
  const panel = document.getElementById("morePanel");
  if (!moreBtn || !panel) return;

  function setOpen(open) {
    panel.hidden = !open;
    moreBtn.setAttribute("aria-expanded", String(open));
  }

  moreBtn.addEventListener("click", () => setOpen(panel.hidden));
  // Toccare fuori dal foglio chiude.
  panel.addEventListener("click", (e) => { if (e.target === panel) setOpen(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
})();

// ---------------- Menu account ----------------
(() => {
  const btn = document.getElementById("accountBtn");
  const menu = document.getElementById("accountMenu");
  if (!btn || !menu) return;

  function setOpen(open) {
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  }

  btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(menu.hidden); });
  // Un clic ovunque fuori dal menu lo chiude, incluso su un altro pulsante.
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target)) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { setOpen(false); btn.focus(); }
  });
})();

// --- Check-in: il bottone «Correggi» riapre il modulo già compilato ---------
// Sta in JS e non in un <details> perché il modulo deve poter comparire già
// aperto quando il check-in non c'è, e chiuso quando c'è: due stati iniziali
// diversi per lo stesso markup.
document.querySelectorAll("[data-toggle-checkin]").forEach((button) => {
  button.addEventListener("click", () => {
    const form = document.querySelector("[data-checkin-form]");
    if (form) form.hidden = !form.hidden;
  });
});
