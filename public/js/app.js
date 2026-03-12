'use strict';

/* ─── DOM refs ────────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);

const srcEncryption     = $('srcEncryption');
const srcHost           = $('srcHost');
const srcPort           = $('srcPort');
const srcUser           = $('srcUser');
const srcPass           = $('srcPass');
const srcMailbox        = $('srcMailbox');
const srcIndicator      = $('srcIndicator');

const dstEncryption     = $('dstEncryption');
const dstHost           = $('dstHost');
const dstPort           = $('dstPort');
const dstUser           = $('dstUser');
const dstPass           = $('dstPass');
const dstMailbox        = $('dstMailbox');
const dstIndicator      = $('dstIndicator');

const optAllMailboxes   = $('optAllMailboxes');
const optBatchSize      = $('optBatchSize');

const btnTestConnections  = $('btnTestConnections');
const btnRefreshMailboxes = $('btnRefreshMailboxes');
const btnStartSync        = $('btnStartSync');
const btnDeleteMailbox    = $('btnDeleteMailbox');

const statusText    = $('statusText');
const totalCount    = $('totalCount');
const batchCount    = $('batchCount');
const progressBar   = $('progressBar');
const progressPct   = $('progressPct');
const outputLog     = $('outputLog');

/* ─── Tab switching ───────────────────────────────────────────────────────── */
document.querySelectorAll('.nav-tab-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const target = link.dataset.tab;
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.add('d-none'));
    document.querySelectorAll('.nav-tab-link').forEach(l => l.classList.remove('active'));
    $(target).classList.remove('d-none');
    link.classList.add('active');
  });
});

/* ─── Password visibility toggle ─────────────────────────────────────────── */
document.querySelectorAll('.toggle-pass').forEach(btn => {
  btn.addEventListener('click', () => {
    const input = $(btn.dataset.target);
    const icon  = btn.querySelector('i');
    if (input.type === 'password') {
      input.type = 'text';
      icon.className = 'bi bi-eye-slash';
    } else {
      input.type = 'password';
      icon.className = 'bi bi-eye';
    }
  });
});

/* ─── Auto-fill port on encryption change ─────────────────────────────────── */
function defaultPort(enc) { return enc === 'SSL/TLS' ? '993' : '143'; }

srcEncryption.addEventListener('change', () => { srcPort.value = defaultPort(srcEncryption.value); });
dstEncryption.addEventListener('change', () => { dstPort.value = defaultPort(dstEncryption.value); });

/* ─── Config helpers ──────────────────────────────────────────────────────── */
function getSourceConfig() {
  return {
    host: srcHost.value.trim(),
    port: srcPort.value.trim(),
    encryption: srcEncryption.value,
    user: srcUser.value.trim(),
    password: srcPass.value,
    mailbox: srcMailbox.value,
  };
}

function getDestConfig() {
  return {
    host: dstHost.value.trim(),
    port: dstPort.value.trim(),
    encryption: dstEncryption.value,
    user: dstUser.value.trim(),
    password: dstPass.value,
    mailbox: dstMailbox.value,
  };
}

/* ─── UI helpers ──────────────────────────────────────────────────────────── */
function setStatus(msg) { statusText.textContent = msg; }

function appendLog(msg, type = 'info') {
  const ts     = new Date().toLocaleTimeString();
  const prefix = type === 'error' ? '[ERR]' : '[LOG]';
  outputLog.value += `[${ts}] ${prefix} ${msg}\n`;
  outputLog.scrollTop = outputLog.scrollHeight;
}

function clearLog() {
  outputLog.value = '';
  setProgress(0, 0);
  batchCount.textContent = '-';
  totalCount.textContent = '0';
  $('copiedCount').textContent = '0';
  $('skippedCount').textContent = '0';
  srcIndicator.className = 'status-dot ms-auto';
  dstIndicator.className = 'status-dot ms-auto';
}

function setProgress(current, total) {
  const pct = total > 0 ? Math.round(current / total * 100) : 0;
  progressBar.style.width = pct + '%';
  progressPct.textContent = pct > 8 ? pct + '%' : '';
  progressBar.setAttribute('aria-valuenow', pct);
}

function setIndicator(el, state) {
  el.className = 'status-dot ms-auto' + (state === true ? ' ok' : state === false ? ' fail' : '');
}

function populateMailboxSelect(selectEl, mailboxes) {
  const current = selectEl.value;
  selectEl.innerHTML = '';
  // Always add "All Mailboxes" as first option
  const allOpt = document.createElement('option');
  allOpt.value = '__ALL__';
  allOpt.textContent = '-- All Mailboxes --';
  selectEl.appendChild(allOpt);
  mailboxes.forEach(mb => {
    const opt = document.createElement('option');
    opt.value = mb;
    opt.textContent = mb;
    selectEl.appendChild(opt);
  });
  if (current && (current === '__ALL__' || mailboxes.includes(current))) {
    selectEl.value = current;
  }
}

/* ─── Test Connections ────────────────────────────────────────────────────── */
btnTestConnections.addEventListener('click', async () => {
  btnTestConnections.disabled = true;
  setStatus('Testing connections...');
  appendLog('Testing source and destination connections...');

  try {
    const res  = await fetch('/api/test-connections', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: getSourceConfig(), destination: getDestConfig() }),
    });
    const data = await res.json();

    setIndicator(srcIndicator, data.source.success);
    setIndicator(dstIndicator, data.destination.success);

    appendLog(data.source.success
      ? `Source OK: ${srcHost.value}`
      : `Source FAILED: ${data.source.error}`
    , data.source.success ? 'info' : 'error');

    appendLog(data.destination.success
      ? `Destination OK: ${dstHost.value}`
      : `Destination FAILED: ${data.destination.error}`
    , data.destination.success ? 'info' : 'error');

    setStatus(data.source.success && data.destination.success ? 'Both connections OK' : 'Connection test failed');
  } catch (err) {
    appendLog(`Request error: ${err.message}`, 'error');
    setStatus('Error');
  } finally {
    btnTestConnections.disabled = false;
  }
});

/* ─── Refresh Mailboxes ───────────────────────────────────────────────────── */
async function refreshMailboxesSide(side) {
  const config = side === 'src' ? getSourceConfig() : getDestConfig();
  const label  = side === 'src' ? 'source' : 'destination';
  try {
    const res  = await fetch('/api/list-mailboxes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    const data = await res.json();
    if (data.error) {
      appendLog(`Failed to list ${label} mailboxes: ${data.error}`, 'error');
      return;
    }
    populateMailboxSelect(side === 'src' ? srcMailbox : dstMailbox, data.mailboxes);
    appendLog(`${label} mailboxes loaded (${data.mailboxes.length})`);
  } catch (err) {
    appendLog(`Error fetching ${label} mailboxes: ${err.message}`, 'error');
  }
}

btnRefreshMailboxes.addEventListener('click', async () => {
  btnRefreshMailboxes.disabled = true;
  setStatus('Loading mailboxes...');
  await Promise.all([refreshMailboxesSide('src'), refreshMailboxesSide('dst')]);
  setStatus('Mailboxes loaded');
  btnRefreshMailboxes.disabled = false;
});

// Individual refresh buttons next to each Mailbox select
document.querySelectorAll('.refresh-mailbox-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const side = btn.dataset.side;
    const icon = btn.querySelector('i');
    icon.className = 'bi bi-arrow-clockwise spin';
    btn.disabled = true;
    await refreshMailboxesSide(side);
    icon.className = 'bi bi-arrow-clockwise';
    btn.disabled = false;
  });
});

// Auto-load mailboxes when password field loses focus
srcPass.addEventListener('blur', async () => {
  if (srcHost.value && srcUser.value && srcPass.value) {
    await refreshMailboxesSide('src');
  }
});
dstPass.addEventListener('blur', async () => {
  if (dstHost.value && dstUser.value && dstPass.value) {
    await refreshMailboxesSide('dst');
  }
});

/* ─── Email Sync (SSE) ────────────────────────────────────────────────────── */
let currentEventSource = null;

btnStartSync.addEventListener('click', () => {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
    btnStartSync.textContent = 'Email sync (Batch mode)';
    btnStartSync.classList.remove('syncing');
    btnStartSync.innerHTML = '<i class="bi bi-stars me-1"></i> Email sync (Batch mode)';
    setStatus('Sync stopped by user');
    appendLog('Sync stopped by user.');
    return;
  }

  clearLog();
  setStatus('Starting sync...');

  const src = getSourceConfig();
  const dst = getDestConfig();

  const useAllMailboxes = optAllMailboxes.checked || src.mailbox === '__ALL__';

  const params = new URLSearchParams({
    srcHost: src.host, srcPort: src.port, srcEncryption: src.encryption,
    srcUser: src.user, srcPass: src.password, srcMailbox: src.mailbox,
    dstHost: dst.host, dstPort: dst.port, dstEncryption: dst.encryption,
    dstUser: dst.user, dstPass: dst.password, dstMailbox: dst.mailbox,
    allMailboxes: useAllMailboxes,
    batchSize: optBatchSize.value,
  });

  currentEventSource = new EventSource(`/api/sync?${params}`);

  btnStartSync.innerHTML = '<i class="bi bi-stop-circle me-1"></i> Stop sync';
  btnStartSync.classList.add('syncing');

  currentEventSource.addEventListener('status', e => {
    setStatus(JSON.parse(e.data).message);
  });

  currentEventSource.addEventListener('progress', e => {
    const d = JSON.parse(e.data);
    setProgress(d.current, d.total);
    totalCount.textContent = d.total;
    batchCount.textContent = d.totalBatches > 0 ? `${d.batch} / ${d.totalBatches}` : '-';
    $('copiedCount').textContent = d.current - (d.skipped || 0);
    $('skippedCount').textContent = d.skipped || 0;
  });

  currentEventSource.addEventListener('log', e => {
    appendLog(JSON.parse(e.data).message);
  });

  currentEventSource.addEventListener('error', e => {
    try { appendLog(JSON.parse(e.data).message, 'error'); } catch (_) {}
  });

  currentEventSource.addEventListener('complete', e => {
    const d = JSON.parse(e.data);
    setStatus(`Complete — ${d.totalSynced} copied, ${d.skipped || 0} skipped, ${d.errors} error(s)`);
    appendLog(`Sync complete. Copied: ${d.totalSynced}, Skipped (duplicates): ${d.skipped || 0}, Errors: ${d.errors}`);
    $('copiedCount').textContent = d.totalSynced;
    $('skippedCount').textContent = d.skipped || 0;
    stopSync();
  });

  currentEventSource.addEventListener('fatal', e => {
    const d = JSON.parse(e.data);
    appendLog(`FATAL: ${d.message}`, 'error');
    setStatus('Sync failed');
    stopSync();
  });

  currentEventSource.onerror = () => {
    if (currentEventSource && currentEventSource.readyState === EventSource.CLOSED) {
      setStatus('Connection closed');
      stopSync();
    }
  };
});

function stopSync() {
  if (currentEventSource) { currentEventSource.close(); currentEventSource = null; }
  btnStartSync.innerHTML = '<i class="bi bi-stars me-1"></i> Email sync (Batch mode)';
  btnStartSync.classList.remove('syncing');
}

/* ─── Delete Mailbox ──────────────────────────────────────────────────────── */
btnDeleteMailbox.addEventListener('click', async () => {
  const dst = getDestConfig();
  const mb  = dst.mailbox;

  if (!confirm(`Delete ALL emails in "${mb}" on ${dst.host}?\n\nThis action cannot be undone.`)) return;

  btnDeleteMailbox.disabled = true;
  setStatus(`Deleting emails in ${mb}...`);

  try {
    const res  = await fetch('/api/delete-mailbox', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...dst }),
    });
    const data = await res.json();
    if (data.error) {
      appendLog(`Delete failed: ${data.error}`, 'error');
      setStatus('Delete failed');
    } else {
      appendLog(`Deleted ${data.deleted} email(s) from ${mb}`);
      setStatus(`Deleted ${data.deleted} email(s)`);
    }
  } catch (err) {
    appendLog(`Delete request error: ${err.message}`, 'error');
    setStatus('Error');
  } finally {
    btnDeleteMailbox.disabled = false;
  }
});
