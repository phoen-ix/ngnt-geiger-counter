// The dashboard's chart. The readings come from the JSON block the template renders
// (#chart-data); the Content-Security-Policy (app.py) allows no inline script.
const { labels, cpm: cpmData, usvh: usvhData } = JSON.parse(document.getElementById('chart-data').textContent);

new Chart(document.getElementById('chart').getContext('2d'), {
    type: 'line',
    data: {
        labels: labels,
        datasets: [
            {
                label: 'CPM',
                data: cpmData,
                borderColor: '#58a6ff',
                backgroundColor: 'rgba(88,166,255,0.08)',
                fill: true,
                tension: 0.3,
                pointRadius: 2,
                yAxisID: 'yCpm'
            },
            {
                label: '\u03bcSv/h',
                data: usvhData,
                borderColor: '#3fb950',
                backgroundColor: 'rgba(63,185,80,0.0)',
                fill: false,
                tension: 0.3,
                pointRadius: 2,
                yAxisID: 'yUsvh'
            }
        ]
    },
    options: {
        responsive: true,
        // Square on phones, so the slanted time labels leave room for the plot.
        aspectRatio: matchMedia('(max-width: 640px)').matches ? 1 : 2,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: {
                labels: { color: '#8b949e', boxWidth: 12 }
            },
            tooltip: {
                backgroundColor: '#161b22',
                borderColor: '#30363d',
                borderWidth: 1,
                titleColor: '#c9d1d9',
                bodyColor: '#8b949e'
            }
        },
        scales: {
            x: {
                ticks: { color: '#6e7681', maxTicksLimit: 10, maxRotation: 40, font: { size: 11 } },
                grid:  { color: '#21262d' }
            },
            yCpm: {
                type: 'linear',
                position: 'left',
                ticks: { color: '#58a6ff', font: { size: 11 } },
                grid:  { color: '#21262d' },
                title: { display: true, text: 'CPM', color: '#58a6ff', font: { size: 11 } }
            },
            yUsvh: {
                type: 'linear',
                position: 'right',
                ticks: { color: '#3fb950', font: { size: 11 } },
                grid:  { drawOnChartArea: false },
                title: { display: true, text: '\u03bcSv/h', color: '#3fb950', font: { size: 11 } }
            }
        }
    }
});
