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

async function fetchData() {
  try {
    const [prices, load, generation, imbalance] = await Promise.all([
      fetch(`/api/prices?country=${currentCountry}`).then(r => r.json()),
      fetch(`/api/load?country=${currentCountry}`).then(r => r.json()),
      fetch(`/api/generation?country=${currentCountry}`).then(r => r.json()),
      fetch(`/api/imbalance?country=${currentCountry}`).then(r => r.json()),
    ]);

    updateCharts(prices, load, generation, imbalance);
    updateTimestamp();
  } catch (e) {
    console.error('Error fetching data:', e);
  }
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
  updatePricesChart(prices);
  updateLoadChart(load);
  updateGenerationChart(generation);
  updateImbalanceChart(imbalance);
}

function updatePricesChart(data) {
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
  const badge = document.getElementById(`${series}-stale`);
  if (data.stale) {
    badge.textContent = '⚠️ Stale';
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
