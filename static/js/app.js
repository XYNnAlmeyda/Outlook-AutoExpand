/* ============================================================
   app.js — Outlook Auto-Expand Dashboard Frontend Logic
   SSE streaming · State management · Export
   ============================================================ */

'use strict';

// ── State ────────────────────────────────────────────────────
const state = {
  running: false,
  total: 0,
  completed: 0,
  successful: 0,
  failed: 0,
  results: [],
  sseSource: null,
  logCount: 0,
  autoScroll: true,
};

// ── DOM refs ─────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const els = {
  statusDot:       $('status-dot'),
  statusText:      $('status-text'),
  startBtn:        $('btn-start'),
  stopBtn:         $('btn-stop'),
  clearBtn:        $('btn-clear-log'),
  scrollBtn:       $('btn-scroll-toggle'),
  accountsTA:      $('accounts-input'),
  targetUrl:       $('cfg-target-url'),
  aliasesPerAcct:  $('cfg-aliases'),
  aliasStrategy:   $('cfg-strategy'),
  headlessToggle:  $('cfg-headless'),
  minDelay:        $('cfg-min-delay'),
  maxDelay:        $('cfg-max-delay'),
  console:         $('console-output'),
  progressFill:    $('progress-fill'),
  progressPct:     $('progress-pct'),
  progressLabel:   $('progress-label'),
  kpiTotal:        $('kpi-total'),
  kpiCompleted:    $('kpi-completed'),
  kpiSuccess:      $('kpi-success'),
  kpiFailed:       $('kpi-failed'),
  kpiActive:       $('kpi-active'),
  resultsBody:     $('results-body'),
  exportTxt:       $('export-txt'),
  exportCsv:       $('export-csv'),
  exportJson:      $('export-json'),
  clearResultsBtn: $('btn-clear-results'),
  toastContainer:  $('toast-container'),
};

// ── Toast notifications ───────────────────────────────────────
function toast(msg, type = 'ok', duration = 4000) {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  els.toastContainer.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── Log console ───────────────────────────────────────────────
function appendLog(entry) {
  // Prevent duplicate log lines
  const lastLine = els.console.lastElementChild;
  if (lastLine) {
    const lastTs = lastLine.querySelector('.log-ts')?.textContent;
    const lastMsg = lastLine.querySelector('.log-msg')?.textContent;
    if (lastTs === entry.ts && lastMsg === entry.message) {
      return;
    }
  }

  const line = document.createElement('div');
  line.className = `log-line log-${entry.level}`;
  line.innerHTML = `
    <span class="log-ts">${entry.ts}</span>
    <span class="log-acct" title="${entry.account}">${entry.account}</span>
    <span class="log-level">[${entry.level}]</span>
    <span class="log-msg">${escapeHtml(entry.message)}</span>
  `;
  els.console.appendChild(line);
  state.logCount++;

  // Trim to 500 lines to avoid DOM bloat
  while (els.console.childElementCount > 500) {
    els.console.removeChild(els.console.firstChild);
  }

  if (state.autoScroll) {
    els.console.scrollTop = els.console.scrollHeight;
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── KPI + progress update ─────────────────────────────────────
function updateKPIs() {
  els.kpiTotal.textContent     = state.total;
  els.kpiCompleted.textContent = state.completed;
  
  // Calculate total created aliases across all completed account results
  const totalAliases = (state.results || []).reduce(
    (sum, r) => sum + ((r.created_aliases && r.created_aliases.length) || 0), 0
  );
  els.kpiSuccess.textContent   = totalAliases > 0 ? totalAliases : state.successful;

  els.kpiFailed.textContent    = state.failed;
  els.kpiActive.textContent    = state.running ? 1 : 0;

  const pct = state.total > 0 ? Math.round((state.completed / state.total) * 100) : 0;
  els.progressFill.style.width = `${pct}%`;
  els.progressPct.textContent  = `${pct}%`;
  els.progressLabel.textContent = state.total > 0
    ? `Processing ${state.completed} of ${state.total} accounts`
    : 'No job running';
}

// ── Results table ─────────────────────────────────────────────
function renderResults() {
  if (state.results.length === 0) {
    els.resultsBody.innerHTML = `
      <tr><td colspan="4">
        <div class="empty-state">
          <div>No results yet. Start a job to see account outcomes here.</div>
        </div>
      </td></tr>`;
    return;
  }

  els.resultsBody.innerHTML = state.results.map((r, i) => {
    const statusClass = { success: 'badge-success', failed: 'badge-failed', partial: 'badge-partial' }[r.status] || 'badge-pending';
    const aliases = (r.created_aliases || []).map(a =>
      `<span class="alias-tag">${escapeHtml(a)}</span>`
    ).join('') || '<span class="text-muted">—</span>';

    return `
      <tr>
        <td>${i + 1}</td>
        <td style="font-family:'JetBrains Mono',monospace">${escapeHtml(r.email)}</td>
        <td><span class="status-badge ${statusClass}">${r.status}</span></td>
        <td><div class="alias-tags">${aliases}</div></td>
      </tr>`;
  }).join('');
}

// ── Job running state UI ──────────────────────────────────────
function setRunningState(running) {
  state.running = running;
  els.statusDot.className  = `status-dot ${running ? 'running' : 'idle'}`;
  els.statusText.textContent = running ? 'Running' : 'Idle';
  els.startBtn.disabled    = running;
  els.stopBtn.disabled     = !running;
  els.startBtn.style.opacity = running ? '0.5' : '1';
}

// ── SSE connection ────────────────────────────────────────────
function connectSSE() {
  if (state.sseSource) {
    state.sseSource.close();
    state.sseSource = null;
  }

  const source = new EventSource('/api/stream');
  state.sseSource = source;

  source.onmessage = (ev) => {
    const entry = JSON.parse(ev.data);
    appendLog(entry);

    // Detect job end from system messages
    if (entry.level === 'SUCCESS' && entry.message.includes('Batch job complete')) {
      pollStatus().then(() => {
        if (!state.running) {
          toast('Batch job completed successfully!', 'ok');
        }
      });
    }
  };

  source.onerror = () => {
    // Reconnect silently after 3s
    source.close();
    setTimeout(connectSSE, 3000);
  };
}

// ── Status polling (on demand) ────────────────────────────────
async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    state.total      = data.total;
    state.completed  = data.completed;
    state.successful = data.successful;
    state.failed     = data.failed;
    state.results    = data.results || [];

    if (data.running !== state.running) {
      setRunningState(data.running);
    }

    updateKPIs();
    renderResults();
  } catch (_) {}
}

// poll every 3s while running
setInterval(() => {
  if (state.running) pollStatus();
}, 3000);

// ── Start job ─────────────────────────────────────────────────
els.startBtn.addEventListener('click', async () => {
  const accounts = els.accountsTA.value.trim();
  if (!accounts) {
    toast('Please enter at least one account in email:password format.', 'warn');
    return;
  }

  const config = {
    target_url:        els.targetUrl.value.trim()    || 'https://account.live.com/names/manage',
    aliases_per_account: parseInt(els.aliasesPerAcct.value) || 3,
    alias_strategy:    els.aliasStrategy.value,
    alias_prefix:      'alias',
    headless:          !els.headlessToggle.checked,   // toggle = headful when ON
    min_delay:         parseFloat(els.minDelay.value) || 2,
    max_delay:         parseFloat(els.maxDelay.value) || 5,
  };

  try {
    const res  = await fetch('/api/start', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ accounts, config }),
    });
    const data = await res.json();

    if (data.ok) {
      state.total = data.total;
      state.completed = state.successful = state.failed = 0;
      state.results = [];
      els.console.innerHTML = '';
      setRunningState(true);
      updateKPIs();
      renderResults();
      toast(`Job started - ${data.total} accounts queued.`, 'ok');
      connectSSE();
    } else {
      toast(`${data.error}`, 'error');
    }
  } catch (err) {
    toast(`Failed to reach server: ${err.message}`, 'error');
  }
});

// ── Stop job ──────────────────────────────────────────────────
els.stopBtn.addEventListener('click', async () => {
  try {
    await fetch('/api/stop', { method: 'POST' });
    setRunningState(false);
    toast('Job stopped.', 'warn');
  } catch (err) {
    toast(`${err.message}`, 'error');
  }
});

// ── Clear console ─────────────────────────────────────────────
els.clearBtn.addEventListener('click', async () => {
  els.console.innerHTML = '';
  state.logCount = 0;
  try {
    await fetch('/api/clear', { method: 'POST' });
  } catch (_) {}
});

// ── Clear results ─────────────────────────────────────────────
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('#btn-clear-results');
  if (btn) {
    e.preventDefault();
    state.results = [];
    state.total = 0;
    state.completed = 0;
    state.successful = 0;
    state.failed = 0;
    updateKPIs();
    renderResults();
    try {
      await fetch('/api/clear_results', { method: 'POST' });
      toast('Account results cleared.', 'ok');
    } catch (_) {}
  }
});

// ── Auto-scroll toggle ────────────────────────────────────────
els.scrollBtn.addEventListener('click', () => {
  state.autoScroll = !state.autoScroll;
  els.scrollBtn.textContent = state.autoScroll ? 'Pause Scroll' : 'Resume Scroll';
  els.scrollBtn.classList.toggle('btn-ghost', state.autoScroll);
});

// ── Exports (Client-Side Blob Download) ───────────────────────
function downloadFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

els.exportTxt.addEventListener('click', () => {
  const successResults = (state.results || []).filter(r => r.status === 'success' || (r.created_aliases && r.created_aliases.length > 0));
  if (successResults.length === 0) {
    toast('No successful results to export yet.', 'warn');
    return;
  }

  const lines = [];
  lines.push("=================================================");
  lines.push("           OUTLOOK ALIASES EXPORT");
  lines.push("=================================================");
  lines.push("");
  lines.push("--- ALL CREATED ALIASES (ALIAS:PASSWORD) ---");

  const allAliasLines = [];
  successResults.forEach(r => {
    const pwd = r.password ? `:${r.password}` : '';
    if (r.created_aliases && r.created_aliases.length > 0) {
      r.created_aliases.forEach(alias => {
        allAliasLines.push(`${alias}${pwd}`);
      });
    }
  });

  if (allAliasLines.length > 0) {
    allAliasLines.forEach(line => lines.push(line));
  } else {
    lines.push("(No aliases created)");
  }

  lines.push("");
  lines.push("--- ACCOUNT SUMMARY BREAKDOWN ---");
  successResults.forEach(r => {
    const pwd = r.password ? `:${r.password}` : '';
    const aliasesStr = (r.created_aliases || []).join(', ') || 'None';
    lines.push(`Account : ${r.email}${pwd}`);
    lines.push(`Status  : ${r.status}`);
    lines.push(`Aliases : ${aliasesStr}`);
    if (r.error) lines.push(`Error   : ${r.error}`);
    lines.push("-------------------------------------------------");
  });

  downloadFile('outlook_aliases.txt', lines.join("\n"), 'text/plain;charset=utf-8');
  toast('Downloaded outlook_aliases.txt', 'ok');
});

els.exportCsv.addEventListener('click', () => {
  const successResults = (state.results || []).filter(r => r.status === 'success' || (r.created_aliases && r.created_aliases.length > 0));
  if (successResults.length === 0) {
    toast('No successful results to export yet.', 'warn');
    return;
  }

  let csv = "Email,Password,Status,Created Aliases,Error\n";
  successResults.forEach(r => {
    const email = `"${(r.email || '').replace(/"/g, '""')}"`;
    const pwd = `"${(r.password || '').replace(/"/g, '""')}"`;
    const status = `"${(r.status || '').replace(/"/g, '""')}"`;
    const aliases = `"${((r.created_aliases || []).join(', ')).replace(/"/g, '""')}"`;
    const err = `"${(r.error || '').replace(/"/g, '""')}"`;
    csv += `${email},${pwd},${status},${aliases},${err}\n`;
  });

  downloadFile('outlook_aliases.csv', csv, 'text/csv;charset=utf-8');
  toast('Downloaded outlook_aliases.csv', 'ok');
});

els.exportJson.addEventListener('click', () => {
  const successResults = (state.results || []).filter(r => r.status === 'success' || (r.created_aliases && r.created_aliases.length > 0));
  if (successResults.length === 0) {
    toast('No successful results to export yet.', 'warn');
    return;
  }

  const jsonStr = JSON.stringify(successResults, null, 2);
  downloadFile('outlook_aliases.json', jsonStr, 'application/json;charset=utf-8');
  toast('Downloaded outlook_aliases.json', 'ok');
});

// ── Init ──────────────────────────────────────────────────────
async function loadLogs() {
  try {
    const res = await fetch('/api/logs');
    const logs = await res.json();
    if (Array.isArray(logs) && logs.length > 0 && els.console.childElementCount === 0) {
      logs.forEach(entry => appendLog(entry));
    }
  } catch (_) {}
}

(async function init() {
  state.running = false;
  state.total = 0;
  state.completed = 0;
  state.successful = 0;
  state.failed = 0;
  state.results = [];
  updateKPIs();
  renderResults();
  setRunningState(false);
  await pollStatus();
  await loadLogs();
  connectSSE();
})();
