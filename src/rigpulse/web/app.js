const $ = (s) => document.querySelector(s);
const endpoint = async (path) => {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} on ${path}`);
  return await response.json();
};
const escapeHTML = (s) => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const compact = (n) => Number(n ?? 0).toLocaleString('en-US');
function renderChart(points) {
  const svg = $('#chart');
  if (!points.length) { svg.innerHTML = '<text x="400" y="130" text-anchor="middle" fill="#8394ac">No data for selected signal</text>'; return; }
  const width = 800, height = 265, left = 52, right = 20, top = 20, bottom = 30;
  const values = points.map(p => p.value);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi-lo)*0.12; lo -= pad; hi += pad;
  const x = i => left + (width-left-right)*i/Math.max(1, points.length-1);
  const y = v => height-bottom - (v-lo)/(hi-lo)*(height-top-bottom);
  let html = '';
  for(let j=0;j<5;j++) {
    const v = lo+(hi-lo)*(4-j)/4, yy=y(v);
    html += `<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" stroke="#26384f" stroke-dasharray="3 6"/>`;
    html += `<text x="${left-10}" y="${yy+4}" text-anchor="end" fill="#738ba2" font-size="11">${v.toFixed(1)}</text>`;
  }
  const coords = points.map((p,i)=>`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  html += `<polyline points="${coords}" fill="none" stroke="#64deca" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
  const stride = Math.max(1,Math.floor(points.length/75));
  points.forEach((p,i) => {if (p.is_alert && (i%stride===0 || points.length<80)) {
    html += `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="4.1" fill="#fc858d" stroke="#20293d" stroke-width="1.7"><title>${escapeHTML(p.observed_at)}: ${p.value.toFixed(2)}</title></circle>`;
  }});
  const first = escapeHTML(points[0].observed_at.slice(0,16).replace('T',' '));
  const last = escapeHTML(points[points.length-1].observed_at.slice(0,16).replace('T',' '));
  html += `<text x="${left}" y="260" fill="#738ba2" font-size="10">${first}</text><text x="${width-right}" y="260" text-anchor="end" fill="#738ba2" font-size="10">${last} UTC</text>`;
  svg.innerHTML = html;
}
async function loadSeries() {
  const rig=$('#rig-select').value, metric=$('#metric-select').value;
  if(!rig) { renderChart([]); return; }
  const data=await endpoint(`/api/v1/series?rig_id=${encodeURIComponent(rig)}&metric=${encodeURIComponent(metric)}&limit=240`);
  renderChart(data.points);
}
async function refresh() {
  try {
    const [summary, rigs, alerts]=await Promise.all([
      endpoint('/api/v1/summary'), endpoint('/api/v1/rigs'), endpoint('/api/v1/alerts?limit=12')
    ]);
    $('#rigs').textContent=compact(summary.rigs);
    $('#readings').textContent=compact(summary.readings);
    $('#flagged').textContent=compact(summary.flagged_readings);
    $('#critical').textContent=compact(summary.critical_alerts);
    const select=$('#rig-select'), selected=select.value;
    select.innerHTML=rigs.rigs.map(r=>`<option value="${escapeHTML(r)}">${escapeHTML(r)}</option>`).join('');
    if(rigs.rigs.includes(selected)) select.value=selected;
    $('#alerts').innerHTML=alerts.items.length ? alerts.items.map(a => `<tr><td>${escapeHTML(a.observed_at)}</td><td>${escapeHTML(a.rig_id)}</td><td>${escapeHTML(a.metric)}</td><td>${Number(a.value).toFixed(2)}</td><td>${escapeHTML(a.detector)}</td><td><span class="sev ${escapeHTML(a.severity)}">${escapeHTML(a.severity.toUpperCase())}</span></td></tr>`).join('') : '<tr><td colspan="6">No alerts detected yet. Run the demo to populate data.</td></tr>';
    await loadSeries();
    $('#connection').classList.add('connected');
    $('#connection').innerHTML='<span class="connection-dot"></span> Local API connected';
  } catch (e) {
    $('#connection').classList.remove('connected');
    $('#connection').textContent='API unavailable';
    $('#alerts').innerHTML=`<tr><td colspan="6">Unable to load data: ${escapeHTML(e.message)}</td></tr>`;
  }
}
$('#refresh').addEventListener('click', refresh);
$('#rig-select').addEventListener('change', loadSeries);
$('#metric-select').addEventListener('change', loadSeries);
refresh();
