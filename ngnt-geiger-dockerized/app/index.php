<?php
// Timezone used for display only. Measurements are stored as UTC in the DB.
define('DISPLAY_TZ', 'Europe/Vienna');

function utcToLocal(string $utc): string {
    return (new DateTime($utc, new DateTimeZone('UTC')))
        ->setTimezone(new DateTimeZone(DISPLAY_TZ))
        ->format('Y-m-d H:i:s');
}

$db_host = 'mariadb';
$db_name = getenv('MARIADB_DATABASE') ?: 'ngnt-geigercounter';
$db_user = getenv('MARIADB_USER');
$db_pass = getenv('MARIADB_PASSWORD');

$db_error = null;
$latest   = null;
$chart_data = [];
$recent   = [];

try {
    $pdo = new PDO(
        "mysql:host={$db_host};port=3306;dbname={$db_name};charset=utf8mb4",
        $db_user,
        $db_pass,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );

    $latest = $pdo->query(
        "SELECT device_id, measured_at, cpm, usvh FROM measurements ORDER BY measured_at DESC LIMIT 1"
    )->fetch(PDO::FETCH_ASSOC);

    $chart_data = $pdo->query(
        "SELECT measured_at, cpm, usvh FROM measurements
         WHERE measured_at >= NOW() - INTERVAL 24 HOUR
         ORDER BY measured_at ASC"
    )->fetchAll(PDO::FETCH_ASSOC);

    $recent = $pdo->query(
        "SELECT device_id, measured_at, cpm, usvh FROM measurements
         ORDER BY measured_at DESC LIMIT 100"
    )->fetchAll(PDO::FETCH_ASSOC);

} catch (PDOException $e) {
    $db_error = $e->getMessage();
}

$chart_labels = array_map(fn($r) => utcToLocal($r['measured_at']), $chart_data);
$chart_cpm    = array_map(fn($r) => (int)$r['cpm'],    $chart_data);
$chart_usvh   = array_map(fn($r) => (float)$r['usvh'], $chart_data);
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>NGNT Geiger Counter</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: #0d1117;
            color: #c9d1d9;
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            padding: 24px 32px;
            min-height: 100vh;
        }
        header { margin-bottom: 28px; }
        header h1 {
            font-size: 1.4rem;
            font-weight: 700;
            color: #58a6ff;
            letter-spacing: -0.01em;
        }
        header p {
            font-size: 0.8rem;
            color: #6e7681;
            margin-top: 4px;
        }
        .error {
            background: #2d1b1b;
            border: 1px solid #5a2020;
            color: #f85149;
            padding: 14px 18px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 0.85rem;
        }
        .cards {
            display: flex;
            gap: 14px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 16px 22px 14px;
            min-width: 150px;
        }
        .card-label {
            font-size: 0.7rem;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 6px;
        }
        .card-value {
            font-size: 1.75rem;
            font-weight: 700;
            color: #58a6ff;
            line-height: 1;
        }
        .card-value.green  { color: #3fb950; }
        .card-value.orange { color: #f97316; }
        .card-unit {
            font-size: 0.72rem;
            color: #6e7681;
            margin-top: 4px;
        }
        .section {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 20px 24px;
            margin-bottom: 24px;
        }
        .section h2 {
            font-size: 0.78rem;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 18px;
        }
        .no-data {
            text-align: center;
            color: #6e7681;
            padding: 32px 0;
            font-size: 0.875rem;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th {
            text-align: left;
            padding: 8px 14px;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.07em;
            color: #6e7681;
            border-bottom: 1px solid #30363d;
        }
        td {
            padding: 7px 14px;
            font-size: 0.85rem;
            border-bottom: 1px solid #21262d;
            font-variant-numeric: tabular-nums;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #1c2129; }
        canvas { max-height: 280px; }
    </style>
</head>
<body>

<header>
    <h1>&#9762; NGNT Geiger Counter</h1>
    <p>
        <?php if ($latest): ?>
            Last reading: <strong><?= htmlspecialchars(utcToLocal($latest['measured_at'])) ?></strong>
            &mdash; device: <?= htmlspecialchars($latest['device_id']) ?>
            &bull; page auto-refreshes every 60&thinsp;s
        <?php else: ?>
            Waiting for first measurement &bull; page auto-refreshes every 60&thinsp;s
        <?php endif; ?>
    </p>
</header>

<?php if ($db_error): ?>
    <div class="error">Database error: <?= htmlspecialchars($db_error) ?></div>
<?php endif; ?>

<div class="cards">
    <div class="card">
        <div class="card-label">CPM</div>
        <div class="card-value"><?= $latest ? (int)$latest['cpm'] : '&mdash;' ?></div>
        <div class="card-unit">counts per minute</div>
    </div>
    <div class="card">
        <div class="card-label">Dose rate</div>
        <div class="card-value <?= ($latest && (float)$latest['usvh'] > 0.5) ? 'orange' : '' ?>">
            <?= $latest ? number_format((float)$latest['usvh'], 4) : '&mdash;' ?>
        </div>
        <div class="card-unit">&mu;Sv/h</div>
    </div>
    <?php if ($latest): ?>
    <div class="card">
        <div class="card-label">Device</div>
        <div class="card-value green" style="font-size:1rem;padding-top:6px;">online</div>
        <div class="card-unit"><?= htmlspecialchars($latest['device_id']) ?></div>
    </div>
    <?php endif; ?>
    <?php if (!empty($recent)): ?>
    <div class="card">
        <div class="card-label">Readings stored</div>
        <div class="card-value" style="color:#c9d1d9;"><?= count($recent) >= 100 ? '100+' : count($recent) ?></div>
        <div class="card-unit">last 100 shown below</div>
    </div>
    <?php endif; ?>
</div>

<div class="section">
    <h2>Last 24 hours</h2>
    <?php if (empty($chart_data)): ?>
        <p class="no-data">No data available for the last 24 hours.</p>
    <?php else: ?>
        <canvas id="chart"></canvas>
    <?php endif; ?>
</div>

<div class="section">
    <h2>Recent measurements</h2>
    <?php if (empty($recent)): ?>
        <p class="no-data">No measurements recorded yet.</p>
    <?php else: ?>
    <table>
        <thead>
            <tr>
                <th>Timestamp</th>
                <th>CPM</th>
                <th>&mu;Sv/h</th>
                <th>Device</th>
            </tr>
        </thead>
        <tbody>
            <?php foreach ($recent as $row): ?>
            <tr>
                <td><?= htmlspecialchars(utcToLocal($row['measured_at'])) ?></td>
                <td><?= (int)$row['cpm'] ?></td>
                <td><?= number_format((float)$row['usvh'], 4) ?></td>
                <td><?= htmlspecialchars($row['device_id']) ?></td>
            </tr>
            <?php endforeach; ?>
        </tbody>
    </table>
    <?php endif; ?>
</div>

<?php if (!empty($chart_data)): ?>
<script>
const labels = <?= json_encode($chart_labels) ?>;
const cpmData  = <?= json_encode($chart_cpm) ?>;
const usvhData = <?= json_encode($chart_usvh) ?>;

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
</script>
<?php endif; ?>

</body>
</html>
