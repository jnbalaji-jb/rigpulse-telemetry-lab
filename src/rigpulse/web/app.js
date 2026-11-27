const $ = (s) => document.querySelector(s);

const endpoint = async (path, options = {}) => {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} on ${path}`;
    try {
      const err = await response.json();
      if (err && err.detail) detail = err.detail;
    } catch (_) {
      // Ignore non-JSON error payloads
    }
    throw new Error(detail);
  }
  return await response.json();
};

const escapeHTML = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c]));

const compact = (n) => Number(n ?? 0).toLocaleString('en-US');

let activeIncidentId = null;

function showBanner(message, isError = false) {
  const banner = $('#incident-banner');
  if (!banner) return;
  banner.textContent = message;
  banner.classList.toggle('error', isError);
  banner.hidden = false;
}

function renderChart(points) {
  const svg = $('#chart');
  if (!points.length) {
    svg.innerHTML = '<text x="400" y="130" text-anchor="middle" fill="#8394ac">No data for selected signal</text>';
    return;
  }
  const width = 800, height = 265, left = 52, right = 20, top = 20, bottom = 30;
  const values = points.map((p) => p.value);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.12;
  lo -= pad;
  hi += pad;
  const x = (i) => left + ((width - left - right) * i) / Math.max(1, points.length - 1);
  const y = (v) => height - bottom - ((v - lo) / (hi - lo)) * (height - top - bottom);
  let html = '';
  for (let j = 0; j < 5; j++) {
    const v = lo + ((hi - lo) * (4 - j)) / 4, yy = y(v);
    html += `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" stroke="#26384f" stroke-dasharray="3 6"/>`;
    html += `<text x="${left - 10}" y="${yy + 4}" text-anchor="end" fill="#738ba2" font-size="11">${v.toFixed(1)}</text>`;
  }
  const coords = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  html += `<polyline points="${coords}" fill="none" stroke="#64deca" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
  const stride = Math.max(1, Math.floor(points.length / 75));
  points.forEach((p, i) => {
    if (p.is_alert && (i % stride === 0 || points.length < 80)) {
      html += `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="4.1" fill="#fc858d" stroke="#20293d" stroke-width="1.7"><title>${escapeHTML(p.observed_at)}: ${p.value.toFixed(2)}</title></circle>`;
    }
  });
  const first = escapeHTML(points[0].observed_at.slice(0, 16).replace('T', ' '));
  const last = escapeHTML(points[points.length - 1].observed_at.slice(0, 16).replace('T', ' '));
  html += `<text x="${left}" y="260" fill="#738ba2" font-size="10">${first}</text><text x="${width - right}" y="260" text-anchor="end" fill="#738ba2" font-size="10">${last} UTC</text>`;
  svg.innerHTML = html;
}

async function loadSeries() {
  const rig = $('#rig-select').value;
  const metric = $('#metric-select').value;
  if (!rig) {
    renderChart([]);
    return;
  }
  const data = await endpoint(`/api/v1/series?rig_id=${encodeURIComponent(rig)}&metric=${encodeURIComponent(metric)}&limit=240`);
  renderChart(data.points);
}

function transitionOptionsFor(status) {
  if (status === 'open') {
    return [
      { target: 'acknowledged', label: 'Acknowledge' },
      { target: 'investigating', label: 'Investigate' },
      { target: 'resolved', label: 'Resolve' },
    ];
  }
  if (status === 'acknowledged') {
    return [
      { target: 'investigating', label: 'Investigate' },
      { target: 'resolved', label: 'Resolve' },
    ];
  }
  if (status === 'investigating') {
    return [
      { target: 'resolved', label: 'Resolve' },
    ];
  }
  if (status === 'resolved') {
    return [
      { target: 'open', label: 'Reopen' },
    ];
  }
  return [];
}

function renderIncidentDetail(detail) {
  const drawer = $('#incident-detail-panel');
  if (!drawer || !detail) return;
  activeIncidentId = detail.incident_id;
  drawer.hidden = false;

  document.querySelectorAll('#incidents-body tr').forEach((row) => {
    const inspectBtn = row.querySelector('button[data-action="inspect"]');
    row.classList.toggle('selected-row', inspectBtn?.dataset.id === detail.incident_id);
  });
  drawer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  $('#detail-title').textContent = `${detail.incident_id} — ${detail.title}`;
  const ackText = detail.acknowledged_by ? ` · Ack by ${detail.acknowledged_by}` : '';
  const resText = detail.resolved_by ? ` · Resolved by ${detail.resolved_by}` : '';
  $('#detail-meta').textContent = `Rig ${detail.rig_id} · Status: ${detail.status.toUpperCase()} · Opened ${detail.opened_at} · Last seen ${detail.last_seen_at}${ackText}${resText}`;

  const transitions = transitionOptionsFor(detail.status);
  $('#detail-transitions').innerHTML = transitions
    .map((t) => `<button class="btn-ghost" type="button" data-action="transition" data-id="${escapeHTML(detail.incident_id)}" data-status="${escapeHTML(t.target)}">${escapeHTML(t.label)}</button>`)
    .join('');

  $('#detail-alerts').innerHTML = detail.alerts && detail.alerts.length
    ? detail.alerts.map((a) => `
        <div class="detail-item">
          <div class="detail-item-top">
            <strong>${escapeHTML(a.metric)} · ${escapeHTML(a.detector)}</strong>
            <span class="sev ${escapeHTML(a.severity)}">${escapeHTML(a.severity.toUpperCase())}</span>
          </div>
          <small>${escapeHTML(a.observed_at)}${a.message ? ` — ${escapeHTML(a.message)}` : ''}</small>
        </div>
      `).join('')
    : '<div class="detail-empty">No linked alerts recorded.</div>';

  $('#detail-notes').innerHTML = detail.notes && detail.notes.length
    ? detail.notes.map((n) => `
        <div class="detail-item">
          <div class="detail-item-top">
            <strong>${escapeHTML(n.author)} <span class="tag-pill">${escapeHTML(n.action)}</span></strong>
            <small>${escapeHTML(n.created_at)}</small>
          </div>
          <p>${escapeHTML(n.note)}</p>
        </div>
      `).join('')
    : '<div class="detail-empty">No investigation notes yet.</div>';
}

async function openIncidentDetail(incidentId) {
  try {
    const detail = await endpoint(`/api/v1/incidents/${encodeURIComponent(incidentId)}`);
    renderIncidentDetail(detail);
  } catch (e) {
    showBanner(`Failed to load incident ${incidentId}: ${e.message}`, true);
  }
}

async function transitionIncidentState(incidentId, targetStatus) {
  try {
    const updated = await endpoint(`/api/v1/incidents/${encodeURIComponent(incidentId)}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: targetStatus, actor: 'operator' }),
    });
    showBanner(`Incident ${incidentId} transitioned to ${targetStatus.toUpperCase()}.`, false);
    await loadIncidents();
    if (activeIncidentId === incidentId) {
      renderIncidentDetail(updated);
    }
  } catch (e) {
    showBanner(`Transition failed: ${e.message}`, true);
  }
}

async function correlateNow() {
  const btn = $('#correlate-btn');
  if (btn) btn.disabled = true;
  try {
    const res = await endpoint('/api/v1/incidents/correlate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ window_minutes: 60 }),
    });
    showBanner(
      `Correlation complete: evaluated ${res.alerts_evaluated} alerts, created ${res.incidents_created} incidents, linked ${res.alerts_linked} alerts (${res.duplicates_skipped} already linked).`,
      false
    );
    await loadIncidents();
    if (activeIncidentId) {
      await openIncidentDetail(activeIncidentId);
    }
  } catch (e) {
    showBanner(`Alert correlation failed: ${e.message}`, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadIncidents() {
  const statusFilter = $('#incident-status-filter')?.value || '';
  const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}&limit=50` : '?limit=50';
  const [incSummary, incList] = await Promise.all([
    endpoint('/api/v1/incidents/summary'),
    endpoint(`/api/v1/incidents${query}`),
  ]);

  const counts = incSummary.by_status || {};
  $('#inc-open').textContent = compact(counts.open || 0);
  $('#inc-ack').textContent = compact(counts.acknowledged || 0);
  $('#inc-inv').textContent = compact(counts.investigating || 0);
  $('#inc-res').textContent = compact(counts.resolved || 0);

  const tbody = $('#incidents-body');
  if (!incList.items.length) {
    tbody.innerHTML = '<tr><td colspan="8">No incidents found for this filter. Click "Correlate alerts" to group active alerts.</td></tr>';
    return;
  }

  tbody.innerHTML = incList.items.map((inc) => {
    const tags = [...(inc.metrics || []), ...(inc.detectors || [])]
      .map((t) => `<span class="tag-pill">${escapeHTML(t)}</span>`)
      .join(' ');
    const quickButtons = transitionOptionsFor(inc.status)
      .slice(0, 2)
      .map((t) => `<button class="btn-ghost" type="button" data-action="transition" data-id="${escapeHTML(inc.incident_id)}" data-status="${escapeHTML(t.target)}">${escapeHTML(t.label)}</button>`)
      .join('');

    return `
      <tr class="${activeIncidentId === inc.incident_id ? 'selected-row' : ''}">
        <td><strong>${escapeHTML(inc.incident_id)}</strong></td>
        <td>${escapeHTML(inc.rig_id)}</td>
        <td class="wrap-cell">
          <div>${escapeHTML(inc.title)}</div>
          <div class="tag-row">${tags}</div>
        </td>
        <td>${compact(inc.alert_count)}</td>
        <td><span class="sev ${escapeHTML(inc.severity)}">${escapeHTML(inc.severity.toUpperCase())}</span></td>
        <td><span class="status-pill ${escapeHTML(inc.status)}">${escapeHTML(inc.status.toUpperCase())}</span></td>
        <td>${escapeHTML(inc.last_seen_at)}</td>
        <td>
          <div class="action-group">
            <button class="btn-ghost" type="button" data-action="inspect" data-id="${escapeHTML(inc.incident_id)}">Inspect</button>
            ${quickButtons}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

async function refresh() {
  try {
    const [summary, rigs, alerts] = await Promise.all([
      endpoint('/api/v1/summary'),
      endpoint('/api/v1/rigs'),
      endpoint('/api/v1/alerts?limit=25'),
    ]);
    $('#rigs').textContent = compact(summary.rigs);
    $('#readings').textContent = compact(summary.readings);
    $('#flagged').textContent = compact(summary.flagged_readings);
    $('#critical').textContent = compact(summary.critical_alerts);

    const hasMlAlerts = alerts.items.some((a) => a.detector === 'isolation_forest');
    const mlPill = $('#ml-engine-pill');
    const mlItem = $('#ml-engine-item');
    if (mlPill && mlItem) {
      mlPill.textContent = hasMlAlerts ? 'ACTIVE' : 'READY';
      mlPill.classList.toggle('ok', hasMlAlerts);
      mlItem.classList.toggle('planned', !hasMlAlerts);
    }

    const select = $('#rig-select');
    const selected = select.value;
    select.innerHTML = rigs.rigs.map((r) => `<option value="${escapeHTML(r)}">${escapeHTML(r)}</option>`).join('');
    if (rigs.rigs.includes(selected)) select.value = selected;

    $('#alerts').innerHTML = alerts.items.length
      ? alerts.items.map((a) => `<tr><td>${escapeHTML(a.observed_at)}</td><td>${escapeHTML(a.rig_id)}</td><td>${escapeHTML(a.metric)}</td><td>${Number(a.value).toFixed(2)}</td><td>${escapeHTML(a.detector)}</td><td><span class="sev ${escapeHTML(a.severity)}">${escapeHTML(a.severity.toUpperCase())}</span></td></tr>`).join('')
      : '<tr><td colspan="6">No alerts detected yet. Run the demo to populate data.</td></tr>';

    await Promise.all([loadSeries(), loadIncidents()]);
    if (activeIncidentId) {
      await openIncidentDetail(activeIncidentId);
    }

    $('#connection').classList.add('connected');
    $('#connection').innerHTML = '<span class="connection-dot"></span> Local API connected';
  } catch (e) {
    $('#connection').classList.remove('connected');
    $('#connection').textContent = 'API unavailable';
    $('#alerts').innerHTML = `<tr><td colspan="6">Unable to load data: ${escapeHTML(e.message)}</td></tr>`;
  }
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('button[data-action]');
  if (!target) return;
  const action = target.dataset.action;
  const id = target.dataset.id;
  if (action === 'inspect' && id) {
    openIncidentDetail(id);
  } else if (action === 'transition' && id && target.dataset.status) {
    transitionIncidentState(id, target.dataset.status);
  }
});

document.querySelectorAll('.side-link').forEach((link) => {
  link.addEventListener('click', () => {
    document.querySelectorAll('.side-link').forEach((el) => el.classList.remove('active'));
    link.classList.add('active');
  });
});

$('#refresh').addEventListener('click', refresh);
$('#rig-select').addEventListener('change', loadSeries);
$('#metric-select').addEventListener('change', loadSeries);
$('#incident-status-filter').addEventListener('change', loadIncidents);
$('#correlate-btn').addEventListener('click', correlateNow);
$('#close-detail-btn').addEventListener('click', () => {
  activeIncidentId = null;
  $('#incident-detail-panel').hidden = true;
  document.querySelectorAll('#incidents-body tr.selected-row').forEach((row) => {
    row.classList.remove('selected-row');
  });
});

$('#note-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!activeIncidentId) return;
  const author = $('#note-author').value.trim() || 'operator';
  const noteInput = $('#note-text');
  const note = noteInput.value.trim();
  if (!note) return;

  try {
    const updated = await endpoint(`/api/v1/incidents/${encodeURIComponent(activeIncidentId)}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, author }),
    });
    noteInput.value = '';
    showBanner(`Note added to ${activeIncidentId}.`, false);
    renderIncidentDetail(updated);
  } catch (e) {
    showBanner(`Failed to add note: ${e.message}`, true);
  }
});

refresh();