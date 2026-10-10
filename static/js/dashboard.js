// Electro Dashboard Frontend JavaScript

let currentRange = '24h';
let historyChart = null;
let pollTimer = null;

function formatTimestamp(isoString) {
  if (!isoString) return '--';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch (e) {
    return isoString;
  }
}

function formatCurrency(val) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  return Number(val).toFixed(2);
}

function formatKwh(val) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  return Number(val).toFixed(2);
}

// Update connection header status indicator
function updateConnectionStatus(status, isStale, lastSuccess, errorCategory, errorMsg, ageSeconds, nextRetry, pollInterval) {
  const dot = document.getElementById('connection-dot');
  const text = document.getElementById('connection-text');
  const banner = document.getElementById('alert-banner');
  const alertTitle = document.getElementById('alert-title');
  const alertBody = document.getElementById('alert-body');

  dot.className = 'h-2 w-2 rounded-full mr-2';

  const cadenceText = pollInterval ? `${pollInterval}s cadence` : '15s';
  const ageDisplay = ageSeconds !== null && ageSeconds !== undefined ? `${Math.round(ageSeconds)}s ago` : formatTimestamp(lastSuccess);

  if (status === 'connected') {
    dot.classList.add('bg-emerald-500');
    text.textContent = `Live (${cadenceText}) · Updated ${ageDisplay}`;
    banner.classList.add('hidden');
  } else if (status === 'authentication_required') {
    dot.classList.add('bg-rose-500', 'animate-pulse');
    text.textContent = nextRetry ? `Auth Retry Pending (${formatTimestamp(nextRetry)})` : 'Authentication Required';
    banner.className = 'rounded-xl border border-rose-500/40 bg-rose-950/40 p-4 text-sm flex items-start space-x-3 text-rose-200';
    alertTitle.textContent = 'Authentication Required';
    alertBody.textContent = errorMsg || 'Please configure valid ELNet credentials in your local environment.';
    banner.classList.remove('hidden');
  } else if (status === 'stale' || isStale) {
    dot.classList.add('bg-amber-500');
    text.textContent = `Stale Data · Age: ${ageDisplay}`;
    banner.className = 'rounded-xl border border-amber-500/40 bg-amber-950/40 p-4 text-sm flex items-start space-x-3 text-amber-200';
    alertTitle.textContent = 'Connection Degraded (Stale Data)';
    alertBody.textContent = `${errorMsg || 'Upstream request failed; displaying cached snapshot data.'}${nextRetry ? ` Retrying at ${formatTimestamp(nextRetry)}.` : ''}`;
    banner.classList.remove('hidden');
  } else {
    dot.classList.add('bg-slate-500');
    text.textContent = 'Offline';
    if (errorMsg) {
      banner.className = 'rounded-xl border border-slate-700 bg-slate-800/80 p-4 text-sm flex items-start space-x-3 text-slate-300';
      alertTitle.textContent = 'Service Offline';
      alertBody.textContent = errorMsg;
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
    }
  }
}

// Update Active Power Source hero card based strictly on EBDGStatus
function updatePowerSourceIndicator(ebDgStatus) {
  const heroCard = document.getElementById('power-source-hero');
  const iconEl = document.getElementById('source-badge-icon');
  const subtagEl = document.getElementById('source-subtag');
  const titleEl = document.getElementById('source-title');
  const descEl = document.getElementById('source-description');
  const pillEl = document.getElementById('source-pill');
  const pillDot = document.getElementById('source-pill-dot');
  const pillText = document.getElementById('source-pill-text');

  if (ebDgStatus === 0) {
    // EBDGStatus == 0: Main Source / Grid (EB)
    heroCard.className = 'rounded-xl border p-5 shadow-lg flex flex-wrap items-center justify-between gap-4 transition bg-gradient-to-r from-emerald-950/70 via-slate-800/90 to-slate-800/80 border-emerald-500/50 shadow-emerald-950/20';
    iconEl.className = 'h-14 w-14 rounded-2xl flex items-center justify-center text-3xl bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 shadow-inner';
    iconEl.textContent = '⚡';
    subtagEl.className = 'text-[10px] px-2 py-0.5 rounded-full font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-700/60';
    subtagEl.textContent = 'EBDGStatus: 0 (Grid)';
    titleEl.textContent = 'Running on Main Source';
    titleEl.className = 'text-2xl sm:text-3xl font-extrabold tracking-tight text-emerald-300 mt-1';
    descEl.textContent = 'Grid electricity supply active · Normal tariff rate';

    pillEl.className = 'px-4 py-2 rounded-xl text-xs font-bold tracking-wide uppercase bg-emerald-500/20 text-emerald-300 border border-emerald-500/50 flex items-center gap-2';
    pillDot.className = 'h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400';
    pillText.textContent = 'Main Source (EB)';
  } else if (ebDgStatus === 1) {
    // EBDGStatus == 1: Generator (DG)
    heroCard.className = 'rounded-xl border p-5 shadow-lg flex flex-wrap items-center justify-between gap-4 transition bg-gradient-to-r from-amber-950/80 via-slate-800/90 to-slate-800/80 border-amber-500/60 shadow-amber-950/30';
    iconEl.className = 'h-14 w-14 rounded-2xl flex items-center justify-center text-3xl bg-amber-500/20 text-amber-400 border border-amber-500/40 shadow-inner';
    iconEl.textContent = '⛽';
    subtagEl.className = 'text-[10px] px-2 py-0.5 rounded-full font-mono bg-amber-950/80 text-amber-400 border border-amber-700/60';
    subtagEl.textContent = 'EBDGStatus: 1 (DG)';
    titleEl.textContent = 'Running on DG';
    titleEl.className = 'text-2xl sm:text-3xl font-extrabold tracking-tight text-amber-300 mt-1';
    descEl.textContent = 'Backup generator power active · DG tariff rate applies';

    pillEl.className = 'px-4 py-2 rounded-xl text-xs font-bold tracking-wide uppercase bg-amber-500/20 text-amber-300 border border-amber-500/50 flex items-center gap-2 animate-pulse';
    pillDot.className = 'h-2.5 w-2.5 rounded-full bg-amber-400 shadow-sm shadow-amber-400';
    pillText.textContent = 'Generator (DG)';
  } else {
    // EBDGStatus == null or any unexpected value
    heroCard.className = 'rounded-xl border p-5 shadow-sm flex flex-wrap items-center justify-between gap-4 transition bg-slate-800/80 border-slate-700';
    iconEl.className = 'h-14 w-14 rounded-2xl flex items-center justify-center text-3xl bg-slate-700 text-slate-400 border border-slate-600 shadow-inner';
    iconEl.textContent = '❓';
    subtagEl.className = 'text-[10px] px-2 py-0.5 rounded-full font-mono bg-slate-800 text-slate-400 border border-slate-700';
    subtagEl.textContent = `EBDGStatus: ${ebDgStatus !== null && ebDgStatus !== undefined ? ebDgStatus : 'null'}`;
    titleEl.textContent = 'Meter Status Unavailable';
    titleEl.className = 'text-2xl sm:text-3xl font-extrabold tracking-tight text-slate-200 mt-1';
    descEl.textContent = 'EBDGStatus is null or unverified value';

    pillEl.className = 'px-4 py-2 rounded-xl text-xs font-bold tracking-wide uppercase bg-slate-700 text-slate-300 border border-slate-600 flex items-center gap-2';
    pillDot.className = 'h-2 w-2 rounded-full bg-slate-400';
    pillText.textContent = 'Unknown';
  }
}

// Update Relay Status independently from power source
function updateRelayStatus(relayStatus) {
  const relayPill = document.getElementById('relay-pill');
  const metricRelay = document.getElementById('metric-relay');

  const s = (relayStatus || '').trim().toUpperCase();

  if (s === 'ON') {
    relayPill.textContent = 'ON';
    relayPill.className = 'font-bold text-emerald-400 font-mono';
    metricRelay.textContent = 'ON';
    metricRelay.className = 'inline-flex items-center px-3 py-1 rounded-md text-sm font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-500/30';
  } else if (s === 'OFF') {
    relayPill.textContent = 'OFF';
    relayPill.className = 'font-bold text-rose-400 font-mono';
    metricRelay.textContent = 'OFF';
    metricRelay.className = 'inline-flex items-center px-3 py-1 rounded-md text-sm font-semibold bg-rose-950/60 text-rose-400 border border-rose-500/30';
  } else {
    relayPill.textContent = s || 'UNKNOWN';
    relayPill.className = 'font-bold text-slate-400 font-mono';
    metricRelay.textContent = s || 'UNKNOWN';
    metricRelay.className = 'inline-flex items-center px-3 py-1 rounded-md text-sm font-semibold bg-slate-700 text-slate-300';
  }
}

// Fetch dashboard snapshot and update DOM
async function fetchDashboard() {
  try {
    const res = await fetch('/api/dashboard');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    updateConnectionStatus(
      data.status,
      data.stale,
      data.last_success_at,
      data.last_error_category,
      data.last_error_message,
      data.age_seconds,
      data.next_retry_at,
      data.poll_interval_seconds
    );

    const snapshot = data.data;
    if (snapshot) {
      // 1. Prominent Active Power Source Indicator (strictly from EBDGStatus)
      updatePowerSourceIndicator(snapshot.eb_dg_status);

      // 2. Independent Relay Status
      updateRelayStatus(snapshot.relay_status);

      // 3. Primary Cards
      document.getElementById('metric-balance').textContent = formatCurrency(snapshot.meter_balance);
      document.getElementById('metric-kwh-today').textContent = formatKwh(snapshot.kwh_rec_today);
      document.getElementById('metric-kwh-month').textContent = formatKwh(snapshot.kwh_rec_month);

      // 4. Charges breakdown
      document.getElementById('charge-today-eb').textContent = snapshot.current_day_eb !== null ? `₹${formatCurrency(snapshot.current_day_eb)}` : '--';
      document.getElementById('charge-today-dg').textContent = snapshot.current_day_dg !== null ? `₹${formatCurrency(snapshot.current_day_dg)}` : '--';
      document.getElementById('charge-today-solar').textContent = snapshot.current_day_solar !== null ? `₹${formatCurrency(snapshot.current_day_solar)}` : '--';
      document.getElementById('charge-today-fixed').textContent = snapshot.fixed_charges_today !== null ? `₹${formatCurrency(snapshot.fixed_charges_today)}` : '--';

      document.getElementById('charge-month-eb').textContent = snapshot.current_month_eb !== null ? `₹${formatCurrency(snapshot.current_month_eb)}` : '--';
      document.getElementById('charge-month-dg').textContent = snapshot.current_month_dg !== null ? `₹${formatCurrency(snapshot.current_month_dg)}` : '--';
      document.getElementById('charge-month-solar').textContent = snapshot.current_month_solar !== null ? `₹${formatCurrency(snapshot.current_month_solar)}` : '--';
      document.getElementById('charge-month-fixed').textContent = snapshot.fixed_charges_month !== null ? `₹${formatCurrency(snapshot.fixed_charges_month)}` : '--';

      // 5. Source flags
      setFlagBadge('flag-eb', snapshot.is_eb);
      setFlagBadge('flag-dg', snapshot.is_dg);
      setFlagBadge('flag-solar', snapshot.is_solar);
      setFlagBadge('flag-fixed', snapshot.is_fixed_charge);

      document.getElementById('meter-id-label').textContent = snapshot.meter_id || '--';
    }
  } catch (err) {
    console.error('Failed to load dashboard:', err);
  }
}

function setFlagBadge(elementId, value) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const valSpan = el.querySelector('.flag-val');
  if (value === true) {
    valSpan.textContent = 'Active';
    valSpan.className = 'flag-val font-semibold text-emerald-400';
  } else if (value === false) {
    valSpan.textContent = 'Disabled';
    valSpan.className = 'flag-val font-semibold text-slate-500';
  } else {
    valSpan.textContent = '--';
    valSpan.className = 'flag-val font-semibold text-slate-400';
  }
}

// Fetch historical records and render chart, table, transitions, and analytics
async function fetchHistory(range) {
  try {
    const res = await fetch(`/api/history?range=${range}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const points = data.points || [];
    const transitions = data.transitions || [];
    const analytics = data.analytics || {};

    renderChart(points);
    renderRecentTable(points);
    renderTransitions(transitions);
    renderAnalytics(analytics, range);
  } catch (err) {
    console.error('Failed to load history:', err);
  }
}

function renderChart(points) {
  const emptyState = document.getElementById('chart-empty-state');
  const canvas = document.getElementById('historyChart');

  if (!points || points.length < 2 || typeof Chart === 'undefined') {
    emptyState.classList.remove('hidden');
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
    }
    return;
  }

  emptyState.classList.add('hidden');

  const labels = points.map(p => formatTimestamp(p.observed_at_utc));
  const balanceData = points.map(p => p.meter_balance);
  const kwhData = points.map(p => p.kwh_rec_today);

  if (historyChart) {
    historyChart.data.labels = labels;
    historyChart.data.datasets[0].data = balanceData;
    historyChart.data.datasets[1].data = kwhData;
    historyChart.update();
    return;
  }

  const ctx = canvas.getContext('2d');
  historyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Meter Balance (₹)',
          data: balanceData,
          borderColor: '#10b981', // emerald-500
          backgroundColor: 'rgba(16, 185, 129, 0.1)',
          yAxisID: 'yBalance',
          tension: 0.2,
          fill: true,
          pointRadius: points.length > 30 ? 0 : 3,
        },
        {
          label: "Today's Reported Energy (Cumulative kWh)",
          data: kwhData,
          borderColor: '#06b6d4', // cyan-500
          backgroundColor: 'rgba(6, 182, 212, 0.1)',
          yAxisID: 'yEnergy',
          tension: 0.2,
          fill: false,
          pointRadius: points.length > 30 ? 0 : 3,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          labels: { color: '#cbd5e1', font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: '#0f172a',
          titleColor: '#f8fafc',
          bodyColor: '#cbd5e1',
          borderColor: '#334155',
          borderWidth: 1,
        }
      },
      scales: {
        x: {
          ticks: { color: '#64748b', font: { size: 10 }, maxTicksLimit: 8 },
          grid: { color: '#1e293b' }
        },
        yBalance: {
          type: 'linear',
          position: 'left',
          ticks: { color: '#10b981', font: { size: 10 } },
          grid: { color: '#1e293b' },
          title: { display: true, text: 'Balance (₹)', color: '#10b981' }
        },
        yEnergy: {
          type: 'linear',
          position: 'right',
          ticks: { color: '#06b6d4', font: { size: 10 } },
          grid: { drawOnChartArea: false },
          title: { display: true, text: 'Reported Energy (kWh)', color: '#06b6d4' }
        }
      }
    }
  });
}

function formatDuration(minutes) {
  if (!minutes || minutes <= 0) return '0m';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hrs = Math.floor(minutes / 60);
  const remMins = Math.round(minutes % 60);
  return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`;
}

function renderAnalytics(analytics, range) {
  const rangeLabel = document.getElementById('analytics-range-label');
  const resEl = document.getElementById('analytics-resolution');
  const dgTimeEl = document.getElementById('analytics-dg-time');
  const dgSubEl = document.getElementById('analytics-dg-sub');
  const gridTimeEl = document.getElementById('analytics-grid-time');
  const gridSubEl = document.getElementById('analytics-grid-sub');
  const transEl = document.getElementById('analytics-transitions');
  const transSubEl = document.getElementById('analytics-transitions-sub');
  const coverageEl = document.getElementById('analytics-coverage-pct');
  const coverageSubEl = document.getElementById('analytics-coverage-sub');

  if (!rangeLabel) return;

  rangeLabel.textContent = range.toUpperCase();
  if (resEl && analytics.poll_interval_seconds) {
    resEl.textContent = analytics.poll_interval_seconds;
  }

  const dgMins = analytics.estimated_dg_minutes || 0;
  const gridMins = analytics.estimated_grid_minutes || 0;
  const gapMins = analytics.gap_minutes || 0;
  const dgSnapshots = analytics.dg_snapshots || 0;
  const gridSnapshots = analytics.grid_snapshots || 0;
  const transitionsCount = analytics.total_transitions || 0;
  const coveragePct = analytics.telemetry_coverage_pct !== undefined ? analytics.telemetry_coverage_pct : 100;
  const gridAvail = analytics.grid_availability_pct !== undefined ? analytics.grid_availability_pct : 100;

  if (dgTimeEl) dgTimeEl.textContent = formatDuration(dgMins);
  if (dgSubEl) dgSubEl.textContent = `${dgSnapshots} snaps (${(100 - gridAvail).toFixed(0)}% active)`;

  if (gridTimeEl) gridTimeEl.textContent = formatDuration(gridMins);
  if (gridSubEl) gridSubEl.textContent = `${gridSnapshots} snaps (${gridAvail.toFixed(0)}% active)`;

  if (transEl) transEl.textContent = transitionsCount;
  if (transSubEl) transSubEl.textContent = `${transitionsCount === 1 ? '1 switch' : transitionsCount + ' switches'} detected`;

  if (coverageEl) {
    coverageEl.textContent = `${coveragePct}%`;
    coverageEl.className = `text-lg font-bold mt-1 ${coveragePct < 90 ? 'text-amber-400' : 'text-cyan-400'}`;
  }
  if (coverageSubEl) {
    if (gapMins > 0) {
      coverageSubEl.textContent = `${formatDuration(gapMins)} unmonitored gap`;
      coverageSubEl.className = 'text-[10px] text-amber-400 font-medium mt-0.5';
    } else {
      coverageSubEl.textContent = 'Continuous capture';
      coverageSubEl.className = 'text-[10px] text-slate-500 mt-0.5';
    }
  }
}

// Render source transitions in historical view
function renderTransitions(transitions) {
  const container = document.getElementById('transitions-container');
  const badge = document.getElementById('transitions-count-badge');

  if (!container || !badge) return;

  const count = (transitions || []).length;
  badge.textContent = `${count} ${count === 1 ? 'transition' : 'transitions'}`;

  if (!transitions || count === 0) {
    container.innerHTML = '<p class="text-slate-500 italic py-1">No source transitions recorded during this timeframe.</p>';
    return;
  }

  // Render transitions in chronological order
  container.innerHTML = transitions.map(t => {
    const isToGrid = t.to_code === 0;
    const isToDG = t.to_code === 1;

    let icon = '⚡';
    let pillClass = 'bg-emerald-950 text-emerald-400 border border-emerald-500/30';
    if (isToDG) {
      icon = '⛽';
      pillClass = 'bg-amber-950 text-amber-400 border border-amber-500/30';
    } else if (!isToGrid) {
      icon = '❓';
      pillClass = 'bg-slate-800 text-slate-400 border border-slate-700';
    }

    const fromLabel = t.from_source ? (t.from_source.short_name || t.from_source.name) : 'Unknown';
    const toLabel = t.to_source ? (t.to_source.short_name || t.to_source.name) : 'Unknown';

    return `
      <div class="p-3 rounded-lg bg-slate-900/80 border border-slate-800 flex items-center justify-between hover:border-slate-700 transition">
        <div class="flex items-center space-x-3">
          <span class="text-lg">${icon}</span>
          <div>
            <div class="font-medium text-slate-200">
              Switched from <span class="text-slate-400 font-semibold">${fromLabel}</span> to <span class="font-bold ${isToGrid ? 'text-emerald-300' : isToDG ? 'text-amber-300' : 'text-slate-300'}">${toLabel}</span>
            </div>
            <div class="text-[11px] text-slate-500 font-sans mt-0.5">
              ${formatTimestamp(t.observed_at_utc)}
            </div>
          </div>
        </div>
        <span class="px-2.5 py-1 rounded text-[11px] font-bold ${pillClass}">
          ${toLabel}
        </span>
      </div>
    `;
  }).join('');
}

// Render recent snapshot table with power source column
function renderRecentTable(points) {
  const tbody = document.getElementById('recent-readings-tbody');
  if (!points || points.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-3 py-4 text-center text-slate-500 font-sans">No snapshots recorded yet.</td></tr>';
    return;
  }

  // Display up to 10 latest snapshots in descending order
  const recent = points.slice().reverse().slice(0, 10);
  tbody.innerHTML = recent.map(p => {
    let sourceBadge = '<span class="inline-flex px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400 border border-slate-700">Unknown</span>';
    if (p.eb_dg_status === 0) {
      sourceBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs bg-emerald-950/80 text-emerald-400 border border-emerald-500/40">⚡ Grid (EB)</span>';
    } else if (p.eb_dg_status === 1) {
      sourceBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs bg-amber-950/80 text-amber-400 border border-amber-500/40">⛽ DG</span>';
    }

    const relay = (p.relay_status || '').trim().toUpperCase();
    let relayBadge = '<span class="inline-flex px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400">UNKNOWN</span>';
    if (relay === 'ON') {
      relayBadge = '<span class="inline-flex px-2 py-0.5 rounded text-xs bg-emerald-950 text-emerald-400 border border-emerald-500/30">ON</span>';
    } else if (relay === 'OFF') {
      relayBadge = '<span class="inline-flex px-2 py-0.5 rounded text-xs bg-rose-950 text-rose-400 border border-rose-500/30">OFF</span>';
    }

    return `
      <tr class="hover:bg-slate-800/40 transition">
        <td class="px-3 py-2 text-slate-400 font-sans">${formatTimestamp(p.observed_at_utc)}</td>
        <td class="px-3 py-2">${sourceBadge}</td>
        <td class="px-3 py-2 text-emerald-400 font-medium">₹${formatCurrency(p.meter_balance)}</td>
        <td class="px-3 py-2 text-slate-200">${formatKwh(p.kwh_rec_today)}</td>
        <td class="px-3 py-2 text-slate-200">${formatKwh(p.kwh_rec_month)}</td>
        <td class="px-3 py-2">${relayBadge}</td>
      </tr>
    `;
  }).join('');
}

// Manual refresh trigger
async function triggerRefresh() {
  const btn = document.getElementById('refresh-btn');
  const icon = document.getElementById('refresh-icon');
  const label = document.getElementById('refresh-label');

  btn.disabled = true;
  icon.classList.add('animate-spin');
  label.textContent = 'Refreshing...';

  try {
    const res = await fetch('/api/refresh', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      console.warn('Manual refresh notice:', data.error);
    }
  } catch (err) {
    console.error('Refresh request error:', err);
  } finally {
    await fetchDashboard();
    await fetchHistory(currentRange);
    btn.disabled = false;
    icon.classList.remove('animate-spin');
    label.textContent = 'Refresh Now';
  }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
  // Bind range selector buttons
  const rangeButtons = document.querySelectorAll('.range-btn');
  rangeButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const selected = btn.getAttribute('data-range');
      if (selected === currentRange) return;

      rangeButtons.forEach(b => {
        b.className = 'range-btn px-3 py-1.5 rounded-md text-slate-400 hover:text-white transition';
      });
      btn.className = 'range-btn px-3 py-1.5 rounded-md text-white bg-emerald-600 transition';

      currentRange = selected;
      fetchHistory(currentRange);
    });
  });

  // Bind refresh button
  document.getElementById('refresh-btn').addEventListener('click', triggerRefresh);

  // Initial load
  fetchDashboard();
  fetchHistory(currentRange);

  // Auto-refresh UI state every 30 seconds
  pollTimer = setInterval(() => {
    fetchDashboard();
    fetchHistory(currentRange);
  }, 30000);
});
