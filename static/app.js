/* guywatchingscreen — SPA frontend (vanilla JS, no dependencies) */

let EXPECTED_STATUS = 200;
let CHECKS = [];
let CURRENT_TAB = 'monitors';
let REFRESH_TIMER = null;
const REFRESH_MS = 30000;

const ICONS = {
  play: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  edit: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
  x: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
};

// --- theme ---

function toggleTheme() {
  const root = document.documentElement;
  const current = root.dataset.theme
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = current === 'dark' ? 'light' : 'dark';
  root.dataset.theme = next;
  try { localStorage.setItem('gws-theme', next); } catch (e) { /* ignore */ }
}

// --- tab switching ---

function showTab(name) {
  CURRENT_TAB = name;
  document.querySelectorAll('.tab').forEach(el => el.hidden = true);
  document.getElementById(`tab-${name}`).hidden = false;
  document.querySelectorAll('.tab-link').forEach(a => {
    a.classList.toggle('active', a.dataset.tab === name);
  });
  if (name === 'monitors') loadDashboard();
  if (name === 'alerts') loadAlertSettings();
  if (name === 'comms') loadCommunication();
}

document.querySelectorAll('[data-tab]').forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    const tab = a.dataset.tab;
    history.replaceState({}, '', `#${tab}`);
    showTab(tab);
  });
});

// --- flash messages ---

function flash(message, type) {
  const area = document.getElementById('flash-area');
  const div = document.createElement('div');
  div.className = `flash flash-${type || 'success'}`;
  div.innerHTML = `<span class="dot"></span><span>${esc(message)}</span>`;
  area.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

// --- modal ---

function modalShell(title, body, foot) {
  return `<div class="modal-head">
      <h2>${esc(title)}</h2>
      <button type="button" class="icon-btn" onclick="closeModal()" aria-label="Close">${ICONS.x}</button>
    </div>
    <div class="modal-body">${body}</div>
    ${foot ? `<div class="modal-foot">${foot}</div>` : ''}`;
}

function openModal(html) {
  const overlay = document.getElementById('modal-overlay');
  document.getElementById('modal-content').innerHTML = html;
  overlay.hidden = false;
  overlay.classList.add('open');
  const first = overlay.querySelector('input:not([type=checkbox]), textarea');
  if (first) first.focus();
}

function closeModal() {
  const overlay = document.getElementById('modal-overlay');
  overlay.classList.remove('open');
  overlay.hidden = true;
  document.getElementById('modal-content').innerHTML = '';
  if (PENDING_CONFIRM) { PENDING_CONFIRM(false); PENDING_CONFIRM = null; }
}

document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !document.getElementById('modal-overlay').hidden) closeModal();
});

// --- confirm dialog (replaces browser confirm()) ---

let PENDING_CONFIRM = null;

function showConfirm(message, action) {
  return new Promise(resolve => {
    openModal(modalShell('Are you sure?', `<p class="subtle">${esc(message)}</p>`,
      `<button type="button" id="confirm-no">Cancel</button>
       <button type="button" id="confirm-yes" class="btn-danger-solid">${esc(action || 'Delete')}</button>`));
    PENDING_CONFIRM = resolve;
    const done = value => { PENDING_CONFIRM = null; closeModal(); resolve(value); };
    document.getElementById('confirm-no').onclick = () => done(false);
    document.getElementById('confirm-yes').onclick = () => done(true);
    document.getElementById('confirm-yes').focus();
  });
}

// --- api helper ---

async function api(url, opts) {
  const res = await fetch(url, opts);
  const contentType = res.headers.get('content-type') || '';
  const isJson = contentType.includes('application/json');
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    const message = data && data.error ? data.error : `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

function jsonOpts(method, body) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

// --- formatting ---

function esc(str) {
  if (str == null) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

// Server timestamps are naive UTC ISO strings.
function parseTs(ts) {
  if (!ts) return null;
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(ts) ? ts : `${ts}Z`);
  return isNaN(d) ? null : d;
}

function timeAgo(ts) {
  const d = parseTs(ts);
  if (!d) return 'never';
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 10) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function fullTime(ts) {
  const d = parseTs(ts);
  return d ? d.toLocaleString() : '';
}

function hostOf(url) {
  try { const u = new URL(url); return u.host + (u.pathname !== '/' ? u.pathname : ''); }
  catch (e) { return url; }
}

// ========================
// MONITORS TAB
// ========================

function scheduleRefresh() {
  clearTimeout(REFRESH_TIMER);
  REFRESH_TIMER = setTimeout(() => {
    if (CURRENT_TAB === 'monitors' && document.visibilityState === 'visible'
        && document.getElementById('modal-overlay').hidden) {
      loadDashboard();
    } else {
      scheduleRefresh();
    }
  }, REFRESH_MS);
}

async function loadDashboard() {
  try {
    const data = await api('/api/dashboard');
    EXPECTED_STATUS = data.expected_status;
    CHECKS = data.checks;
    renderStats(CHECKS);
    renderChecks();
    renderRecentAlerts(data.alerts);
    document.getElementById('refresh-note').textContent =
      `Updated ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} · refreshes every 30s`;
  } catch (err) {
    flash(err.message, 'error');
  } finally {
    scheduleRefresh();
  }
}

function checkStatus(c) {
  if (!c.last_checked_at) return { label: 'Pending', key: 'idle' };
  if (c.last_status_code === EXPECTED_STATUS && !c.last_error) return { label: 'Up', key: 'up' };
  if (c.alert_rule_count === 0 || c.alert_active) return { label: 'Down', key: 'down' };
  return { label: 'Degraded', key: 'warn' };
}

function renderStats(checks) {
  const counts = { up: 0, down: 0, warn: 0, idle: 0 };
  let latencySum = 0, latencyN = 0;
  checks.forEach(c => {
    counts[checkStatus(c).key]++;
    if (c.last_response_time_ms != null) { latencySum += c.last_response_time_ms; latencyN++; }
  });
  const avg = latencyN ? Math.round(latencySum / latencyN) : null;
  document.getElementById('stats-area').innerHTML = `
    <div class="stat"><div class="stat-label"><span class="dot dot-up"></span>Up</div>
      <div class="stat-value">${counts.up}<small>/ ${checks.length}</small></div></div>
    <div class="stat ${counts.down ? 'stat-down' : ''}"><div class="stat-label"><span class="dot dot-down"></span>Down</div>
      <div class="stat-value">${counts.down}</div></div>
    <div class="stat"><div class="stat-label"><span class="dot dot-warn"></span>Degraded</div>
      <div class="stat-value">${counts.warn}</div></div>
    <div class="stat"><div class="stat-label"><span class="dot"></span>Avg response</div>
      <div class="stat-value">${avg == null ? '—' : `${avg}<small>ms</small>`}</div></div>`;
}

function renderChecks() {
  const area = document.getElementById('checks-area');
  if (!CHECKS.length) {
    area.innerHTML = `<div class="empty-state">
      <h3>Nothing on screen yet</h3>
      <p>Add a website and the guy will start watching it.</p>
      <button class="btn btn-primary" onclick="showCheckForm()">Add monitor</button>
    </div>`;
    return;
  }

  const q = (document.getElementById('check-filter').value || '').trim().toLowerCase();
  const order = { down: 0, warn: 1, idle: 2, up: 3 };
  const list = CHECKS
    .filter(c => !q || c.name.toLowerCase().includes(q) || c.url.toLowerCase().includes(q))
    .map(c => ({ c, s: checkStatus(c) }))
    .sort((a, b) => order[a.s.key] - order[b.s.key]);

  if (!list.length) { area.innerHTML = '<p class="empty">No monitors match that filter.</p>'; return; }

  area.innerHTML = '<div class="monitor-list">' + list.map(({ c, s }) => {
    const total = c.success_count + c.failure_count;
    const pct = total ? (c.success_count / total) * 100 : null;
    const pctLabel = pct == null ? '—' : `${pct >= 99.95 ? '100' : pct.toFixed(1)}%`;
    const code = c.last_error ? 'error' : (c.last_status_code || '');
    const latency = c.last_response_time_ms != null ? `${c.last_response_time_ms} ms` : '—';
    return `<article class="monitor is-${s.key}">
      <div class="monitor-id">
        <div class="monitor-name"><span title="${esc(c.name)}">${esc(c.name)}</span>
          <span class="badge badge-${s.key}">${s.label}${code ? ` · ${esc(code)}` : ''}</span></div>
        <a class="monitor-url" href="${esc(c.url)}" target="_blank" rel="noreferrer" title="${esc(c.url)}">${esc(hostOf(c.url))}</a>
      </div>
      <div class="monitor-uptime">
        <div class="uptime-head"><span>Last hour</span><strong>${pctLabel}</strong></div>
        <div class="bar" title="${c.success_count} ok · ${c.failure_count} failed">
          ${total ? `<span class="bar-up" style="width:${pct}%"></span><span class="bar-down" style="width:${100 - pct}%"></span>` : ''}
        </div>
      </div>
      <div class="monitor-latency">
        <div class="metric-label">Response</div>
        <div class="metric-value">${latency}</div>
      </div>
      <div class="monitor-last">
        <div class="metric-label">Every ${c.frequency_minutes}m</div>
        <div class="metric-value" title="${esc(fullTime(c.last_checked_at))}">${timeAgo(c.last_checked_at)}</div>
      </div>
      <div class="monitor-actions">
        <button class="icon-btn" onclick="runCheck(${c.id}, this)" title="Run now" aria-label="Run ${esc(c.name)} now">${ICONS.play}</button>
        <button class="icon-btn" onclick="showCheckForm(${c.id})" title="Edit" aria-label="Edit ${esc(c.name)}">${ICONS.edit}</button>
      </div>
    </article>`;
  }).join('') + '</div>';
}

function renderRecentAlerts(alerts) {
  const area = document.getElementById('recent-alerts-area');
  const btn = document.getElementById('clear-alerts-btn');
  if (!alerts.length) { area.innerHTML = '<p class="empty">All quiet. No alerts yet.</p>'; btn.hidden = true; return; }
  btn.hidden = false;

  area.innerHTML = '<ul class="feed-list">' + alerts.map(a => {
    const isRecovery = /recover|up/i.test(a.alert_type || '');
    return `<li class="feed-item">
      <span class="dot ${isRecovery ? 'dot-up' : 'dot-down'}"></span>
      <button type="button" class="feed-body" onclick="showAlertDetail(${a.id})">
        <div class="feed-title">${esc(a.check_name)}<span class="feed-time" title="${esc(fullTime(a.created_at))}">${timeAgo(a.created_at)}</span></div>
        <div class="feed-msg">${esc(a.message)}</div>
      </button>
      <button class="icon-btn" onclick="deleteAlert(${a.id})" title="Delete" aria-label="Delete alert">${ICONS.x}</button>
    </li>`;
  }).join('') + '</ul>';
}

async function runCheck(id, btn) {
  if (btn) { btn.classList.add('spin'); btn.disabled = true; }
  try {
    await api(`/api/checks/${id}/run`, { method: 'POST' });
    await loadDashboard();
  } catch (err) {
    flash(err.message, 'error');
  } finally {
    if (btn) { btn.classList.remove('spin'); btn.disabled = false; }
  }
}

async function clearAlerts() {
  if (!await showConfirm('This removes every alert from the recent feed.', 'Clear all')) return;
  try {
    await api('/api/alerts/clear', { method: 'POST' });
    flash('Alerts cleared.');
    loadDashboard();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteAlert(id) {
  if (!await showConfirm('Delete this alert from the feed?')) return;
  try {
    await api(`/api/alerts/${id}`, { method: 'DELETE' });
    flash('Alert deleted.');
    loadDashboard();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function showAlertDetail(id) {
  try {
    const a = await api(`/api/alerts/${id}`);
    openModal(modalShell(a.check_name || 'Alert', `
      <dl class="detail-grid">
        <dt>URL</dt><dd><a href="${esc(a.check_url)}" target="_blank" rel="noreferrer">${esc(a.check_url)}</a></dd>
        <dt>Time</dt><dd>${esc(fullTime(a.created_at))} <span class="subtle">(${timeAgo(a.created_at)})</span></dd>
        <dt>Type</dt><dd>${esc(a.alert_type)}</dd>
        ${a.alert_rule_name ? `<dt>Rule</dt><dd>${esc(a.alert_rule_name)}</dd>` : ''}
      </dl>
      <pre class="detail">${esc(a.detail || a.message)}</pre>`,
      '<button type="button" onclick="closeModal()">Close</button>'));
  } catch (err) {
    flash(err.message, 'error');
  }
}

// --- check form ---

async function showCheckForm(id) {
  try {
    let check = null;
    if (id) check = await api(`/api/checks/${id}`);
    const c = check || {};

    openModal(modalShell(check ? 'Edit monitor' : 'Add monitor', `
      <form id="check-form" class="stack" onsubmit="saveCheck(event, ${id || 'null'})">
        <label class="field"><span>Name</span>
          <input name="name" type="text" value="${esc(c.name || '')}" placeholder="Marketing site" required>
        </label>
        <label class="field"><span>URL</span>
          <input name="url" type="url" value="${esc(c.url || '')}" placeholder="https://example.com/health" required>
        </label>
        <div class="row-2">
          <label class="field"><span>Check every (minutes)</span>
            <input name="frequency_minutes" type="number" min="1" value="${c.frequency_minutes || 5}" required>
          </label>
          <label class="field"><span>Timeout (seconds)</span>
            <input name="timeout_seconds" type="number" min="1" value="${c.timeout_seconds || 10}" required>
          </label>
        </div>
        <label class="field"><span>Blackout periods</span>
          <textarea name="blackout_periods" rows="3" placeholder="23:00-06:00&#10;12:30-13:00">${esc(c.blackout_periods || '')}</textarea>
          <span class="hint">One range per line, HH:MM-HH:MM, server local time. No checks run during these.</span>
        </label>
      </form>`,
      `${check ? `<button type="button" class="btn-danger spacer" onclick="deleteCheck(${id})">${ICONS.trash} Delete</button>` : ''}
       <button type="button" onclick="closeModal()">Cancel</button>
       <button type="submit" form="check-form" class="btn-primary">${check ? 'Save changes' : 'Add monitor'}</button>`));
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function saveCheck(e, id) {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    name: f.get('name'),
    url: f.get('url'),
    frequency_minutes: parseInt(f.get('frequency_minutes')),
    timeout_seconds: parseInt(f.get('timeout_seconds')),
    blackout_periods: f.get('blackout_periods'),
  };
  try {
    if (id) {
      await api(`/api/checks/${id}`, jsonOpts('PUT', body));
      flash('Monitor updated.');
    } else {
      await api('/api/checks', jsonOpts('POST', body));
      flash('Monitor added.');
    }
    closeModal();
    loadDashboard();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteCheck(id) {
  if (!await showConfirm('This monitor and its history will be removed.')) return;
  try {
    await api(`/api/checks/${id}`, { method: 'DELETE' });
    flash('Monitor deleted.');
    loadDashboard();
  } catch (err) {
    flash(err.message, 'error');
  }
}


// ========================
// ALERTS TAB
// ========================

async function loadAlertSettings() {
  try {
    const data = await api('/api/alerts');
    renderAlertSettings(data.alert_settings);
  } catch (err) {
    flash(err.message, 'error');
  }
}

function chips(csv, emptyText) {
  if (!csv) return `<span class="chip-none">${emptyText}</span>`;
  return '<div class="chips">' + csv.split(',').map(n => `<span class="chip" title="${esc(n)}">${esc(n)}</span>`).join('') + '</div>';
}

function renderAlertSettings(settings) {
  const area = document.getElementById('alert-settings-area');
  if (!settings.length) {
    area.innerHTML = `<div class="empty-state">
      <h3>No alert rules</h3>
      <p>Monitors still show status, but nobody gets told when something breaks.</p>
      <button class="btn btn-primary" onclick="showAlertRuleForm()">Add rule</button>
    </div>`;
    return;
  }

  area.innerHTML = '<div class="rule-list">' + settings.map(s => `<article class="card rule">
    <div class="rule-head">
      <div>
        <h3>${esc(s.name)}</h3>
        <div class="rule-cond"><strong>${s.alert_failures}</strong> failures within <strong>${s.alert_window_minutes}</strong> min</div>
      </div>
      <div class="monitor-actions">
        <button class="icon-btn" onclick="showAlertRuleForm(${s.id})" title="Edit" aria-label="Edit rule">${ICONS.edit}</button>
        <button class="icon-btn" onclick="deleteAlertRule(${s.id})" title="Delete" aria-label="Delete rule">${ICONS.trash}</button>
      </div>
    </div>
    <div class="rule-meta">
      <div><div class="rule-meta-label">Monitors · ${s.monitor_count}</div>${chips(s.monitor_names, 'No monitors')}</div>
      <div><div class="rule-meta-label">Notifies · ${s.communication_count}</div>${chips(s.communication_names, 'Nobody (dashboard only)')}</div>
    </div>
  </article>`).join('') + '</div>';
}

async function showAlertRuleForm(id) {
  try {
    const [dashData, commsData] = await Promise.all([
      api('/api/dashboard'),
      api('/api/communication'),
    ]);
    const checks = dashData.checks;
    const recipients = commsData.email_recipients;

    let rule = null;
    let selectedCheckIds = [];
    let selectedRecipientIds = [];
    if (id) {
      rule = await api(`/api/alert-rules/${id}`);
      selectedCheckIds = rule.check_ids || [];
      selectedRecipientIds = rule.recipient_ids || [];
    }
    const r = rule || {};

    if (!checks.length) {
      openModal(modalShell('Add rule', '<p class="subtle">Add a monitor first, then attach alert rules to it.</p>',
        '<button type="button" onclick="closeModal()">Close</button>'));
      return;
    }

    const checkBoxes = checks.map(c =>
      `<label class="checkbox-row"><input type="checkbox" name="check_ids" value="${c.id}" ${selectedCheckIds.includes(c.id) ? 'checked' : ''}><span>${esc(c.name)}</span></label>`
    ).join('');

    const recipientBoxes = recipients.length
      ? `<div class="checkbox-list">${recipients.map(rec =>
          `<label class="checkbox-row"><input type="checkbox" name="recipient_ids" value="${rec.id}" ${selectedRecipientIds.includes(rec.id) ? 'checked' : ''}><span>${esc(rec.email)}</span></label>`
        ).join('')}</div>`
      : '<p class="hint">No recipients yet. Add one on the Comms tab.</p>';

    openModal(modalShell(rule ? 'Edit rule' : 'Add rule', `
      <form id="alert-rule-form" class="stack" onsubmit="saveAlertRule(event, ${id || 'null'})">
        <label class="field"><span>Name</span>
          <input name="name" type="text" maxlength="40" value="${esc(r.name || '')}" placeholder="Prod is down" required>
        </label>
        <div class="row-2">
          <label class="field"><span>Failures</span>
            <input name="alert_failures" type="number" min="1" value="${r.alert_failures || 3}" required>
          </label>
          <label class="field"><span>Within (minutes)</span>
            <input name="alert_window_minutes" type="number" min="1" value="${r.alert_window_minutes || 15}" required>
          </label>
        </div>
        <fieldset><legend>Monitors</legend><div class="checkbox-list">${checkBoxes}</div></fieldset>
        <fieldset><legend>Notify</legend>${recipientBoxes}</fieldset>
      </form>`,
      `<button type="button" onclick="closeModal()">Cancel</button>
       <button type="submit" form="alert-rule-form" class="btn-primary">${rule ? 'Save changes' : 'Add rule'}</button>`));
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function saveAlertRule(e, id) {
  e.preventDefault();
  const f = e.target;
  const checkIds = [...f.querySelectorAll('input[name="check_ids"]:checked')].map(i => parseInt(i.value));
  const recipientIds = [...f.querySelectorAll('input[name="recipient_ids"]:checked')].map(i => parseInt(i.value));
  const body = {
    name: f.elements.name.value,
    alert_failures: parseInt(f.elements.alert_failures.value),
    alert_window_minutes: parseInt(f.elements.alert_window_minutes.value),
    check_ids: checkIds,
    recipient_ids: recipientIds,
  };
  try {
    if (id) {
      await api(`/api/alert-rules/${id}`, jsonOpts('PUT', body));
      flash('Rule updated.');
    } else {
      await api('/api/alert-rules', jsonOpts('POST', body));
      flash('Rule added.');
    }
    closeModal();
    loadAlertSettings();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteAlertRule(id) {
  if (!await showConfirm('This alert rule will stop firing.')) return;
  try {
    await api(`/api/alert-rules/${id}`, { method: 'DELETE' });
    flash('Rule deleted.');
    loadAlertSettings();
  } catch (err) {
    flash(err.message, 'error');
  }
}


// ========================
// COMMS TAB
// ========================

async function loadCommunication() {
  try {
    const data = await api('/api/communication');
    renderEmailSetup(data.email_settings);
    renderEmailRecipients(data.email_recipients);
  } catch (err) {
    flash(err.message, 'error');
  }
}

function renderEmailSetup(settings) {
  const area = document.getElementById('email-setup-area');
  if (!settings.sender_email && !settings.smtp_host) {
    area.innerHTML = `<p class="empty">Email isn't configured. Set <span class="mono">SENDER_EMAIL</span> and
      <span class="mono">SMTP_*</span> in the environment to send alerts.</p>`;
    return;
  }
  area.innerHTML = `<dl class="kv">
    <div class="kv-row"><dt>From</dt><dd>${esc(settings.sender_email || 'Not set')}</dd></div>
    <div class="kv-row"><dt>SMTP host</dt><dd>${esc(settings.smtp_host || 'Not set')}</dd></div>
    <div class="kv-row"><dt>Port</dt><dd>${esc(settings.smtp_port)}</dd></div>
    <div class="kv-row"><dt>TLS</dt><dd><span class="badge ${settings.use_tls ? 'badge-up' : ''}">${settings.use_tls ? 'On' : 'Off'}</span></dd></div>
  </dl>`;
}

function renderEmailRecipients(recipients) {
  const area = document.getElementById('email-recipients-area');
  if (!recipients.length) { area.innerHTML = '<p class="empty">No recipients yet.</p>'; return; }

  area.innerHTML = '<ul class="recipient-list">' + recipients.map(r => `<li class="recipient">
    <span class="avatar" aria-hidden="true">${esc((r.email || '?')[0])}</span>
    <span class="recipient-email">${esc(r.email)}</span>
    <div class="monitor-actions">
      <button class="icon-btn" onclick="showEmailForm(${r.id})" title="Edit" aria-label="Edit ${esc(r.email)}">${ICONS.edit}</button>
      <button class="icon-btn" onclick="deleteEmail(${r.id})" title="Delete" aria-label="Delete ${esc(r.email)}">${ICONS.trash}</button>
    </div>
  </li>`).join('') + '</ul>';
}

async function showEmailForm(id) {
  try {
    let recipient = null;
    if (id) {
      const data = await api('/api/communication');
      recipient = data.email_recipients.find(r => r.id === id);
    }

    openModal(modalShell(recipient ? 'Edit recipient' : 'Add recipient', `
      <form id="email-form" class="stack" onsubmit="saveEmail(event, ${id || 'null'})">
        <label class="field"><span>Email</span>
          <input name="email" type="email" value="${esc(recipient ? recipient.email : '')}" placeholder="oncall@example.com" required>
        </label>
      </form>`,
      `<button type="button" onclick="closeModal()">Cancel</button>
       <button type="submit" form="email-form" class="btn-primary">${recipient ? 'Save changes' : 'Add recipient'}</button>`));
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function saveEmail(e, id) {
  e.preventDefault();
  const email = new FormData(e.target).get('email');
  try {
    if (id) {
      await api(`/api/communication/emails/${id}`, jsonOpts('PUT', { email }));
      flash('Recipient updated.');
    } else {
      await api('/api/communication/emails', jsonOpts('POST', { email }));
      flash('Recipient added.');
    }
    closeModal();
    loadCommunication();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteEmail(id) {
  if (!await showConfirm('This address will stop receiving alerts.')) return;
  try {
    await api(`/api/communication/emails/${id}`, { method: 'DELETE' });
    flash('Recipient deleted.');
    loadCommunication();
  } catch (err) {
    flash(err.message, 'error');
  }
}


// ========================
// INIT
// ========================

function showTabFromHash() {
  const hash = location.hash.replace('#', '') || 'monitors';
  const valid = ['monitors', 'alerts', 'comms'];
  if (!document.getElementById('modal-overlay').hidden) closeModal();
  showTab(valid.includes(hash) ? hash : 'monitors');
}

window.addEventListener('hashchange', showTabFromHash);
showTabFromHash();
