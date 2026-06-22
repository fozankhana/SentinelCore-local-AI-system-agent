/* SentinelCore dashboard — live metrics engine */

const SC = {
  sse: null,
  charts: {},
  history: { cpu: [], mem: [], net_in: [], net_out: [], gpu: [] },
  MAX_POINTS: 60,
  last: {},

  init() {
    this.connectSSE();
    this.initCharts();
    setInterval(() => this.tick(), 5000);
  },

  connectSSE() {
    if (this.sse) this.sse.close();
    this.sse = new EventSource('/api/stream');
    this.sse.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.error) return;
        this.last = data;
        this.update(data);
      } catch (_) {}
    };
    this.sse.onerror = () => {
      setTimeout(() => this.connectSSE(), 3000);
    };
  },

  update(d) {
    this.updateCPU(d.cpu || {});
    this.updateMem(d.memory || {});
    this.updateGPU(d.gpus || []);
    this.updateNetwork(d.network || {});
    this.updateDisk(d.disks || []);
    this.updateProcesses(d.processes || []);
    this.updateEnforcement(d.enforcement || []);
    this.updateAlertBadge(d.alert_count || 0);
    this.updateTopbar(d);
    this.updateHealthScore(d);
  },

  // ── CPU ──
  updateCPU(cpu) {
    setText('cpu-total', fmt1(cpu.total_pct) + '%');
    setText('cpu-cores', (cpu.count_logical || '—') + ' logical');
    barFill('cpu-bar', cpu.total_pct, 85, 95);

    const pct = cpu.total_pct || 0;
    this.push(this.history.cpu, pct);
    chartPush(this.charts.cpu, pct);

    const grid = document.getElementById('core-grid');
    if (grid && cpu.cores) {
      if (grid.children.length !== cpu.cores.length) {
        grid.innerHTML = cpu.cores.map((_, i) =>
          `<div class="core-cell" id="core-${i}"></div>`
        ).join('');
      }
      cpu.cores.forEach((c, i) => {
        const el = document.getElementById(`core-${i}`);
        if (!el) return;
        const cls = c.pct > 90 ? 'danger' : c.pct > 70 ? 'warn' : '';
        el.innerHTML = `
          <div class="text-xs text-dim">C${i}</div>
          <div class="fw-600 text-sm">${fmt1(c.pct)}%</div>
          <div class="bar mt-3"><div class="bar-fill ${cls}" style="width:${c.pct}%"></div></div>
          ${c.mhz ? `<div class="text-xs text-dim mt-3">${Math.round(c.mhz)} MHz</div>` : ''}
        `;
      });
    }
  },

  // ── Memory ──
  updateMem(mem) {
    setText('mem-used', fmtGB(mem.used_mb) + ' GB');
    setText('mem-total', '/ ' + fmtGB(mem.total_mb) + ' GB');
    setText('mem-pct', fmt1(mem.pct) + '%');
    setText('swap-used', fmtGB(mem.swap_mb) + ' GB');
    setText('swap-total', '/ ' + fmtGB(mem.swap_total_mb) + ' GB');
    barFill('mem-bar', mem.pct, 85, 95);
    barFill('swap-bar', mem.swap_pct, 70, 90);

    this.push(this.history.mem, mem.pct || 0);
    chartPush(this.charts.mem, mem.pct || 0);
  },

  // ── GPU ──
  updateGPU(gpus) {
    const el = document.getElementById('gpu-list');
    if (!el) return;
    if (!gpus.length) {
      el.innerHTML = '<div class="text-dim text-sm">No GPU detected.</div>';
      return;
    }
    gpus.forEach((g, i) => {
      let panel = document.getElementById(`gpu-panel-${i}`);
      if (!panel) {
        panel = document.createElement('div');
        panel.id = `gpu-panel-${i}`;
        panel.className = 'gpu-panel mb-4';
        el.appendChild(panel);
      }
      const temp = g.temp_c != null ? `${g.temp_c}°C` : '—';
      const tempCls = g.temp_c > 85 ? 'text-red' : g.temp_c > 70 ? 'text-yellow' : 'text-green';
      const vramPct = g.vram_total_mb ? (g.vram_used_mb / g.vram_total_mb * 100) : 0;
      const watts = g.watts != null ? `${g.watts}W` : '—';
      const util = g.util_pct != null ? g.util_pct : 0;

      panel.innerHTML = `
        <div class="gpu-name">${g.name || 'GPU ' + i}
          <span class="pill pill-gray ml-2">${g.backend || ''}</span>
        </div>
        <div class="g2 gap-3">
          <div>
            <div class="gpu-gauge"><span class="gauge-label">Core util</span><span class="gauge-val">${util}%</span></div>
            <div class="bar"><div class="bar-fill ${util>90?'danger':util>70?'warn':''}" style="width:${util}%"></div></div>
          </div>
          <div>
            <div class="gpu-gauge"><span class="gauge-label">VRAM</span><span class="gauge-val">${fmtGB(g.vram_used_mb)} / ${fmtGB(g.vram_total_mb)} GB</span></div>
            <div class="bar"><div class="bar-fill ${vramPct>90?'danger':vramPct>75?'warn':''}" style="width:${vramPct.toFixed(1)}%"></div></div>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-dim text-xs">Temp</span>
            <span class="fw-600 ${tempCls}">${temp}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-dim text-xs">Power</span>
            <span class="fw-600">${watts}</span>
          </div>
        </div>
      `;
    });

    if (gpus[0]) {
      this.push(this.history.gpu, gpus[0].util_pct || 0);
      chartPush(this.charts.gpu, gpus[0].util_pct || 0);
    }
  },

  // ── Network ──
  updateNetwork(net) {
    const ifaces = net.interfaces || [];
    const totalIn  = ifaces.reduce((s, i) => s + (i.bytes_in  || 0), 0);
    const totalOut = ifaces.reduce((s, i) => s + (i.bytes_out || 0), 0);

    setText('net-in',  fmtRate(totalIn));
    setText('net-out', fmtRate(totalOut));

    this.push(this.history.net_in,  totalIn  / 1024);
    this.push(this.history.net_out, totalOut / 1024);
    chartPush(this.charts.net_in,  totalIn  / 1024);
    chartPush(this.charts.net_out, totalOut / 1024);

    const tbody = document.getElementById('iface-tbody');
    if (tbody) {
      tbody.innerHTML = ifaces.map(i => `
        <tr>
          <td class="font-mono">${i.name}</td>
          <td class="text-green">${fmtRate(i.bytes_in)}</td>
          <td class="text-yellow">${fmtRate(i.bytes_out)}</td>
          <td class="text-dim">${fmtMB(i.total_recv_mb)}</td>
          <td class="text-dim">${fmtMB(i.total_sent_mb)}</td>
        </tr>`).join('');
    }

    const conns = net.connections || [];
    const ctbody = document.getElementById('conn-tbody');
    if (ctbody) {
      ctbody.innerHTML = conns.slice(0, 50).map(c => `
        <tr>
          <td>${c.proc || '—'}</td>
          <td class="font-mono text-xs">${c.laddr}</td>
          <td class="font-mono text-xs">${c.raddr}</td>
          <td><span class="pill ${c.status==='ESTABLISHED'?'pill-green':'pill-gray'}">${c.status}</span></td>
        </tr>`).join('');
    }
  },

  // ── Disk ──
  updateDisk(disks) {
    setText('disk-read',  fmtRate((disks[0] || {}).read_mbs  * 1048576 || 0));
    setText('disk-write', fmtRate((disks[0] || {}).write_mbs * 1048576 || 0));

    const tbody = document.getElementById('disk-tbody');
    if (tbody) {
      tbody.innerHTML = disks.map(d => `
        <tr>
          <td class="font-mono">${d.device}</td>
          <td class="text-green">${d.read_mbs.toFixed(2)} MB/s</td>
          <td class="text-yellow">${d.write_mbs.toFixed(2)} MB/s</td>
        </tr>`).join('');
    }
  },

  // ── Processes ──
  updateProcesses(procs) {
    const tbody = document.getElementById('proc-tbody');
    if (!tbody) return;

    const filter = (document.getElementById('proc-search') || {}).value || '';
    const filtered = filter
      ? procs.filter(p => p.name.toLowerCase().includes(filter.toLowerCase()))
      : procs;

    tbody.innerHTML = filtered.slice(0, 60).map(p => {
      const cpuCls = p.cpu_pct > 50 ? 'text-red' : p.cpu_pct > 20 ? 'text-yellow' : '';
      const ramCls = p.ram_mb > 2000 ? 'text-yellow' : '';
      return `
        <tr>
          <td class="text-dim">${p.pid}</td>
          <td class="font-mono truncate" style="max-width:200px">${p.name}</td>
          <td class="fw-600 ${cpuCls}">${fmt1(p.cpu_pct)}%</td>
          <td class="${ramCls}">${fmtMB(p.ram_mb)}</td>
          <td>${p.vram_mb > 0 ? fmtMB(p.vram_mb) : '—'}</td>
          <td><span class="pill ${p.status==='running'?'pill-green':'pill-gray'}">${p.status}</span></td>
          <td>
            <button class="btn btn-sm btn-warn" onclick="SC.throttleProc(${p.pid},'${escHtml(p.name)}')">⬇</button>
            <button class="btn btn-sm btn-danger" onclick="SC.killProc(${p.pid},'${escHtml(p.name)}')">✕</button>
          </td>
        </tr>`;
    }).join('');
  },

  // ── Enforcement ──
  updateEnforcement(rules) {
    const el = document.getElementById('enf-list');
    if (!el) return;
    if (!rules.length) {
      el.innerHTML = '<div class="text-dim text-sm" style="padding:8px 0">No enforcement rules configured.</div>';
      return;
    }
    const total     = rules.length;
    const violations = rules.filter(r => r.running && !r.compliant).length;
    const cntEl  = document.getElementById('enf-compliant-count');
    const violEl = document.getElementById('enf-violation-count');
    if (cntEl)  cntEl.textContent  = `${total - violations} / ${total} compliant`;
    if (violEl) { violEl.textContent = violations + ' violations'; violEl.style.display = violations > 0 ? '' : 'none'; }

    el.innerHTML = rules.map(r => {
      const notRunning  = !r.running;
      const violated    = r.running && !r.compliant;
      const compliant   = r.running &&  r.compliant;
      const dotColor    = notRunning ? 'var(--text-dim)' : violated ? 'var(--red)' : 'var(--green)';
      const statusPill  = notRunning
        ? '<span class="pill pill-gray">Offline</span>'
        : violated
          ? '<span class="pill pill-red">Violation</span>'
          : '<span class="pill pill-green">Compliant</span>';
      const vramStr     = (r.vram_mb > 0) ? fmtMB(r.vram_mb) : '—';
      const violBadge   = r.violation_count > 0
        ? `<span class="text-xs text-red">${r.violation_count}✗</span>`
        : '';
      const lastAct     = r.last_action
        ? `<span class="pill pill-yellow text-xs">${r.last_action}</span>`
        : '';
      const migrateBtn  = (r.running && r.gpu_enforce && r.pid)
        ? `<button class="btn btn-sm" onclick="SC.migrateProc(${r.pid},'${escHtml(r.exe)}')">GPU →</button>`
        : '';
      return `
        <div class="enf-row">
          <div class="enf-dot" style="background:${dotColor}"></div>
          <div class="flex-1">
            <div class="text-sm fw-600">${escHtml(r.rule_name || r.exe)}</div>
            <div class="text-xs text-dim">${escHtml(r.exe)}${r.pid ? ' · PID ' + r.pid : ''}${r.gpu_enforce ? ' · GPU enforced' : ''}</div>
          </div>
          <div class="flex gap-2 items-center" style="flex-wrap:wrap;justify-content:flex-end">
            <span class="text-xs text-dim">VRAM: ${vramStr}</span>
            ${violBadge}
            ${lastAct}
            ${statusPill}
            ${r.gpu_enforce ? '<span class="pill pill-blue">GPU</span>' : ''}
            <span class="pill pill-gray">${r.action}</span>
            ${migrateBtn}
          </div>
        </div>`;
    }).join('');
  },

  // ── Topbar ──
  updateTopbar(d) {
    const cpu = (d.cpu || {}).total_pct || 0;
    const mem = (d.memory || {}).pct || 0;
    const gpuUtil = d.gpus && d.gpus[0] ? (d.gpus[0].util_pct || 0) : null;

    setText('tb-cpu', fmt1(cpu) + '%');
    setText('tb-mem', fmt1(mem) + '%');
    if (gpuUtil !== null) setText('tb-gpu', fmt1(gpuUtil) + '%');
  },

  // ── Health score ──
  updateHealthScore(d) {
    const el = document.getElementById('health-score');
    if (!el) return;
    fetch('/api/health').then(r => r.json()).then(h => {
      el.textContent = h.score;
      el.className = 'health-num ' + (h.score >= 80 ? 'h-good' : h.score >= 50 ? 'h-ok' : 'h-bad');
    }).catch(() => {});
  },

  // ── Alert badge ──
  updateAlertBadge(count) {
    const el = document.getElementById('alert-badge');
    if (!el) return;
    el.textContent = count > 0 ? count : '';
    el.style.display = count > 0 ? 'inline-block' : 'none';
  },

  // ── Process actions ──
  async killProc(pid, name) {
    if (!confirm(`Kill ${name} (PID ${pid})?`)) return;
    const r = await fetch(`/api/processes/${pid}/kill`, { method: 'POST' });
    const j = await r.json();
    toast(j.message || j.error, j.ok ? 'green' : 'red');
  },

  async throttleProc(pid, name) {
    const r = await fetch(`/api/processes/${pid}/throttle`, { method: 'POST' });
    const j = await r.json();
    toast(j.message || j.error, j.ok ? 'yellow' : 'red');
  },

  async migrateProc(pid, name) {
    const r = await fetch(`/api/processes/${pid}/migrate`, { method: 'POST' });
    const j = await r.json();
    toast(j.message || j.error, j.ok ? 'green' : 'red');
  },

  // ── Charts init ──
  initCharts() {
    const defaults = {
      type: 'line',
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false },
          y: { display: false, min: 0 },
        },
        elements: {
          point: { radius: 0 },
          line:  { tension: 0.3, borderWidth: 1.5 },
        },
      },
    };

    const makeChart = (id, color, max) => {
      const canvas = document.getElementById(id);
      if (!canvas) return null;
      const labels = Array(SC.MAX_POINTS).fill('');
      const data   = Array(SC.MAX_POINTS).fill(0);
      const opts   = JSON.parse(JSON.stringify(defaults));
      if (max) opts.options.scales.y.max = max;
      return new Chart(canvas, {
        ...opts,
        data: {
          labels,
          datasets: [{
            data,
            borderColor: color,
            backgroundColor: color + '18',
            fill: true,
          }],
        },
      });
    };

    this.charts.cpu     = makeChart('chart-cpu', '#22c55e', 100);
    this.charts.mem     = makeChart('chart-mem', '#3b82f6', 100);
    this.charts.gpu     = makeChart('chart-gpu', '#a855f7', 100);
    this.charts.net_in  = makeChart('chart-net-in',  '#22c55e');
    this.charts.net_out = makeChart('chart-net-out', '#f59e0b');
  },

  push(arr, val) {
    arr.push(val);
    if (arr.length > this.MAX_POINTS) arr.shift();
  },

  tick() {
    // periodic refresh for alerts page
    const page = document.body.dataset.page;
    if (page === 'alerts') loadAlerts();
    if (page === 'audit')  loadAudit();
  },
};

// ── Chart helper ──
function chartPush(chart, val) {
  if (!chart) return;
  chart.data.labels.push('');
  chart.data.labels.shift();
  chart.data.datasets[0].data.push(val);
  chart.data.datasets[0].data.shift();
  chart.update('none');
}

// ── Formatters ──
function fmt1(v)     { return v != null ? Number(v).toFixed(1) : '—'; }
function fmtGB(mb)   { return mb != null ? (mb / 1024).toFixed(2) : '—'; }
function fmtMB(mb)   { return mb != null ? (mb >= 1024 ? (mb/1024).toFixed(1)+' GB' : Math.round(mb)+' MB') : '—'; }

function fmtRate(bps) {
  if (bps == null) return '—';
  if (bps >= 1048576) return (bps / 1048576).toFixed(1) + ' MB/s';
  if (bps >= 1024)    return (bps / 1024).toFixed(0)    + ' KB/s';
  return Math.round(bps) + ' B/s';
}

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function fmtDateTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function escHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ── DOM helpers ──
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function barFill(id, pct, warnAt = 75, dangerAt = 90) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width = Math.min(100, pct || 0) + '%';
  el.className = 'bar-fill' + (pct >= dangerAt ? ' danger' : pct >= warnAt ? ' warn' : '');
}

// ── Toast ──
function toast(msg, color = 'green') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.style.borderColor = color === 'red' ? 'var(--red)' : color === 'yellow' ? 'var(--yellow)' : 'var(--green)';
  el.style.display = 'block';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// ── Alerts page ──
function loadAlerts() {
  fetch('/api/alerts?limit=100').then(r => r.json()).then(data => {
    const el = document.getElementById('alerts-list');
    if (!el) return;
    if (!data.length) {
      el.innerHTML = '<div class="text-dim text-sm" style="padding:12px 0">No alerts recorded.</div>';
      return;
    }
    el.innerHTML = data.map(a => `
      <div class="alert-row" id="alert-${a.id}">
        <div class="alert-dot ad-${a.severity}"></div>
        <div class="alert-msg ${a.acknowledged ? 'text-dim' : ''}">${escHtml(a.message)}</div>
        <div class="alert-ts">${fmtDateTime(a.timestamp)}</div>
        <span class="pill ${a.severity==='critical'?'pill-red':a.severity==='warning'?'pill-yellow':'pill-blue'}">${a.severity}</span>
        ${!a.acknowledged ? `<button class="btn btn-sm" onclick="ackAlert(${a.id})">Ack</button>` : '<span class="text-dim text-xs">acked</span>'}
      </div>`).join('');
  });
}

function ackAlert(id) {
  fetch(`/api/alerts/${id}/ack`, { method: 'POST' }).then(() => loadAlerts());
}

function ackAll() {
  fetch('/api/alerts/ack_all', { method: 'POST' }).then(() => loadAlerts());
}

// ── Audit page ──
function loadAudit() {
  fetch('/api/audit?limit=200').then(r => r.json()).then(data => {
    const tbody = document.getElementById('audit-tbody');
    if (!tbody) return;
    tbody.innerHTML = data.map(a => `
      <tr>
        <td class="text-dim text-xs">${fmtDateTime(a.timestamp)}</td>
        <td><span class="pill ${actionPill(a.action)}">${a.action}</span></td>
        <td class="font-mono">${escHtml(a.target)}</td>
        <td class="text-dim">${escHtml(a.reason)}</td>
        <td class="text-dim">${escHtml(a.result)}</td>
      </tr>`).join('');
  });
}

function actionPill(a) {
  const m = {
    KILL:'pill-red', THROTTLE:'pill-yellow', RESTART:'pill-blue',
    ALERT:'pill-yellow', ENFORCE:'pill-green', MIGRATE:'pill-blue',
    VIOLATED:'pill-red', RESTORED:'pill-green',
  };
  return m[a] || 'pill-gray';
}

// ── Config page ──
function loadConfig() {
  fetch('/api/config').then(r => r.json()).then(cfg => {
    const el = document.getElementById('config-json');
    if (el) el.textContent = JSON.stringify(cfg, null, 2);
  });
}

// ── DOM ready ──
document.addEventListener('DOMContentLoaded', () => {
  const page = document.body.dataset.page;
  if (typeof Chart !== 'undefined') SC.init();
  if (page === 'alerts') loadAlerts();
  if (page === 'audit')  loadAudit();
  if (page === 'config') loadConfig();
});
