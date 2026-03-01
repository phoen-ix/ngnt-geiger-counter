<?php
// Timezone used for display only. Measurements are stored as UTC in the DB.
// Overwritten from settings table after DB connection.
$display_tz      = 'Europe/Vienna';
$offline_timeout = 5;
$default_alert   = 0.5;

function utcToLocal(string $utc): string {
    global $display_tz;
    return (new DateTime($utc, new DateTimeZone('UTC')))
        ->setTimezone(new DateTimeZone($display_tz))
        ->format('Y-m-d H:i:s');
}

function isDeviceOnline(string $device_id, array $statuses, array $lastseen, int $timeout): bool {
    if (($statuses[$device_id] ?? 'offline') === 'offline') return false;
    $last = $lastseen[$device_id] ?? null;
    if (!$last) return false;
    $age_min = (time() - strtotime($last . ' UTC')) / 60;
    return $age_min <= $timeout;
}

// Time-range options: key → [SQL interval literal, human label]
$range_options = [
    '1h'  => ['1 HOUR',  'Last hour'],
    '6h'  => ['6 HOUR',  'Last 6 hours'],
    '24h' => ['24 HOUR', 'Last 24 hours'],
    '7d'  => ['7 DAY',   'Last 7 days'],
];
$range = array_key_exists($_GET['range'] ?? '', $range_options) ? $_GET['range'] : '24h';
$sql_interval = $range_options[$range][0];
$range_label  = $range_options[$range][1];
$device_param = $_GET['device'] ?? 'all';

$db_host = 'mariadb';
$db_name = getenv('MARIADB_DATABASE') ?: 'ngnt-geigercounter';
$db_user = getenv('MARIADB_USER');
$db_pass = getenv('MARIADB_PASSWORD');

$db_error        = null;
$latest          = null;
$chart_data      = [];
$recent          = [];
$devices         = [];
$device          = 'all';
$device_qs       = '';
$device_names    = [];
$device_statuses = [];
$device_lastseen = [];
$device_alerts   = [];

try {
    $pdo = new PDO(
        "mysql:host={$db_host};port=3306;dbname={$db_name};charset=utf8mb4",
        $db_user,
        $db_pass,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );

    // Load global settings (fail-safe: keep defaults if table doesn't exist yet)
    try {
        $settings = $pdo->query("SELECT `key`, `value` FROM settings")->fetchAll(PDO::FETCH_KEY_PAIR);
        $display_tz      = $settings['display_timezone']        ?? $display_tz;
        $offline_timeout = (int)($settings['offline_timeout_minutes'] ?? $offline_timeout);
        $default_alert   = (float)($settings['default_alert_threshold'] ?? $default_alert);
    } catch (PDOException $e) {
        // settings table doesn't exist yet — use hardcoded defaults
    }

    // Load device list from devices table (fall back to SELECT DISTINCT)
    $has_devices_table = false;
    try {
        $device_rows = $pdo->query(
            "SELECT device_id, display_name, status, last_seen, alert_threshold FROM devices ORDER BY device_id"
        )->fetchAll(PDO::FETCH_ASSOC);
        $has_devices_table = true;
        $devices         = array_column($device_rows, 'device_id');
        $device_names    = array_column($device_rows, 'display_name', 'device_id');
        $device_statuses = array_column($device_rows, 'status', 'device_id');
        $device_lastseen = array_column($device_rows, 'last_seen', 'device_id');
        $device_alerts   = array_column($device_rows, 'alert_threshold', 'device_id');
    } catch (PDOException $e) {
        // devices table doesn't exist yet — fall back
        $devices = $pdo->query(
            "SELECT DISTINCT device_id FROM measurements ORDER BY device_id"
        )->fetchAll(PDO::FETCH_COLUMN);
    }

    $device = in_array($device_param, $devices, true) ? $device_param : 'all';
    $device_qs = ($device !== 'all') ? '&device=' . urlencode($device) : '';

    if ($device === 'all') {
        $latest = $pdo->query(
            "SELECT device_id, measured_at, cpm, usvh FROM measurements ORDER BY measured_at DESC LIMIT 1"
        )->fetch(PDO::FETCH_ASSOC);
    } else {
        $stmt = $pdo->prepare(
            "SELECT device_id, measured_at, cpm, usvh FROM measurements WHERE device_id = ? ORDER BY measured_at DESC LIMIT 1"
        );
        $stmt->execute([$device]);
        $latest = $stmt->fetch(PDO::FETCH_ASSOC);
    }

    if ($device === 'all') {
        $chart_data = $pdo->query(
            "SELECT measured_at, cpm, usvh FROM measurements
             WHERE measured_at >= NOW() - INTERVAL {$sql_interval}
             ORDER BY measured_at ASC"
        )->fetchAll(PDO::FETCH_ASSOC);
    } else {
        $stmt = $pdo->prepare(
            "SELECT measured_at, cpm, usvh FROM measurements
             WHERE device_id = ? AND measured_at >= NOW() - INTERVAL {$sql_interval}
             ORDER BY measured_at ASC"
        );
        $stmt->execute([$device]);
        $chart_data = $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    if ($device === 'all') {
        $recent = $pdo->query(
            "SELECT device_id, measured_at, cpm, usvh FROM measurements
             WHERE measured_at >= NOW() - INTERVAL {$sql_interval}
             ORDER BY measured_at DESC LIMIT 100"
        )->fetchAll(PDO::FETCH_ASSOC);
    } else {
        $stmt = $pdo->prepare(
            "SELECT device_id, measured_at, cpm, usvh FROM measurements
             WHERE device_id = ? AND measured_at >= NOW() - INTERVAL {$sql_interval}
             ORDER BY measured_at DESC LIMIT 100"
        );
        $stmt->execute([$device]);
        $recent = $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

} catch (PDOException $e) {
    error_log('Dashboard DB error: ' . $e->getMessage());
    $db_error = 'Could not connect to the database.';
}

// Determine alert threshold for the currently selected device
$active_alert = $default_alert;
if ($device !== 'all' && isset($device_alerts[$device]) && $device_alerts[$device] !== null) {
    $active_alert = (float)$device_alerts[$device];
} elseif ($device === 'all' && $latest) {
    $lid = $latest['device_id'];
    if (isset($device_alerts[$lid]) && $device_alerts[$lid] !== null) {
        $active_alert = (float)$device_alerts[$lid];
    }
}

$chart_labels = array_map(fn($r) => utcToLocal($r['measured_at']), $chart_data);
$chart_cpm    = array_map(fn($r) => (int)$r['cpm'],    $chart_data);
$chart_usvh   = array_map(fn($r) => (float)$r['usvh'], $chart_data);

// Helper: get display label for a device
function deviceLabel(string $id, array $names): string {
    $name = $names[$id] ?? null;
    return ($name !== null && $name !== '') ? $name : $id;
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60;url=?range=<?= htmlspecialchars($range) ?><?= htmlspecialchars($device_qs) ?>">
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
        header { margin-bottom: 28px; display: flex; justify-content: space-between; align-items: flex-start; }
        header div { flex: 1; }
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
        .settings-link {
            font-size: 0.8rem;
            color: #8b949e;
            text-decoration: none;
            border: 1px solid #30363d;
            padding: 5px 12px;
            border-radius: 6px;
            white-space: nowrap;
            margin-top: 2px;
        }
        .settings-link:hover { border-color: #58a6ff; color: #58a6ff; }
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
        .range-bar {
            display: flex;
            gap: 8px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }
        .range-bar a {
            display: inline-block;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
            text-decoration: none;
            border: 1px solid #30363d;
            background: #161b22;
            color: #8b949e;
            transition: border-color 0.15s;
        }
        .range-bar a:hover {
            border-color: #58a6ff;
        }
        .range-bar a.active {
            background: #58a6ff;
            border-color: #58a6ff;
            color: #fff;
        }
        canvas { max-height: 280px; }
    </style>
</head>
<body>

<header>
    <div>
        <h1>&#9762; NGNT Geiger Counter</h1>
        <p>
            <?php if ($latest): ?>
                Last reading: <strong><?= htmlspecialchars(utcToLocal($latest['measured_at'])) ?></strong>
                <?php if ($device === 'all'): ?>
                    &mdash; device: <?= htmlspecialchars(deviceLabel($latest['device_id'], $device_names)) ?>
                <?php endif; ?>
                &bull; page auto-refreshes every 60&thinsp;s
            <?php else: ?>
                Waiting for first measurement &bull; page auto-refreshes every 60&thinsp;s
            <?php endif; ?>
        </p>
    </div>
    <a href="admin.php" class="settings-link">Settings</a>
</header>

<div class="range-bar">
    <?php foreach ($range_options as $key => $opt): ?>
        <a href="?range=<?= $key ?><?= htmlspecialchars($device_qs) ?>"<?= $key === $range ? ' class="active"' : '' ?>><?= htmlspecialchars($opt[1]) ?></a>
    <?php endforeach; ?>
</div>

<?php if (count($devices) > 1): ?>
<div class="range-bar" style="margin-top:-16px;">
    <form method="get" style="display:flex;align-items:center;gap:8px;">
        <input type="hidden" name="range" value="<?= htmlspecialchars($range) ?>">
        <label for="device-select" style="font-size:0.8rem;color:#8b949e;">Device:</label>
        <select name="device" id="device-select" onchange="this.form.submit()"
            style="background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:20px;padding:6px 16px;font-size:0.8rem;font-weight:600;cursor:pointer;">
            <option value="all"<?= $device === 'all' ? ' selected' : '' ?>>All devices</option>
            <?php foreach ($devices as $dev): ?>
                <option value="<?= htmlspecialchars($dev) ?>"<?= $device === $dev ? ' selected' : '' ?>><?= htmlspecialchars(deviceLabel($dev, $device_names)) ?></option>
            <?php endforeach; ?>
        </select>
    </form>
</div>
<?php endif; ?>

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
        <div class="card-value <?= ($latest && (float)$latest['usvh'] > $active_alert) ? 'orange' : '' ?>">
            <?= $latest ? number_format((float)$latest['usvh'], 4) : '&mdash;' ?>
        </div>
        <div class="card-unit">&mu;Sv/h</div>
    </div>
    <?php if ($latest): ?>
    <div class="card">
        <div class="card-label">Device</div>
        <?php
            $show_device_id = ($device === 'all') ? $latest['device_id'] : $device;
            $is_online = isDeviceOnline($show_device_id, $device_statuses, $device_lastseen, $offline_timeout);
            $status_class = $is_online ? 'green' : 'orange';
            $status_text  = $is_online ? 'online' : 'offline';
        ?>
        <div class="card-value <?= $status_class ?>" style="font-size:1rem;padding-top:6px;">
            <?= htmlspecialchars(deviceLabel($show_device_id, $device_names)) ?>
        </div>
        <div class="card-unit">
            <?= $status_text ?>
        </div>
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
    <h2><?= htmlspecialchars($range_label) ?></h2>
    <?php if (empty($chart_data)): ?>
        <p class="no-data">No data available for the <?= htmlspecialchars(lcfirst($range_label)) ?>.</p>
    <?php else: ?>
        <canvas id="chart"></canvas>
    <?php endif; ?>
</div>

<div class="section">
    <h2>Recent measurements &mdash; <?= htmlspecialchars(lcfirst($range_label)) ?></h2>
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
                <td><?= htmlspecialchars(deviceLabel($row['device_id'], $device_names)) ?></td>
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
