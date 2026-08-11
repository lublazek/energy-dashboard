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
let currentCountry = 'CZ';
let charts = {};

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
      if (country.code === 'CZ') option.selected = true;
      select.appendChild(option);
    });
  } catch (e) {
    console.error('Error fetching countries:', e);
  }
}

// Fetch one series. Never throws and never rejects: a failed series returns
// null so it cannot take down the other three. A 404 means "nothing fetched
// yet for this series" (see routes.py), which is a normal startup state.
async function fetchSeries(path) {
  try {
    const res = await fetch(`${path}?country=${currentCountry}`);
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
  const [prices, load, generation, imbalance] = await Promise.all([
    fetchSeries('/api/prices'),
    fetchSeries('/api/load'),
    fetchSeries('/api/generation'),
    fetchSeries('/api/imbalance'),
  ]);

  updateCharts(prices, load, generation, imbalance);
  updateTimestamp();
}

function updateTimestamp() {
  const now = new Date();
  document.getElementById('last-updated').textContent = `Last updated: ${now.toLocaleTimeString()}`;
}

function formatDateTime(isoString) {
  const date = new Date(isoString);
  return date.toLocaleString('cs-CZ', { timeZone: 'Europe/Prague' });
}

function updateCharts(prices, load, generation, imbalance) {
  safeRender('prices', updatePricesChart, prices);
  safeRender('load', updateLoadChart, load);
  safeRender('generation', updateGenerationChart, generation);
  safeRender('imbalance', updateImbalanceChart, imbalance);
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

function updatePricesChart(data) {
  if (!hasPoints('prices', data)) return;

  const labels = data.points.map(p => formatDateTime(p.t));
  const values = data.points.map(p => p.v);

  if (!charts.prices) {
    const ctx = document.getElementById('prices-chart').getContext('2d');
    charts.prices = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'EUR/MWh',
          data: values,
          borderColor: '#FF6384',
          backgroundColor: 'rgba(255, 99, 132, 0.1)',
          tension: 0.1,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true } },
      },
    });
  } else {
    charts.prices.data.labels = labels;
    charts.prices.data.datasets[0].data = values;
    charts.prices.update();
  }

  updateStaleBadge('prices', data);
}

function updateLoadChart(data) {
  if (!hasPoints('load', data)) return;

  const labels = data.points.map(p => formatDateTime(p.t));
  const values = data.points.map(p => p.v);

  if (!charts.load) {
    const ctx = document.getElementById('load-chart').getContext('2d');
    charts.load = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'MW',
          data: values,
          borderColor: '#36A2EB',
          backgroundColor: 'rgba(54, 162, 235, 0.1)',
          tension: 0.1,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true } },
      },
    });
  } else {
    charts.load.data.labels = labels;
    charts.load.data.datasets[0].data = values;
    charts.load.update();
  }

  updateStaleBadge('load', data);
}

function updateGenerationChart(data) {
  if (!hasPoints('generation', data)) return;

  const labels = data.points.map(p => formatDateTime(p.t));
  const sources = Object.keys(GENERATION_COLORS);

  const datasets = sources.map(source => ({
    label: source.charAt(0).toUpperCase() + source.slice(1),
    data: data.points.map(p => p.by_source?.[source] || 0),
    borderColor: GENERATION_COLORS[source],
    backgroundColor: GENERATION_COLORS[source],
    borderWidth: 0,
  }));

  if (!charts.generation) {
    const ctx = document.getElementById('generation-chart').getContext('2d');
    charts.generation = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        plugins: { legend: { display: true } },
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
      },
    });
  } else {
    charts.generation.data.labels = labels;
    charts.generation.data.datasets = datasets;
    charts.generation.update();
  }

  updateStaleBadge('generation', data);
}

function updateImbalanceChart(data) {
  if (!hasPoints('imbalance', data)) return;

  const labels = data.points.map(p => formatDateTime(p.t));
  const values = data.points.map(p => p.v);
  const colors = values.map(v => v >= 0 ? '#4CAF50' : '#FF5252');

  if (!charts.imbalance) {
    const ctx = document.getElementById('imbalance-chart').getContext('2d');
    charts.imbalance = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'MW',
          data: values,
          backgroundColor: colors,
          borderColor: colors,
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true } },
      },
    });
  } else {
    charts.imbalance.data.labels = labels;
    charts.imbalance.data.datasets[0].data = values;
    charts.imbalance.data.datasets[0].backgroundColor = colors;
    charts.imbalance.data.datasets[0].borderColor = colors;
    charts.imbalance.update();
  }

  updateStaleBadge('imbalance', data);
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

async function init() {
  await fetchCountries();
  await fetchData();
  setInterval(fetchData, POLL_INTERVAL_MS);
}

init();
