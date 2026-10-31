// Electro Dashboard Frontend JavaScript
// Modern, minimal, and practical single-user telemetry interface

let currentRange = '24h';
let currentTab = 'overview';
let historyChart = null;
let pollTimer = null;
let cachedEvents = [];
let activeEventFilter = 'all';

// Scalable vector icons for clean UI without noisy emojis
const ICONS = {
  grid: `<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/></svg>`,
  dg: `<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>`,
  info: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
  alert: `<svg class="w-4 h-4 text-rose-500 dark:text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>`,
  warning: `<svg class="w-4 h-4 text-amber-500 dark:text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
  power: `<svg class="w-4 h-4 text-emerald-600 dark:text-emerald-400" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/></svg>`,
  relay: `<svg class="w-4 h-4 text-purple-600 dark:text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"/></svg>`,
  network: `<svg class="w-4 h-4 text-cyan-600 dark:text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0"/></svg>`,
  billing: `<svg class="w-4 h-4 text-emerald-600 dark:text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
  unknown: `<svg class="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
  switchArrow: `<svg class="w-3.5 h-3.5 text-slate-400 dark:text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>`,
};

function getAuthHeaders(existingHeaders = {}) {
  const headers = { ...existingHeaders };
  if (window.__ELECTRO_API_TOKEN__) {
    headers['Authorization'] = `Bearer ${window.__ELECTRO_API_TOKEN__}`;
  }
  return headers;
}

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

function formatDuration(minutes) {
  if (!minutes || minutes <= 0) return '0m';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hrs = Math.floor(minutes / 60);
  const remMins = Math.round(minutes % 60);
  return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`;
}

// ---------------------------------------------------------------------------
// Theme Management (Light / Dark Mode)
// ---------------------------------------------------------------------------

function getCurrentTheme() {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

function setTheme(theme) {
  const isDark = theme === 'dark';
  if (isDark) {
    document.documentElement.classList.add('dark');
    document.documentElement.classList.remove('light');
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', '#090d16');
  } else {
    document.documentElement.classList.remove('dark');
    document.documentElement.classList.add('light');
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', '#f8fafc');
  }

  try {
    localStorage.setItem('electro_theme', theme);
  } catch (e) {}

  // Update Chart.js colors if rendered
  updateChartTheme();

  // Refresh dynamic tab classes for active state
  switchTab(currentTab);

  // Re-render cached events timeline to update dynamic card styles if present
  if (cachedEvents && cachedEvents.length > 0) {
    renderEventsTimeline(cachedEvents, activeEventFilter);
  }
}

function toggleTheme() {
  const current = getCurrentTheme();
  const next = current === 'dark' ? 'light' : 'dark';
  setTheme(next);
}

function updateChartTheme() {
  if (!historyChart) return;
  const isDark = getCurrentTheme() === 'dark';

  if (historyChart.options.scales && historyChart.options.scales.x) {
    historyChart.options.scales.x.grid.color = isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.05)';
  }
  if (historyChart.options.scales && historyChart.options.scales.yBalance) {
    historyChart.options.scales.yBalance.grid.color = isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.05)';
  }
  if (historyChart.options.plugins && historyChart.options.plugins.legend && historyChart.options.plugins.legend.labels) {
    historyChart.options.plugins.legend.labels.color = isDark ? '#94a3b8' : '#475569';
  }
  if (historyChart.options.plugins && historyChart.options.plugins.tooltip) {
    historyChart.options.plugins.tooltip.backgroundColor = isDark ? 'rgba(9, 13, 22, 0.94)' : 'rgba(255, 255, 255, 0.98)';
    historyChart.options.plugins.tooltip.titleColor = isDark ? '#f8fafc' : '#0f172a';
    historyChart.options.plugins.tooltip.bodyColor = isDark ? '#cbd5e1' : '#334155';
    historyChart.options.plugins.tooltip.borderColor = isDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(203, 213, 225, 0.9)';
  }
  historyChart.update();
}

// ---------------------------------------------------------------------------
// 4-View Segmented Tab Navigation
// ---------------------------------------------------------------------------

function switchTab(targetView) {
  currentTab = targetView;
  try {
    localStorage.setItem('electro_active_tab', targetView);
  } catch (e) {}

  // Desktop header tabs
  const tabButtons = document.querySelectorAll('.tab-btn');
  tabButtons.forEach(btn => {
    const view = btn.getAttribute('data-view');
    if (view === targetView) {
      btn.className = 'tab-btn active px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all bg-white dark:bg-slate-800/90 text-slate-900 dark:text-white shadow-sm border border-slate-200 dark:border-slate-700/60';
      btn.setAttribute('aria-selected', 'true');
    } else {
      btn.className = 'tab-btn px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-200/50 dark:hover:bg-slate-800/40 border border-transparent';
      btn.setAttribute('aria-selected', 'false');
    }
  });

  // Mobile bottom dock tabs
  const mobileTabButtons = document.querySelectorAll('.mobile-tab-btn');
  mobileTabButtons.forEach(btn => {
    const view = btn.getAttribute('data-view');
    if (view === targetView) {
      btn.className = 'mobile-tab-btn active flex flex-col items-center justify-center py-1.5 px-2 rounded-xl text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/25 transition-all active:scale-95 text-[11px] font-bold shadow-sm';
      btn.setAttribute('data-active', 'true');
      btn.setAttribute('aria-selected', 'true');
    } else {
      btn.className = 'mobile-tab-btn flex flex-col items-center justify-center py-1.5 px-2 rounded-xl text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-all active:scale-95 text-[11px] font-medium border border-transparent';
      btn.setAttribute('data-active', 'false');
      btn.setAttribute('aria-selected', 'false');
    }
  });

  const panels = document.querySelectorAll('.view-panel');
  panels.forEach(panel => {
    if (panel.id === `view-${targetView}`) {
      panel.classList.remove('hidden');
      panel.classList.remove('panel-transition-in');
      void panel.offsetWidth; // Reflow to replay entrance transition
      panel.classList.add('panel-transition-in');
    } else {
      panel.classList.add('hidden');
      panel.classList.remove('panel-transition-in');
    }
  });

  if (targetView === 'analytics' && historyChart) {
    setTimeout(() => {
      historyChart.resize();
    }, 50);
  }
}

// ---------------------------------------------------------------------------
// Connection & Active Status Header
// ---------------------------------------------------------------------------

function updateConnectionStatus(status, isStale, lastSuccess, errorCategory, errorMsg, ageSeconds, nextRetry, pollInterval) {
  const dot = document.getElementById('connection-dot');
  const text = document.getElementById('connection-text');
  const banner = document.getElementById('alert-banner');
  const alertIcon = document.getElementById('alert-icon');
  const alertTitle = document.getElementById('alert-title');
  const alertBody = document.getElementById('alert-body');

  const cadenceText = pollInterval ? `${pollInterval}s cadence` : '15s';
  const ageDisplay = ageSeconds !== null && ageSeconds !== undefined ? `${Math.round(ageSeconds)}s ago` : formatTimestamp(lastSuccess);
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640;

  dot.className = 'h-2 w-2 rounded-full mr-2 sm:mr-2.5 transition-colors shrink-0';

  if (status === 'connected') {
    dot.classList.add('bg-emerald-500', 'dark:bg-emerald-400', 'beacon-dot-emerald', 'animate-beacon');
    text.textContent = isMobile ? `Live · ${ageDisplay}` : `Live (${cadenceText}) · Updated ${ageDisplay}`;
    if (banner) banner.classList.add('hidden');
  } else if (status === 'authentication_required') {
    dot.classList.add('bg-rose-500', 'dark:bg-rose-400', 'beacon-dot-rose', 'animate-pulse');
    text.textContent = nextRetry ? `Auth Retry Pending (${formatTimestamp(nextRetry)})` : 'Authentication Required';
    if (banner) {
      banner.className = 'glass-panel rounded-2xl border border-rose-300 dark:border-rose-500/40 bg-rose-50 dark:bg-rose-950/30 p-4 text-xs flex items-start space-x-3 text-rose-800 dark:text-rose-200';
      if (alertIcon) alertIcon.innerHTML = ICONS.alert;
      alertTitle.textContent = 'Authentication Required';
      alertBody.textContent = errorMsg || 'Please configure valid ELNet credentials in your local environment.';
      banner.classList.remove('hidden');
    }
  } else if (status === 'stale' || isStale) {
    dot.classList.add('bg-amber-500', 'dark:bg-amber-400', 'beacon-dot-amber', 'animate-pulse');
    text.textContent = `Stale Data · Age: ${ageDisplay}`;
    if (banner) {
      banner.className = 'glass-panel rounded-2xl border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-950/30 p-4 text-xs flex items-start space-x-3 text-amber-800 dark:text-amber-200';
      if (alertIcon) alertIcon.innerHTML = ICONS.warning;
      alertTitle.textContent = 'Connection Degraded (Stale Data)';
      alertBody.textContent = `${errorMsg || 'Upstream request failed; displaying cached snapshot data.'}${nextRetry ? ` Retrying at ${formatTimestamp(nextRetry)}.` : ''}`;
      banner.classList.remove('hidden');
    }
  } else {
    dot.classList.add('bg-slate-400', 'dark:bg-slate-500');
    text.textContent = 'Offline';
    if (banner) {
      if (errorMsg) {
        banner.className = 'glass-panel rounded-2xl border border-slate-300 dark:border-slate-700/60 bg-slate-100 dark:bg-slate-900/60 p-4 text-xs flex items-start space-x-3 text-slate-700 dark:text-slate-300';
        if (alertIcon) alertIcon.innerHTML = ICONS.info;
        alertTitle.textContent = 'Service Offline';
        alertBody.textContent = errorMsg;
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Power Source Hero Indicator
// ---------------------------------------------------------------------------

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
    // Grid (EB)
    if (heroCard) heroCard.className = 'metric-card p-4 sm:p-5 shadow-xl transition-all border border-emerald-300 dark:border-emerald-500/30 bg-gradient-to-b from-emerald-50/50 via-white to-slate-50/90 dark:from-emerald-950/20 dark:via-slate-900/80 dark:to-slate-950/90 space-y-4 rounded-2xl';
    if (pillEl) pillEl.className = 'px-2.5 py-1 rounded-lg text-xs font-semibold flex items-center gap-1.5 bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-500/30 shadow-sm';
    if (pillDot) pillDot.className = 'h-2 w-2 rounded-full bg-emerald-500 dark:bg-emerald-400 beacon-dot-emerald animate-beacon';
    if (titleEl) titleEl.textContent = 'Main Grid Active';
    if (subtagEl) subtagEl.textContent = 'EBDGStatus: 0 (Grid)';
    if (descEl) descEl.textContent = 'Main grid electricity supply active';
    if (pillText) pillText.textContent = 'Main Source (EB)';
    if (iconEl) iconEl.innerHTML = ICONS.grid;
  } else if (ebDgStatus === 1) {
    // Generator (DG)
    if (heroCard) heroCard.className = 'metric-card p-4 sm:p-5 shadow-xl transition-all border border-amber-300 dark:border-amber-500/30 bg-gradient-to-b from-amber-50/50 via-white to-slate-50/90 dark:from-amber-950/20 dark:via-slate-900/80 dark:to-slate-950/90 space-y-4 rounded-2xl';
    if (pillEl) pillEl.className = 'px-2.5 py-1 rounded-lg text-xs font-semibold flex items-center gap-1.5 bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-500/30 shadow-sm animate-pulse';
    if (pillDot) pillDot.className = 'h-2 w-2 rounded-full bg-amber-500 dark:bg-amber-400 beacon-dot-amber';
    if (titleEl) titleEl.textContent = 'DG Backup Active';
    if (subtagEl) subtagEl.textContent = 'EBDGStatus: 1 (DG)';
    if (descEl) descEl.textContent = 'Diesel generator backup active';
    if (pillText) pillText.textContent = 'Generator (DG)';
    if (iconEl) iconEl.innerHTML = ICONS.dg;
  } else {
    // Unknown
    if (heroCard) heroCard.className = 'metric-card p-4 sm:p-5 shadow-xl transition-all border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/60 space-y-4 rounded-2xl';
    if (pillEl) pillEl.className = 'px-2.5 py-1 rounded-lg text-xs font-semibold flex items-center gap-1.5 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700';
    if (pillDot) pillDot.className = 'h-2 w-2 rounded-full bg-slate-400';
    if (titleEl) titleEl.textContent = 'Source Unknown';
    if (subtagEl) subtagEl.textContent = `EBDGStatus: ${ebDgStatus !== null && ebDgStatus !== undefined ? ebDgStatus : 'null'}`;
    if (descEl) descEl.textContent = 'EBDGStatus reading is null or unverified';
    if (pillText) pillText.textContent = 'Unknown';
    if (iconEl) iconEl.innerHTML = ICONS.unknown;
  }
}

function updateRelayStatus(relayStatus) {
  const relayPill = document.getElementById('relay-pill');
  const metricRelay = document.getElementById('metric-relay');

  const s = (relayStatus || '').trim().toUpperCase();

  if (s === 'ON') {
    if (relayPill) {
      relayPill.textContent = 'CLOSED (ON)';
      relayPill.className = 'font-bold text-emerald-600 dark:text-emerald-400 font-mono';
    }
    if (metricRelay) {
      metricRelay.textContent = 'CONNECTED (ON)';
      metricRelay.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-bold font-mono tracking-wider bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-500/30';
    }
  } else if (s === 'OFF') {
    if (relayPill) {
      relayPill.textContent = 'OPEN (OFF)';
      relayPill.className = 'font-bold text-rose-600 dark:text-rose-400 font-mono';
    }
    if (metricRelay) {
      metricRelay.textContent = 'DISCONNECTED (OFF)';
      metricRelay.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-bold font-mono tracking-wider bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-400 border border-rose-200 dark:border-rose-500/30';
    }
  } else {
    const val = s || 'UNKNOWN';
    if (relayPill) {
      relayPill.textContent = val;
      relayPill.className = 'font-bold text-slate-500 dark:text-slate-400 font-mono';
    }
    if (metricRelay) {
      metricRelay.textContent = val;
      metricRelay.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-bold font-mono tracking-wider bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700';
    }
  }
}

// ---------------------------------------------------------------------------
// Telemetry Snapshots & Dashboard Data Fetch
// ---------------------------------------------------------------------------

async function fetchDashboard() {
  try {
    const res = await fetch('/api/dashboard', { headers: getAuthHeaders() });
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
      updatePowerSourceIndicator(snapshot.eb_dg_status);
      updateRelayStatus(snapshot.relay_status);

      const balEl = document.getElementById('metric-balance');
      if (balEl) balEl.textContent = formatCurrency(snapshot.meter_balance);

      const kwhEl = document.getElementById('metric-kwh-today');
      if (kwhEl) kwhEl.textContent = formatKwh(snapshot.kwh_rec_today);

      // Today's Electricity Spend Widget (in Rs.)
      const totalSpend = snapshot.total_charges_today !== undefined ? snapshot.total_charges_today :
        ((snapshot.current_day_eb || 0) + (snapshot.current_day_dg || 0) + (snapshot.current_day_solar || 0) + (snapshot.fixed_charges_today || 0));

      const wSpend = document.getElementById('widget-today-spend');
      if (wSpend) wSpend.textContent = formatCurrency(totalSpend);

      const wSpendEb = document.getElementById('widget-spend-eb');
      if (wSpendEb) wSpendEb.textContent = `₹${formatCurrency(snapshot.current_day_eb || 0)}`;

      const wSpendDg = document.getElementById('widget-spend-dg');
      if (wSpendDg) wSpendDg.textContent = `₹${formatCurrency(snapshot.current_day_dg || 0)}`;

      const wKwh = document.getElementById('widget-today-kwh');
      if (wKwh) wKwh.textContent = `${formatKwh(snapshot.kwh_rec_today)} kWh`;

      const metricSpendSub = document.getElementById('metric-today-spend-sub');
      if (metricSpendSub) metricSpendSub.textContent = `₹${formatCurrency(totalSpend)}`;

      // Previous Day Relativity
      const prevDay = snapshot.previous_day || data.previous_day;
      const prevPill = document.getElementById('metric-kwh-yesterday-pill');
      const prevKwhEl = document.getElementById('metric-kwh-yesterday');
      const prevSpendSub = document.getElementById('metric-yesterday-spend-sub');

      if (prevDay && prevDay.kwh !== null && prevDay.kwh !== undefined) {
        if (prevKwhEl) prevKwhEl.textContent = formatKwh(prevDay.kwh);
        if (prevPill) prevPill.classList.remove('hidden');
        if (prevSpendSub) prevSpendSub.textContent = `Yesterday: ₹${formatCurrency(prevDay.spend || 0)}`;
      } else {
        if (prevPill) prevPill.classList.add('hidden');
        if (prevSpendSub) prevSpendSub.textContent = 'Today';
      }

      // Month to Date (Card 4)
      const kwhMonthEl = document.getElementById('metric-kwh-month');
      if (kwhMonthEl) kwhMonthEl.textContent = formatKwh(snapshot.kwh_rec_month || 0);

      const totalMonthSpend = (snapshot.current_month_eb || 0) + (snapshot.current_month_dg || 0) + (snapshot.current_month_solar || 0) + (snapshot.fixed_charges_month || 0);
      const metricMonthSpendSub = document.getElementById('metric-month-spend-sub');
      if (metricMonthSpendSub) metricMonthSpendSub.textContent = `₹${formatCurrency(totalMonthSpend)}`;

      // Charges breakdown (Supply view)
      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val !== null && val !== undefined ? `₹${formatCurrency(val)}` : '--';
      };
      setVal('charge-today-eb', snapshot.current_day_eb);
      setVal('charge-today-dg', snapshot.current_day_dg);
      setVal('charge-today-solar', snapshot.current_day_solar);
      setVal('charge-today-fixed', snapshot.fixed_charges_today);

      setVal('charge-month-eb', snapshot.current_month_eb);
      setVal('charge-month-dg', snapshot.current_month_dg);
      setVal('charge-month-solar', snapshot.current_month_solar);
      setVal('charge-month-fixed', snapshot.fixed_charges_month);

      // Flags (Supply view)
      setFlagBadge('flag-eb', snapshot.is_eb);
      setFlagBadge('flag-dg', snapshot.is_dg);
      setFlagBadge('flag-solar', snapshot.is_solar);
      setFlagBadge('flag-fixed', snapshot.is_fixed_charge);

      const mIdEl = document.getElementById('meter-id-label');
      if (mIdEl) mIdEl.textContent = snapshot.meter_id || '--';
    }
  } catch (err) {
    console.error('Failed to load dashboard:', err);
  }
}

function setFlagBadge(elementId, value) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const valSpan = el.querySelector('.flag-val');
  if (!valSpan) return;
  if (value === true) {
    valSpan.textContent = 'Active';
    valSpan.className = 'flag-val font-semibold text-emerald-400 font-mono';
  } else if (value === false) {
    valSpan.textContent = 'Disabled';
    valSpan.className = 'flag-val font-semibold text-slate-500 font-mono';
  } else {
    valSpan.textContent = '--';
    valSpan.className = 'flag-val font-semibold text-slate-400 font-mono';
  }
}

// ---------------------------------------------------------------------------
// Balance Runway & Burn Rate Forecasting (Phase 3 Intelligence)
// ---------------------------------------------------------------------------

async function fetchForecast(range = '24h') {
  try {
    const res = await fetch(`/api/forecast?range=${range}`, { headers: getAuthHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const fc = await res.json();

    // 1. Overview card metrics
    const runwayDaysEl = document.getElementById('metric-runway-days');
    const burnDailyEl = document.getElementById('metric-burn-daily');
    const horizonSubEl = document.getElementById('metric-horizon-sub');

    if (runwayDaysEl) {
      if (fc.runway_days !== null && fc.runway_days !== undefined) {
        runwayDaysEl.textContent = fc.runway_days.toFixed(1);
      } else {
        runwayDaysEl.textContent = '--';
      }
    }

    if (burnDailyEl) {
      if (fc.daily_burn_rate > 0) {
        burnDailyEl.textContent = `₹${fc.daily_burn_rate.toFixed(2)}/day`;
      } else {
        burnDailyEl.textContent = '--';
      }
    }

    if (horizonSubEl) {
      if (fc.days_to_threshold !== null && fc.days_to_threshold !== undefined) {
        horizonSubEl.textContent = `> ₹500: ~${fc.days_to_threshold.toFixed(0)}d left`;
      } else {
        horizonSubEl.textContent = '';
      }
    }

    // 2. Analytics view detailed forecast card
    const rangeBadge = document.getElementById('forecast-range-badge');
    if (rangeBadge) rangeBadge.textContent = range.toUpperCase();

    const setEl = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };

    setEl('analytics-burn-hourly', fc.hourly_burn_rate > 0 ? `₹${fc.hourly_burn_rate.toFixed(2)}` : '--');
    setEl('analytics-burn-daily', fc.daily_burn_rate > 0 ? `₹${fc.daily_burn_rate.toFixed(2)}` : '--');
    setEl('analytics-runway-days', fc.runway_days !== null && fc.runway_days !== undefined ? `${fc.runway_days.toFixed(1)} days` : '--');
    setEl('analytics-runway-hours', fc.runway_hours !== null && fc.runway_hours !== undefined ? `${fc.runway_hours.toFixed(0)} hours` : '--');

    if (fc.days_to_threshold !== null && fc.days_to_threshold !== undefined) {
      setEl('analytics-threshold-days', `${fc.days_to_threshold.toFixed(1)} days`);
      setEl('analytics-threshold-date', fc.projected_threshold_utc ? formatTimestamp(fc.projected_threshold_utc) : '--');
    } else {
      setEl('analytics-threshold-days', '--');
      setEl('analytics-threshold-date', '--');
    }

    setEl('analytics-depletion-date', fc.projected_depletion_utc ? formatTimestamp(fc.projected_depletion_utc) : '--');
    setEl('analytics-sample-window', fc.sample_hours ? `${fc.sample_hours.toFixed(1)} hrs` : '--');
    setEl('analytics-sample-count', fc.sample_points || 0);

  } catch (err) {
    console.error('Failed to load forecast:', err);
  }
}

// ---------------------------------------------------------------------------
// Unified Chronological Events Stream (Phase 2 Event Stream)
// ---------------------------------------------------------------------------

async function fetchEvents(limit = 100) {
  try {
    const res = await fetch(`/api/events?limit=${limit}`, { headers: getAuthHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cachedEvents = data.events || [];

    const badge = document.getElementById('events-count-badge');
    if (badge) badge.textContent = cachedEvents.length;

    const mobileBadge = document.getElementById('mobile-events-badge');
    if (mobileBadge) {
      mobileBadge.textContent = cachedEvents.length;
      if (cachedEvents.length > 0) {
        mobileBadge.classList.remove('hidden');
      } else {
        mobileBadge.classList.add('hidden');
      }
    }

    renderEventsTimeline(cachedEvents, activeEventFilter);
  } catch (err) {
    console.error('Failed to load events:', err);
  }
}

function renderEventsTimeline(events, filterCategory = 'all') {
  const container = document.getElementById('events-timeline-container');
  if (!container) return;

  let filtered = events;
  if (filterCategory && filterCategory !== 'all') {
    filtered = events.filter(e => e.category === filterCategory || (filterCategory === 'Power' && e.type === 'source_change'));
  }

  if (!filtered || filtered.length === 0) {
    container.innerHTML = `
      <div class="py-10 text-center text-slate-400 dark:text-slate-500 font-sans">
        <div class="h-10 w-10 mx-auto mb-2.5 rounded-xl bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-center justify-center text-slate-500 dark:text-slate-400">
          ${ICONS.info}
        </div>
        <p class="font-medium text-slate-800 dark:text-slate-300 text-xs">No events found in this category</p>
        <p class="text-[11px] text-slate-500 mt-0.5">Power switches, contactor trips, and balance changes appear here automatically.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map(ev => {
    let borderClass = 'border-slate-200 dark:border-slate-800/80 bg-white/70 dark:bg-slate-900/40 hover:border-slate-300 dark:hover:border-slate-700/80';
    let badgeClass = 'bg-slate-100 dark:bg-slate-800/80 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700/60';
    let iconSvg = ICONS.info;

    if (ev.severity === 'alert') {
      borderClass = 'border-rose-200 dark:border-rose-500/30 bg-rose-50/70 dark:bg-rose-950/15 hover:border-rose-300 dark:hover:border-rose-500/50';
      badgeClass = 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-600/40';
      iconSvg = ICONS.alert;
    } else if (ev.severity === 'warning') {
      borderClass = 'border-amber-200 dark:border-amber-500/30 bg-amber-50/70 dark:bg-amber-950/15 hover:border-amber-300 dark:hover:border-amber-500/50';
      badgeClass = 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-600/40';
      iconSvg = ICONS.warning;
    } else {
      if (ev.category === 'Power') iconSvg = ICONS.power;
      else if (ev.category === 'Relay') iconSvg = ICONS.relay;
      else if (ev.category === 'Network') iconSvg = ICONS.network;
      else if (ev.category === 'Billing') iconSvg = ICONS.billing;
      borderClass = 'border-slate-200 dark:border-slate-800/80 bg-white/70 dark:bg-slate-900/40 hover:border-slate-300 dark:hover:border-slate-700/80';
      badgeClass = 'bg-slate-100 dark:bg-slate-800/80 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700/60';
    }

    return `
      <div class="p-3.5 rounded-xl border ${borderClass} flex flex-col sm:flex-row sm:items-center justify-between gap-3 transition-colors shadow-sm">
        <div class="flex items-start space-x-3">
          <div class="mt-0.5 p-1 rounded-lg bg-slate-100 dark:bg-slate-950/60 border border-slate-200 dark:border-slate-800/80 flex items-center justify-center shrink-0">
            ${iconSvg}
          </div>
          <div>
            <div class="flex items-center gap-2">
              <span class="font-semibold text-slate-900 dark:text-slate-100 text-xs">${ev.title}</span>
              <span class="text-[9px] uppercase font-mono font-bold tracking-wider px-2 py-0.2 rounded-full border ${badgeClass}">
                ${ev.category}
              </span>
            </div>
            <p class="text-[11px] text-slate-600 dark:text-slate-400 mt-0.5 leading-relaxed">${ev.details}</p>
          </div>
        </div>
        <div class="text-right sm:self-center flex sm:flex-col items-center sm:items-end justify-between text-[11px] text-slate-400 dark:text-slate-500 font-mono shrink-0">
          <span>${formatTimestamp(ev.timestamp)}</span>
        </div>
      </div>
    `;
  }).join('');
}

// ---------------------------------------------------------------------------
// Historical Records, Chart & Analytics (Analytics & Supply Views)
// ---------------------------------------------------------------------------

async function fetchHistory(range) {
  try {
    const res = await fetch(`/api/history?range=${range}`, { headers: getAuthHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const points = data.points || [];
    const transitions = data.transitions || [];
    const analytics = data.analytics || {};

    renderChart(points);
    renderRecentTable(points, 'recent-readings-tbody', 10);
    renderRecentTable(points, 'events-recent-tbody', 10);
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
    if (emptyState) emptyState.classList.remove('hidden');
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
    }
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

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
  const isDark = getCurrentTheme() === 'dark';
  const gridColor = isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.05)';
  const legendColor = isDark ? '#94a3b8' : '#475569';
  const tooltipBg = isDark ? 'rgba(9, 13, 22, 0.94)' : 'rgba(255, 255, 255, 0.98)';
  const tooltipTitle = isDark ? '#f8fafc' : '#0f172a';
  const tooltipBody = isDark ? '#cbd5e1' : '#334155';
  const tooltipBorder = isDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(203, 213, 225, 0.9)';

  // Gradient fill for Prepaid Balance
  const gradientBalance = ctx.createLinearGradient(0, 0, 0, 260);
  gradientBalance.addColorStop(0, isDark ? 'rgba(16, 185, 129, 0.22)' : 'rgba(16, 185, 129, 0.18)');
  gradientBalance.addColorStop(1, 'rgba(16, 185, 129, 0.00)');

  // Gradient fill for Cumulative Energy
  const gradientEnergy = ctx.createLinearGradient(0, 0, 0, 260);
  gradientEnergy.addColorStop(0, isDark ? 'rgba(6, 182, 212, 0.15)' : 'rgba(6, 182, 212, 0.12)');
  gradientEnergy.addColorStop(1, 'rgba(6, 182, 212, 0.00)');

  historyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Prepaid Balance (₹)',
          data: balanceData,
          borderColor: '#10b981', // emerald-500
          borderWidth: 2,
          backgroundColor: gradientBalance,
          yAxisID: 'yBalance',
          tension: 0.25,
          fill: true,
          pointRadius: points.length > 40 ? 0 : 2.5,
          pointHoverRadius: 5,
          pointBackgroundColor: '#10b981',
        },
        {
          label: "Today's Reported Energy (kWh)",
          data: kwhData,
          borderColor: '#06b6d4', // cyan-500
          borderWidth: 2,
          backgroundColor: gradientEnergy,
          yAxisID: 'yEnergy',
          tension: 0.25,
          fill: false,
          pointRadius: points.length > 40 ? 0 : 2.5,
          pointHoverRadius: 5,
          pointBackgroundColor: '#06b6d4',
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
          position: 'top',
          align: 'end',
          labels: {
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: 'circle',
            color: legendColor,
            font: { size: 11, family: 'var(--font-sans)', weight: '500' },
            padding: 14,
          }
        },
        tooltip: {
          backgroundColor: tooltipBg,
          titleColor: tooltipTitle,
          bodyColor: tooltipBody,
          borderColor: tooltipBorder,
          borderWidth: 1,
          padding: 10,
          cornerRadius: 8,
          titleFont: { size: 11, family: 'var(--font-sans)', weight: '600' },
          bodyFont: { size: 11, family: 'var(--font-mono)' },
          boxPadding: 4,
          usePointStyle: true,
        }
      },
      scales: {
        x: {
          ticks: {
            color: '#64748b',
            font: { size: 10, family: 'var(--font-mono)' },
            maxTicksLimit: 7,
            maxRotation: 0,
          },
          grid: { color: gridColor }
        },
        yBalance: {
          type: 'linear',
          position: 'left',
          ticks: {
            color: '#10b981',
            font: { size: 10, family: 'var(--font-mono)' },
            callback: (v) => `₹${v}`
          },
          grid: { color: gridColor },
          title: { display: false }
        },
        yEnergy: {
          type: 'linear',
          position: 'right',
          ticks: {
            color: '#06b6d4',
            font: { size: 10, family: 'var(--font-mono)' },
            callback: (v) => `${v} kWh`
          },
          grid: { drawOnChartArea: false },
          title: { display: false }
        }
      }
    }
  });
}

function renderAnalytics(analytics, range) {
  if (!analytics) return;

  const dgMins = analytics.estimated_dg_minutes || 0;
  const gridMins = analytics.estimated_grid_minutes || 0;
  const gapMins = analytics.gap_minutes || 0;
  const dgSnapshots = analytics.dg_snapshots || 0;
  const gridSnapshots = analytics.grid_snapshots || 0;
  const transitionsCount = analytics.total_transitions || 0;
  const coveragePct = analytics.telemetry_coverage_pct !== undefined ? analytics.telemetry_coverage_pct : 100;
  const gridAvail = analytics.grid_availability_pct !== undefined ? analytics.grid_availability_pct : 100;
  const resSecs = analytics.poll_interval_seconds || 15;

  const setText = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };

  // 1. Overview Main Page Elements
  setText('overview-resolution', resSecs);
  setText('overview-dg-time', formatDuration(dgMins));
  setText('overview-dg-sub', `${dgSnapshots} snaps (${(100 - gridAvail).toFixed(0)}% active)`);
  setText('overview-grid-time', formatDuration(gridMins));
  setText('overview-grid-sub', `${gridSnapshots} snaps (${gridAvail.toFixed(0)}% active)`);
  setText('overview-transitions', transitionsCount);
  setText('overview-transitions-sub', `${transitionsCount === 1 ? '1 switch' : transitionsCount + ' switches'} detected`);

  const overviewCov = document.getElementById('overview-coverage-pct');
  if (overviewCov) {
    overviewCov.textContent = `${coveragePct}%`;
    overviewCov.className = `text-lg font-bold mt-1 font-mono tabular-nums ${coveragePct < 90 ? 'text-amber-400' : 'text-cyan-400'}`;
  }
  const overviewCovSub = document.getElementById('overview-coverage-sub');
  if (overviewCovSub) {
    overviewCovSub.textContent = gapMins > 0 ? `${formatDuration(gapMins)} unmonitored gap` : 'Continuous capture';
    overviewCovSub.className = gapMins > 0 ? 'text-[10px] text-amber-400 font-medium mt-0.5 font-mono' : 'text-[10px] text-slate-500 mt-0.5 font-mono';
  }

  // 2. Supply Tab Elements
  const rangeLabel = document.getElementById('analytics-range-label');
  if (rangeLabel) rangeLabel.textContent = (range || '24h').toUpperCase();

  setText('analytics-resolution', resSecs);
  setText('analytics-dg-time', formatDuration(dgMins));
  setText('analytics-dg-sub', `${dgSnapshots} snaps (${(100 - gridAvail).toFixed(0)}% active)`);
  setText('analytics-grid-time', formatDuration(gridMins));
  setText('analytics-grid-sub', `${gridSnapshots} snaps (${gridAvail.toFixed(0)}% active)`);
  setText('analytics-transitions', transitionsCount);
  setText('analytics-transitions-sub', `${transitionsCount === 1 ? '1 switch' : transitionsCount + ' switches'} detected`);

  const coverageEl = document.getElementById('analytics-coverage-pct');
  if (coverageEl) {
    coverageEl.textContent = `${coveragePct}%`;
    coverageEl.className = `text-lg font-bold mt-1 font-mono tabular-nums ${coveragePct < 90 ? 'text-amber-400' : 'text-cyan-400'}`;
  }
  const coverageSubEl = document.getElementById('analytics-coverage-sub');
  if (coverageSubEl) {
    coverageSubEl.textContent = gapMins > 0 ? `${formatDuration(gapMins)} unmonitored gap` : 'Continuous capture';
    coverageSubEl.className = gapMins > 0 ? 'text-[10px] text-amber-400 font-medium mt-0.5 font-mono' : 'text-[10px] text-slate-500 mt-0.5 font-mono';
  }

  // Update dynamic supply bar widths
  const gridBar = document.getElementById('supply-bar-grid');
  const dgBar = document.getElementById('supply-bar-dg');
  if (gridBar && dgBar) {
    const gridPct = Math.max(0, Math.min(100, gridAvail));
    const dgPct = 100 - gridPct;
    gridBar.style.width = `${gridPct.toFixed(1)}%`;
    dgBar.style.width = `${dgPct.toFixed(1)}%`;
  }

  // 3. 24-Hour Running Power Source Timeline Bar
  if (analytics.timeline_24h) {
    render24hTimeline(analytics.timeline_24h, transitionsCount);
  }
}

function render24hTimeline(timeline, totalTransitions) {
  if (!timeline) return;

  // 1. High-Resolution Visual Multi-Segment Timeline Ribbon
  const visualBar = document.getElementById('timeline-visual-bar');
  const touchDetail = document.getElementById('timeline-touch-detail');
  const touchText = document.getElementById('timeline-touch-text');
  const touchClose = document.getElementById('timeline-touch-close');

  if (touchClose && touchDetail) {
    touchClose.onclick = (e) => {
      e.stopPropagation();
      touchDetail.classList.add('hidden');
    };
  }

  if (visualBar && timeline.intervals && timeline.intervals.length > 0) {
    visualBar.innerHTML = timeline.intervals.map(int => {
      let bg = 'bg-emerald-500 shadow-sm shadow-emerald-950/40';
      let name = 'Main Grid (EB)';
      let badgeColor = 'text-emerald-400';
      if (int.source === 'DG') {
        bg = 'bg-amber-400 shadow-sm shadow-amber-950/40 animate-pulse';
        name = 'Backup Generator (DG)';
        badgeColor = 'text-amber-400';
      } else if (int.source === 'GAP') {
        bg = 'bg-slate-800/80';
        name = 'Unmonitored Gap';
        badgeColor = 'text-slate-400';
      } else if (int.source === 'UNKNOWN') {
        bg = 'bg-slate-700';
        name = 'Unknown';
        badgeColor = 'text-slate-400';
      }
      const pct = Math.max(0.75, int.pct);
      const startStr = formatTimestamp(int.start_utc);
      const endStr = formatTimestamp(int.end_utc);
      const title = `${name}: ${formatDuration(int.duration_minutes)} (${startStr} → ${endStr})`;
      const touchHtml = `<span class="${badgeColor} font-bold">${name}</span>: ${formatDuration(int.duration_minutes)} <span class="text-slate-500">(${startStr} → ${endStr})</span>`;
      return `<div class="${bg} h-full rounded-sm transition-all hover:brightness-125 hover:scale-y-110 cursor-pointer" style="width: ${pct}%" title="${title}" data-touch="${encodeURIComponent(touchHtml)}"></div>`;
    }).join('');

    // Touch & click interaction to inspect segment on mobile
    visualBar.onclick = (e) => {
      const seg = e.target.closest('[data-touch]');
      if (seg && touchDetail && touchText) {
        touchText.innerHTML = decodeURIComponent(seg.getAttribute('data-touch'));
        touchDetail.classList.remove('hidden');
      }
    };
  }

  // 2. Vitals & Legend
  const gridTimeEl = document.getElementById('timeline-grid-time');
  const gridPctEl = document.getElementById('timeline-grid-pct');
  const dgTimeEl = document.getElementById('timeline-dg-time');
  const dgPctEl = document.getElementById('timeline-dg-pct');
  const switchesBadge = document.getElementById('timeline-switches-badge');

  if (gridTimeEl) gridTimeEl.textContent = formatDuration((timeline.grid_hours || 0) * 60);
  if (gridPctEl) gridPctEl.textContent = `${timeline.grid_pct !== undefined ? timeline.grid_pct : 100}%`;
  if (dgTimeEl) dgTimeEl.textContent = formatDuration((timeline.dg_hours || 0) * 60);
  if (dgPctEl) dgPctEl.textContent = `${timeline.dg_pct !== undefined ? timeline.dg_pct : 0}%`;
  if (switchesBadge) {
    const sw = totalTransitions || 0;
    switchesBadge.textContent = `${sw} ${sw === 1 ? 'switch' : 'switches'} in 24h`;
  }
}

function renderTransitions(transitions) {
  const container = document.getElementById('transitions-container');
  const badge = document.getElementById('transitions-count-badge');

  if (!container || !badge) return;

  const count = (transitions || []).length;
  badge.textContent = `${count} ${count === 1 ? 'transition' : 'transitions'}`;

  if (!transitions || count === 0) {
    container.innerHTML = '<p class="text-slate-400 dark:text-slate-500 italic py-2 text-xs">No power source transitions recorded during this timeframe.</p>';
    return;
  }

  container.innerHTML = transitions.map(t => {
    const isToGrid = t.to_code === 0;
    const isToDG = t.to_code === 1;

    let iconSvg = ICONS.power;
    let pillClass = 'bg-emerald-50 dark:bg-emerald-950/80 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-500/30';
    if (isToDG) {
      iconSvg = ICONS.dg;
      pillClass = 'bg-amber-50 dark:bg-amber-950/80 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-500/30';
    } else if (!isToGrid) {
      iconSvg = ICONS.unknown;
      pillClass = 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-400 border border-slate-200 dark:border-slate-700';
    }

    const fromLabel = t.from_source ? (t.from_source.short_name || t.from_source.name) : 'Unknown';
    const toLabel = t.to_source ? (t.to_source.short_name || t.to_source.name) : 'Unknown';

    return `
      <div class="p-3 rounded-xl bg-white/70 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800/80 flex items-center justify-between hover:border-slate-300 dark:hover:border-slate-700/80 transition-colors shadow-sm">
        <div class="flex items-center space-x-3">
          <div class="p-1.5 rounded-lg bg-slate-100 dark:bg-slate-950/80 border border-slate-200 dark:border-slate-800/80 flex items-center justify-center shrink-0">
            ${iconSvg}
          </div>
          <div>
            <div class="text-xs font-medium text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
              <span>Switched from</span>
              <span class="text-slate-500 dark:text-slate-400 font-semibold font-mono">${fromLabel}</span>
              <span class="text-slate-400 dark:text-slate-600">→</span>
              <span class="font-bold font-mono ${isToGrid ? 'text-emerald-600 dark:text-emerald-300' : isToDG ? 'text-amber-600 dark:text-amber-300' : 'text-slate-700 dark:text-slate-300'}">${toLabel}</span>
            </div>
            <div class="text-[11px] text-slate-400 dark:text-slate-500 font-mono mt-0.5">
              ${formatTimestamp(t.observed_at_utc)}
            </div>
          </div>
        </div>
        <span class="px-2.5 py-0.5 rounded-md text-[10px] font-bold font-mono ${pillClass}">
          ${toLabel}
        </span>
      </div>
    `;
  }).join('');
}

function renderRecentTable(points, tbodyId = 'recent-readings-tbody', limit = 10) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;

  if (!points || points.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-3.5 py-4 text-center text-slate-400 dark:text-slate-500 font-sans">No snapshots recorded yet.</td></tr>';
    return;
  }

  const recent = points.slice().reverse().slice(0, limit);
  tbody.innerHTML = recent.map(p => {
    let sourceBadge = '<span class="inline-flex px-2 py-0.5 rounded text-[11px] bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-400 border border-slate-200 dark:border-slate-700 font-mono">Unknown</span>';
    if (p.eb_dg_status === 0) {
      sourceBadge = '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-emerald-50 dark:bg-emerald-950/80 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-500/30 font-mono font-medium"><span class="h-1.5 w-1.5 rounded-full bg-emerald-500 dark:bg-emerald-400"></span>Grid (EB)</span>';
    } else if (p.eb_dg_status === 1) {
      sourceBadge = '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-amber-50 dark:bg-amber-950/80 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-500/30 font-mono font-medium"><span class="h-1.5 w-1.5 rounded-full bg-amber-500 dark:bg-amber-400"></span>DG Backup</span>';
    }

    const relay = (p.relay_status || '').trim().toUpperCase();
    let relayBadge = '<span class="inline-flex px-2 py-0.5 rounded text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 font-mono">UNKNOWN</span>';
    if (relay === 'ON') {
      relayBadge = '<span class="inline-flex px-2 py-0.5 rounded text-[10px] bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-500/30 font-mono font-bold">ON</span>';
    } else if (relay === 'OFF') {
      relayBadge = '<span class="inline-flex px-2 py-0.5 rounded text-[10px] bg-rose-50 dark:bg-rose-950 text-rose-700 dark:text-rose-400 border border-rose-200 dark:border-rose-500/30 font-mono font-bold">OFF</span>';
    }

    return `
      <tr class="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
        <td class="px-3 sm:px-3.5 py-2 sm:py-2.5 text-slate-500 dark:text-slate-400 font-mono tabular-nums text-[11px] sm:text-xs">${formatTimestamp(p.observed_at_utc)}</td>
        <td class="px-3 sm:px-3.5 py-2 sm:py-2.5">${sourceBadge}</td>
        <td class="px-3 sm:px-3.5 py-2 sm:py-2.5 text-emerald-600 dark:text-emerald-400 font-semibold font-mono tabular-nums text-[11px] sm:text-xs">₹${formatCurrency(p.meter_balance)}</td>
        <td class="px-3 sm:px-3.5 py-2 sm:py-2.5 text-slate-800 dark:text-slate-200 font-mono tabular-nums text-[11px] sm:text-xs">${formatKwh(p.kwh_rec_today)}</td>
        <td class="px-3 sm:px-3.5 py-2 sm:py-2.5 text-slate-800 dark:text-slate-200 font-mono tabular-nums text-[11px] sm:text-xs">${formatKwh(p.kwh_rec_month)}</td>
        <td class="px-3 sm:px-3.5 py-2 sm:py-2.5">${relayBadge}</td>
      </tr>
    `;
  }).join('');
}

// ---------------------------------------------------------------------------
// Manual Refresh Trigger
// ---------------------------------------------------------------------------

async function triggerRefresh() {
  const btn = document.getElementById('refresh-btn');
  const icon = document.getElementById('refresh-icon');
  const label = document.getElementById('refresh-label');

  if (btn) btn.disabled = true;
  if (icon) icon.classList.add('animate-spin');
  if (label) label.textContent = 'Refreshing...';

  try {
    const res = await fetch('/api/refresh', { method: 'POST', headers: getAuthHeaders() });
    const data = await res.json();
    if (!res.ok) {
      console.warn('Manual refresh notice:', data.error);
    }
  } catch (err) {
    console.error('Refresh request error:', err);
  } finally {
    await fetchDashboard();
    await fetchHistory(currentRange);
    await fetchForecast(currentRange);
    await fetchEvents();
    if (btn) btn.disabled = false;
    if (icon) icon.classList.remove('animate-spin');
    if (label) label.textContent = 'Refresh Now';
  }
}

// ---------------------------------------------------------------------------
// Initialization on Page Load
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  // 1. Tab buttons (Desktop tabs & Mobile bottom dock)
  const tabButtons = document.querySelectorAll('.tab-btn, .mobile-tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.getAttribute('data-view');
      switchTab(view);
    });
  });

  // Check URL hash or localStorage for active tab
  const hash = window.location.hash.replace('#', '');
  const savedTab = localStorage.getItem('electro_active_tab');
  if (hash && ['overview', 'analytics', 'supply', 'history'].includes(hash)) {
    switchTab(hash);
  } else if (savedTab && ['overview', 'analytics', 'supply', 'history'].includes(savedTab)) {
    switchTab(savedTab);
  }

  // Quick link in Events to view full history in Analytics
  const eventsAnalyticsLink = document.getElementById('events-view-analytics');
  if (eventsAnalyticsLink) {
    eventsAnalyticsLink.addEventListener('click', () => {
      switchTab('analytics');
    });
  }

  // 2. Event filter buttons
  const filterBtns = document.querySelectorAll('.event-filter-btn');
  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const f = btn.getAttribute('data-filter');
      activeEventFilter = f;
      filterBtns.forEach(b => {
        b.className = 'event-filter-btn px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg text-[11px] sm:text-xs font-medium bg-white dark:bg-slate-900/80 text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 border border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 transition whitespace-nowrap';
      });
      btn.className = 'event-filter-btn px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg text-[11px] sm:text-xs font-semibold bg-emerald-600 text-white shadow-sm border border-emerald-500 transition whitespace-nowrap';
      renderEventsTimeline(cachedEvents, activeEventFilter);
    });
  });

  // 3. Time range buttons
  const rangeButtons = document.querySelectorAll('.range-btn');
  rangeButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const selected = btn.getAttribute('data-range');
      if (selected === currentRange) return;

      rangeButtons.forEach(b => {
        b.className = 'range-btn px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg font-medium text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 transition border border-transparent';
      });
      btn.className = 'range-btn px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg font-semibold text-white bg-emerald-600 shadow-sm transition border border-emerald-500';

      currentRange = selected;
      fetchHistory(currentRange);
      fetchForecast(currentRange);
    });
  });

  // 4. Theme switcher toggle button
  const themeToggleBtn = document.getElementById('theme-toggle-btn');
  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', toggleTheme);
  }

  // 5. Refresh & CSV Export
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', triggerRefresh);

  const exportBtn = document.getElementById('export-csv-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      let url = `/api/export/csv?range=${currentRange}`;
      if (window.__ELECTRO_API_TOKEN__) {
        url += `&token=${encodeURIComponent(window.__ELECTRO_API_TOKEN__)}`;
      }
      window.location.href = url;
    });
  }

  // 6. Practical Keyboard Shortcuts:
  // - [R] to Refresh telemetry
  // - [T] to Toggle Theme (Light / Dark)
  // - [1] - [4] to switch tabs (Overview, Analytics, Supply, Events)
  document.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === 'r' || e.key === 'R') {
      e.preventDefault();
      triggerRefresh();
    } else if (e.key === 't' || e.key === 'T') {
      e.preventDefault();
      toggleTheme();
    } else if (e.key === '1') {
      switchTab('overview');
    } else if (e.key === '2') {
      switchTab('analytics');
    } else if (e.key === '3') {
      switchTab('supply');
    } else if (e.key === '4') {
      switchTab('history');
    }
  });

  // Initial data loading
  fetchDashboard();
  fetchHistory(currentRange);
  fetchForecast(currentRange);
  fetchEvents();

  // Auto-refresh cycle (every 30 seconds)
  pollTimer = setInterval(() => {
    fetchDashboard();
    fetchHistory(currentRange);
    fetchForecast(currentRange);
    fetchEvents();
  }, 30000);
});
