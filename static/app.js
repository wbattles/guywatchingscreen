/* =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
   guywatchingscreen :: SPA frontend
   vanilla JS, no dependencies
   =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-= */

let EXPECTED_STATUS = 200;

// --- tab switching ---

function showTab(name) {
  document.querySelectorAll('.tab').forEach(el => el.hidden = true);
  document.getElementById(`tab-${name}`).hidden = false;
  document.querySelectorAll('.nav-link').forEach(a => {
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
  div.textContent = `>> ${message}`;
  area.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

// --- modal ---

function openModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal-overlay').classList.add('open');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.getElementById('modal-content').innerHTML = '';
}

document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

// --- confirm dialog (replaces browser confirm()) ---

function showConfirm(message) {
  return new Promise(resolve => {
    openModal(`<section class="panel form-panel confirm-panel">
      <h2>Confirm</h2>
      <p>${esc(message)}</p>
      <div class="form-actions">
        <button id="confirm-no">Cancel</button>
        <button id="confirm-yes" class="btn-danger">Yes</button>
      </div>
    </section>`);
    document.getElementById('confirm-no').onclick = () => { closeModal(); resolve(false); };
    document.getElementById('confirm-yes').onclick = () => { closeModal(); resolve(true); };
  });
}

// --- api helper ---

async function api(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

// --- escape html ---

function esc(str) {
  if (str == null) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

// ========================
// MONITORS TAB
// ========================

async function loadDashboard() {
  const data = await api('/api/dashboard');
  EXPECTED_STATUS = data.expected_status;
  renderChecks(data.checks);
  renderRecentAlerts(data.alerts);
}

function checkStatus(c) {
  if (!c.last_checked_at) return { label: 'new', cls: '' };
  if (c.last_status_code === EXPECTED_STATUS && !c.last_error) return { label: 'up', cls: 'pill-up' };
  if (c.alert_rule_count === 0 || c.alert_active) return { label: 'down', cls: 'pill-down' };
  return { label: 'issue', cls: 'pill-warn' };
}

function renderChecks(checks) {
  const area = document.getElementById('checks-area');
  if (!checks.length) { area.innerHTML = '<p>No checks yet. Add your first website.</p>'; return; }

  const rows = checks.map(c => {
    const s = checkStatus(c);
    const lastCheck = c.last_checked_at ? esc(c.last_checked_at.replace('T', ' ')) : 'Never';
    const meta = c.last_status_code ? `<div class="meta">${esc(c.last_status_code)}</div>`
               : c.last_error ? '<div class="meta">error</div>' : '';
    return `<tr>
      <td><div class="cell-clamp-2">${esc(c.name)}</div></td>
      <td><div class="cell-scroll"><a href="${esc(c.url)}" target="_blank" rel="noreferrer">${esc(c.url)}</a></div></td>
      <td><span class="pill ${s.cls}">${s.label}</span>${meta}</td>
      <td>${c.success_count}</td>
      <td>${c.failure_count}</td>
      <td>${c.frequency_minutes} min</td>
      <td>${lastCheck}</td>
      <td><div class="actions">
        <button onclick="showCheckForm(${c.id})">Edit</button>
        <button onclick="runCheck(${c.id})">Run now</button>
      </div></td>
    </tr>`;
  }).join('');

  area.innerHTML = `<div class="table-wrap checks-table-wrap"><table>
    <colgroup>
      <col class="checks-col-name"><col class="checks-col-url"><col class="checks-col-status">
      <col class="checks-col-successes"><col class="checks-col-failures"><col class="checks-col-frequency">
      <col class="checks-col-last-check"><col class="checks-col-actions">
    </colgroup>
    <thead><tr>
      <th>Name</th><th>URL</th><th>Status</th><th>Success</th><th>Failure</th>
      <th>Frequency</th><th>Last check</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderRecentAlerts(alerts) {
  const area = document.getElementById('recent-alerts-area');
  const btn = document.getElementById('clear-alerts-btn');
  if (!alerts.length) { area.innerHTML = '<p>No alerts yet.</p>'; btn.style.display = 'none'; return; }
  btn.style.display = '';

  area.innerHTML = '<ul class="alerts">' + alerts.map(a => `<li class="alert-row">
    <a class="alert-link" href="#" onclick="showAlertDetail(${a.id}); return false;">
      <strong>${esc(a.check_name)}</strong>
      <span class="meta">${esc((a.created_at || '').replace('T', ' '))}</span>
      <div>${esc(a.message)}</div>
    </a>
    <button onclick="deleteAlert(${a.id})">Delete</button>
  </li>`).join('') + '</ul>';
}

async function runCheck(id) {
  await api(`/api/checks/${id}/run`, { method: 'POST' });
  flash('Check triggered.');
  loadDashboard();
}

async function clearAlerts() {
  if (!await showConfirm('Clear all recent alerts?')) return;
  await api('/api/alerts/clear', { method: 'POST' });
  flash('Alerts cleared.');
  loadDashboard();
}

async function deleteAlert(id) {
  if (!await showConfirm('Delete this alert?')) return;
  await api(`/api/alerts/${id}`, { method: 'DELETE' });
  flash('Alert deleted.');
  loadDashboard();
}

async function showAlertDetail(id) {
  const a = await api(`/api/alerts/${id}`);
  openModal(`<section class="panel form-panel">
    <h2>Alert</h2>
    <p><strong>Monitor:</strong> ${esc(a.check_name)}</p>
    <p><strong>URL:</strong> <a href="${esc(a.check_url)}" target="_blank" rel="noreferrer">${esc(a.check_url)}</a></p>
    <p><strong>Time:</strong> ${esc((a.created_at || '').replace('T', ' '))}</p>
    <p><strong>Type:</strong> ${esc(a.alert_type)}</p>
    ${a.alert_rule_name ? `<p><strong>Alert rule:</strong> ${esc(a.alert_rule_name)}</p>` : ''}
    <label><span>Details</span>
      <textarea rows="8" readonly>${esc(a.detail || a.message)}</textarea>
    </label>
    <div class="form-actions"><button onclick="closeModal()">Back</button></div>
  </section>`);
}

// --- check form ---

async function showCheckForm(id) {
  let check = null;
  if (id) check = await api(`/api/checks/${id}`);
  const c = check || {};

  openModal(`<section class="panel form-panel">
    <h2>${check ? 'Edit check' : 'Add check'}</h2>
    <form id="check-form" class="check-form" onsubmit="saveCheck(event, ${id || 'null'})">
      <label><span>Name</span>
        <input name="name" type="text" value="${esc(c.name || '')}" required>
      </label>
      <label><span>URL</span>
        <input name="url" type="url" value="${esc(c.url || '')}" required>
      </label>
      <div class="grid two-col">
        <label><span>Frequency (minutes)</span>
          <input name="frequency_minutes" type="number" min="1" value="${c.frequency_minutes || 5}" required>
        </label>
        <label><span>Timeout (seconds)</span>
          <input name="timeout_seconds" type="number" min="1" value="${c.timeout_seconds || 10}" required>
        </label>
      </div>
      <label><span>Blackout periods</span>
        <textarea name="blackout_periods" rows="4" placeholder="23:00-06:00&#10;12:30-13:00">${esc(c.blackout_periods || '')}</textarea>
      </label>
      <small>One range per line. Format: HH:MM-HH:MM</small>
      <div class="form-actions">
        <button type="button" onclick="closeModal()">Cancel</button>
        <button type="submit">Save</button>
      </div>
    </form>
    ${check ? `<div class="form-actions" style="margin-top:16px">
      <button onclick="deleteCheck(${id})">Delete website</button>
    </div>` : ''}
  </section>`);
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
      await api(`/api/checks/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      flash('Check updated.');
    } else {
      await api('/api/checks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      flash('Check created.');
    }
    closeModal();
    loadDashboard();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteCheck(id) {
  if (!await showConfirm('Delete this website?')) return;
  await api(`/api/checks/${id}`, { method: 'DELETE' });
  flash('Website deleted.');
  closeModal();
  loadDashboard();
}


// ========================
// ALERTS TAB
// ========================

async function loadAlertSettings() {
  const data = await api('/api/alerts');
  renderAlertSettings(data.alert_settings);
}

function renderAlertSettings(settings) {
  const area = document.getElementById('alert-settings-area');
  if (!settings.length) { area.innerHTML = '<p>No alert rules yet.</p>'; return; }

  const rows = settings.map(s => {
    const monitors = s.monitor_names ? `<div class="meta cell-scroll">${esc(s.monitor_names.replace(/,/g, ', '))}</div>` : '';
    const comms = s.communication_names ? `<div class="meta cell-scroll">${esc(s.communication_names.replace(/,/g, ', '))}</div>` : '';
    return `<tr>
      <td><div class="cell-clamp-2">${esc(s.name)}</div></td>
      <td>${s.alert_failures} failures / ${s.alert_window_minutes} min</td>
      <td>${s.monitor_count}${monitors}</td>
      <td>${s.communication_count}${comms}</td>
      <td><div class="actions">
        <button onclick="showAlertRuleForm(${s.id})">Edit</button>
        <button onclick="deleteAlertRule(${s.id})">Delete</button>
      </div></td>
    </tr>`;
  }).join('');

  area.innerHTML = `<div class="table-wrap"><table>
    <colgroup>
      <col class="alerts-col-name"><col class="alerts-col-rule">
      <col class="alerts-col-monitors"><col class="alerts-col-communication"><col class="alerts-col-actions">
    </colgroup>
    <thead><tr>
      <th>Name</th><th>Rule</th><th>Monitors</th><th>Communication</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

async function showAlertRuleForm(id) {
  // need checks + recipients for the checkboxes
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
    openModal(`<section class="panel form-panel"><h2>Add alert</h2><p>No monitors yet.</p>
      <div class="form-actions"><button onclick="closeModal()">Back</button></div></section>`);
    return;
  }

  const checkBoxes = checks.map(c =>
    `<label class="checkbox-row"><input type="checkbox" name="check_ids" value="${c.id}" ${selectedCheckIds.includes(c.id) ? 'checked' : ''}><span>${esc(c.name)}</span></label>`
  ).join('');

  const recipientBoxes = recipients.length
    ? recipients.map(r =>
        `<label class="checkbox-row"><input type="checkbox" name="recipient_ids" value="${r.id}" ${selectedRecipientIds.includes(r.id) ? 'checked' : ''}><span>${esc(r.email)}</span></label>`
      ).join('')
    : '<p>No email recipients yet. Add one on the comms tab first.</p>';

  openModal(`<section class="panel form-panel">
    <h2>${rule ? 'Edit alert' : 'Add alert'}</h2>
    <form id="alert-rule-form" class="check-form" onsubmit="saveAlertRule(event, ${id || 'null'})">
      <label><span>Name</span>
        <input name="name" type="text" maxlength="40" value="${esc(r.name || '')}" required>
      </label>
      <fieldset><legend>Monitors</legend><div class="checkbox-list">${checkBoxes}</div></fieldset>
      <fieldset><legend>Communication</legend><div class="checkbox-list">${recipientBoxes}</div></fieldset>
      <div class="grid two-col">
        <label><span>Alert after X failures</span>
          <input name="alert_failures" type="number" min="1" value="${r.alert_failures || 3}" required>
        </label>
        <label><span>Within Y minutes</span>
          <input name="alert_window_minutes" type="number" min="1" value="${r.alert_window_minutes || 15}" required>
        </label>
      </div>
      <div class="form-actions">
        <button type="button" onclick="closeModal()">Cancel</button>
        <button type="submit">Save</button>
      </div>
    </form>
  </section>`);
}

async function saveAlertRule(e, id) {
  e.preventDefault();
  const f = e.target;
  const checkIds = [...f.querySelectorAll('input[name="check_ids"]:checked')].map(i => parseInt(i.value));
  const recipientIds = [...f.querySelectorAll('input[name="recipient_ids"]:checked')].map(i => parseInt(i.value));
  const body = {
    name: f.name.value,
    alert_failures: parseInt(f.alert_failures.value),
    alert_window_minutes: parseInt(f.alert_window_minutes.value),
    check_ids: checkIds,
    recipient_ids: recipientIds,
  };
  try {
    if (id) {
      await api(`/api/alert-rules/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      flash('Alert updated.');
    } else {
      await api('/api/alert-rules', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      flash('Alert created.');
    }
    closeModal();
    loadAlertSettings();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteAlertRule(id) {
  if (!await showConfirm('Delete this alert rule?')) return;
  await api(`/api/alert-rules/${id}`, { method: 'DELETE' });
  flash('Alert deleted.');
  loadAlertSettings();
}


// ========================
// COMMS TAB
// ========================

async function loadCommunication() {
  const data = await api('/api/communication');
  renderEmailSetup(data.email_settings);
  renderEmailRecipients(data.email_recipients);
}

function renderEmailSetup(settings) {
  const area = document.getElementById('email-setup-area');
  if (settings.sender_email || settings.smtp_host) {
    area.innerHTML = `
      <p><strong>From:</strong> ${esc(settings.sender_email || 'Not set')}</p>
      <p><strong>SMTP host:</strong> ${esc(settings.smtp_host || 'Not set')}</p>
      <p><strong>SMTP port:</strong> ${settings.smtp_port}</p>
      <p><strong>TLS:</strong> ${settings.use_tls ? 'On' : 'Off'}</p>`;
  } else {
    area.innerHTML = '<p>No email setup yet.</p>';
  }
}

function renderEmailRecipients(recipients) {
  const area = document.getElementById('email-recipients-area');
  if (!recipients.length) { area.innerHTML = '<p>No email recipients yet.</p>'; return; }

  const rows = recipients.map(r => `<tr>
    <td>${esc(r.email)}</td>
    <td><div class="actions actions-right">
      <button onclick="showEmailForm(${r.id})">Edit</button>
      <button onclick="deleteEmail(${r.id})">Delete</button>
    </div></td>
  </tr>`).join('');

  area.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Email</th><th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

async function showEmailForm(id) {
  let recipient = null;
  if (id) {
    const data = await api('/api/communication');
    recipient = data.email_recipients.find(r => r.id === id);
  }

  openModal(`<section class="panel form-panel">
    <h2>${recipient ? 'Edit email' : 'Add email'}</h2>
    <form class="check-form" onsubmit="saveEmail(event, ${id || 'null'})">
      <label><span>Email</span>
        <input name="email" type="email" value="${esc(recipient ? recipient.email : '')}" required>
      </label>
      <div class="form-actions">
        <button type="button" onclick="closeModal()">Cancel</button>
        <button type="submit">Save</button>
      </div>
    </form>
  </section>`);
}

async function saveEmail(e, id) {
  e.preventDefault();
  const email = new FormData(e.target).get('email');
  try {
    if (id) {
      await api(`/api/communication/emails/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
      flash('Email updated.');
    } else {
      await api('/api/communication/emails', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
      flash('Email added.');
    }
    closeModal();
    loadCommunication();
  } catch (err) {
    flash(err.message, 'error');
  }
}

async function deleteEmail(id) {
  if (!await showConfirm('Delete this email?')) return;
  await api(`/api/communication/emails/${id}`, { method: 'DELETE' });
  flash('Email deleted.');
  loadCommunication();
}


// ========================
// INIT
// ========================

(function init() {
  const hash = location.hash.replace('#', '') || 'monitors';
  const valid = ['monitors', 'alerts', 'comms'];
  showTab(valid.includes(hash) ? hash : 'monitors');
})();
