const GENERATION_COLORS = {
  nuclear: '#FF8C00',
  lignite: '#8B4513',
  hard_coal: '#333333',
  gas: '#4169E1',
  wind: '#20B2AA',
  solar: '#FFD700',
  hydro: '#1E90FF',
  biomass: '#228B22',
  other: '#808080',
};

const POLL_INTERVAL_MS = 60000;
let currentCountry = null;
let charts = {};
let inFlight = false;

async function fetchCountries() {
  try {
    const res = await fetch('/api/countries');
    const data = await res.json();
    const select = document.getElementById('country-select');
    select.innerHTML = '';
    data.countries.forEach(country => {
      const option = document.createElement('option');
      option.value = country.code;
      option.textContent = country.name;
      select.appendChild(option);
    });
    // The backend decides the default (DEFAULT_COUNTRY); fall back to whatever
    // it listed first. Hardcoding 'CZ' here would 404 every chart the moment
    // countries.yaml enables a different single country.
    const codes = data.countries.map(c => c.code);
    currentCountry = codes.includes(data.default) ? data.default : codes[0];
    select.value = currentCountry;
  } catch (e) {
    console.error('Error fetching countries:', e);
  }
}

// Fetch one series. Never throws and never rejects: a failed series returns
// null so it cannot take down the other three. A 404 means "nothing fetched
// yet for this series" (see routes.py), which is a normal startup state.
async function fetchSeries(path, country) {
  try {
    const res = await fetch(`${path}?country=${country}`);
    if (!res.ok) {
      console.warn(`${path} returned HTTP ${res.status}`);
      return null;
    }
    return await res.json();
  } catch (e) {
    console.error(`Error fetching ${path}:`, e);
    return null;
  }
}

async function fetchData() {
  if (inFlight) return;
  inFlight = true;

  // Pinned for the whole batch: switching country mid-flight must not repaint
  // the charts with the country we just navigated away from.
  const country = currentCountry;
  if (!country) {
    inFlight = false;
    return;
  }

  try {
    const [prices, load, generation, imbalance, imbalancePrices] = await Promise.all([
      fetchSeries('/api/prices', country),
      fetchSeries('/api/load', country),
      fetchSeries('/api/generation', country),
      fetchSeries('/api/imbalance', country),
      fetchSeries('/api/imbalance_prices', country),
    ]);

    if (country !== currentCountry) return;

    updateCharts(prices, load, generation, imbalance, imbalancePrices);
    updateTimestamp();
  } finally {
    inFlight = false;
  }
}

function updateTimestamp() {
  const now = new Date();
  document.getElementById('last-updated').textContent = `Last updated: ${now.toLocaleTimeString()}`;
}

// Axis ticks carry the time only — one full locale datetime per point is ~20
// characters of mostly redundant date. The full value moves to the tooltip.
function formatTime(isoString) {
  return new Date(isoString).toLocaleTimeString('cs-CZ', {
    timeZone: 'Europe/Prague',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatDateTime(isoString) {
  return new Date(isoString).toLocaleString('cs-CZ', { timeZone: 'Europe/Prague' });
}

function updateCharts(prices, load, generation, imbalance, imbalancePrices) {
  safeRender('prices', updatePricesChart, prices);
  safeRender('load', updateLoadChart, load);
  safeRender('generation', updateGenerationChart, generation);
  safeRender('imbalance', updateImbalanceChart, imbalance);
  safeRender('imbalance_prices', updateImbalancePricesChart, imbalancePrices);
}

// Render one chart in isolation, so an unexpected error in it (bad shape,
// Chart.js blowing up) never stops the remaining charts from rendering.
function safeRender(series, render, data) {
  try {
    render(data);
  } catch (e) {
    console.error(`Error rendering ${series} chart:`, e);
    setBadge(series, '⚠️ Error');
  }
}

// True only when `data` is a NormalizedSeries carrying at least one point.
// Otherwise flags the chart and returns false — callers bail out early, which
// leaves any previously rendered chart on screen instead of wiping it.
function hasPoints(series, data) {
  if (!data || !Array.isArray(data.points)) {
    setBadge(series, '⚠️ Unavailable');
    return false;
  }
  if (data.points.length === 0) {
    setBadge(series, '⚠️ No data');
    return false;
  }
  return true;
}

// Create the chart on first render, mutate it on every later one. Chart.js
// keeps its own canvas state, so rebuilding each poll would leak instances.
function drawChart(series, type, data, datasets, { stacked = false } = {}) {
  const labels = data.points.map(p => formatTime(p.t));
  const fullLabels = data.points.map(p => formatDateTime(p.t));

  if (!charts[series]) {
    const ctx = document.getElementById(`${series}-chart`).getContext('2d');
    charts[series] = new Chart(ctx, {
      type,
      data: { labels, datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true },
          tooltip: {
            callbacks: {
              // Ticks show time only, so the tooltip carries the date.
              title: items => items[0]?.chart.data.fullLabels?.[items[0].dataIndex] ?? '',
            },
          },
        },
        scales: {
          x: { stacked },
          y: { stacked, beginAtZero: true },
        },
      },
    });
  } else {
    charts[series].data.labels = labels;
    charts[series].data.datasets = datasets;
  }

  charts[series].data.fullLabels = fullLabels;
  charts[series].update();
  updateStaleBadge(series, data);
}

function updatePricesChart(data) {
  if (!hasPoints('prices', data)) return;
  drawChart('prices', 'line', data, [{
    label: 'EUR/MWh',
    data: data.points.map(p => p.v),
    borderColor: '#FF6384',
    backgroundColor: 'rgba(255, 99, 132, 0.1)',
    tension: 0.1,
    fill: true,
  }]);
}

function updateLoadChart(data) {
  if (!hasPoints('load', data)) return;
  drawChart('load', 'line', data, [{
    label: 'MW',
    data: data.points.map(p => p.v),
    borderColor: '#36A2EB',
    backgroundColor: 'rgba(54, 162, 235, 0.1)',
    tension: 0.1,
    fill: true,
  }]);
}

function updateGenerationChart(data) {
  if (!hasPoints('generation', data)) return;

  const datasets = Object.keys(GENERATION_COLORS).map(source => ({
    label: source.charAt(0).toUpperCase() + source.slice(1),
    data: data.points.map(p => p.by_source?.[source] ?? 0),
    borderColor: GENERATION_COLORS[source],
    backgroundColor: GENERATION_COLORS[source],
    borderWidth: 0,
  }));

  drawChart('generation', 'bar', data, datasets, { stacked: true });
}

function updateImbalanceChart(data) {
  if (!hasPoints('imbalance', data)) return;

  const values = data.points.map(p => p.v);
  const colors = values.map(v => v >= 0 ? '#4CAF50' : '#FF5252');

  drawChart('imbalance', 'bar', data, [{
    label: 'MW',
    data: values,
    backgroundColor: colors,
    borderColor: colors,
    borderWidth: 0,
  }]);
}

function updateImbalancePricesChart(data) {
  if (!hasPoints('imbalance_prices', data)) return;
  drawChart('imbalance_prices', 'line', data, [{
    // Imbalance prices are settled in the national currency (CZK for CZ,
    // PLN for PL), so the label must come from the data.
    label: data.unit || 'per MWh',
    data: data.points.map(p => p.v),
    borderColor: '#9966FF',
    backgroundColor: 'rgba(153, 102, 255, 0.1)',
    tension: 0.1,
    fill: true,
  }]);
}

function updateStaleBadge(series, data) {
  setBadge(series, data.stale ? '⚠️ Stale' : null);
}

// Show `text` in the chart's badge, or hide the badge when text is null.
function setBadge(series, text) {
  const badge = document.getElementById(`${series}-stale`);
  if (!badge) return;

  if (text) {
    badge.textContent = text;
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }
}

document.getElementById('country-select').addEventListener('change', (e) => {
  currentCountry = e.target.value;
  fetchData();
});

function showFatal(message) {
  document.getElementById('last-updated').textContent = message;
  ['prices', 'load', 'generation', 'imbalance', 'imbalance_prices'].forEach(s => setBadge(s, '⚠️ Error'));
}

async function init() {
  // Chart.js comes from a CDN. If it failed to load, every `new Chart` throws
  // and safeRender paints four identical badges that look like a backend
  // problem — so say what actually happened instead.
  if (typeof Chart === 'undefined') {
    console.error('Chart.js failed to load from the CDN.');
    showFatal('Charting library failed to load — check your network connection.');
    return;
  }

  await fetchCountries();
  await fetchData();
  setInterval(fetchData, POLL_INTERVAL_MS);
}

init();
