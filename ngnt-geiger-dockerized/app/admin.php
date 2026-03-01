<?php
$db_host = 'mariadb';
$db_name = getenv('MARIADB_DATABASE') ?: 'ngnt-geigercounter';
$db_user = getenv('MARIADB_USER');
$db_pass = getenv('MARIADB_PASSWORD');

$db_error = null;
$success  = null;

// Whitelist of allowed setting keys
$allowed_keys = ['display_timezone', 'offline_timeout_minutes', 'default_cpm_factor', 'default_alert_threshold'];

try {
    $pdo = new PDO(
        "mysql:host={$db_host};port=3306;dbname={$db_name};charset=utf8mb4",
        $db_user,
        $db_pass,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );

    // ── Handle POST actions ──────────────────────────────────────────────────
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $action = $_POST['action'] ?? '';

        if ($action === 'save_settings') {
            $stmt = $pdo->prepare("UPDATE settings SET `value` = ? WHERE `key` = ?");
            foreach ($allowed_keys as $key) {
                if (isset($_POST[$key])) {
                    $stmt->execute([trim($_POST[$key]), $key]);
                }
            }
            $success = 'Settings saved.';
        }

        if ($action === 'save_device') {
            $dev_id = $_POST['device_id'] ?? '';
            if ($dev_id !== '') {
                $display_name    = trim($_POST['display_name'] ?? '') !== '' ? trim($_POST['display_name']) : null;
                $cpm_factor      = trim($_POST['cpm_factor'] ?? '') !== '' ? (float)trim($_POST['cpm_factor']) : null;
                $alert_threshold = trim($_POST['alert_threshold'] ?? '') !== '' ? (float)trim($_POST['alert_threshold']) : null;

                $stmt = $pdo->prepare(
                    "UPDATE devices SET display_name = ?, cpm_factor = ?, alert_threshold = ? WHERE device_id = ?"
                );
                $stmt->execute([$display_name, $cpm_factor, $alert_threshold, $dev_id]);
                $success = 'Device "' . $dev_id . '" updated.';
            }
        }
    }

    // ── Load current settings ────────────────────────────────────────────────
    $settings = $pdo->query("SELECT `key`, `value` FROM settings")->fetchAll(PDO::FETCH_KEY_PAIR);

    // ── Load devices ─────────────────────────────────────────────────────────
    $device_rows = $pdo->query(
        "SELECT device_id, display_name, status, last_seen, cpm_factor, alert_threshold, created_at FROM devices ORDER BY device_id"
    )->fetchAll(PDO::FETCH_ASSOC);

} catch (PDOException $e) {
    error_log('Admin DB error: ' . $e->getMessage());
    $db_error = 'Could not connect to the database.';
    $settings = [];
    $device_rows = [];
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Settings — NGNT Geiger Counter</title>
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
        .back-link {
            font-size: 0.8rem;
            color: #8b949e;
            text-decoration: none;
            border: 1px solid #30363d;
            padding: 5px 12px;
            border-radius: 6px;
            white-space: nowrap;
            margin-top: 2px;
        }
        .back-link:hover { border-color: #58a6ff; color: #58a6ff; }
        .error {
            background: #2d1b1b;
            border: 1px solid #5a2020;
            color: #f85149;
            padding: 14px 18px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 0.85rem;
        }
        .success {
            background: #1b2d1b;
            border: 1px solid #205a20;
            color: #3fb950;
            padding: 14px 18px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 0.85rem;
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
        .form-row {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
        }
        .form-row label {
            font-size: 0.85rem;
            color: #8b949e;
            min-width: 180px;
            flex-shrink: 0;
        }
        .form-row input[type="text"],
        .form-row input[type="number"] {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            color: #c9d1d9;
            padding: 6px 12px;
            font-size: 0.85rem;
            width: 260px;
        }
        .form-row input:focus {
            outline: none;
            border-color: #58a6ff;
        }
        .btn {
            background: #238636;
            color: #fff;
            border: 1px solid #2ea043;
            border-radius: 6px;
            padding: 6px 18px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
        }
        .btn:hover { background: #2ea043; }
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
        td input[type="text"] {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 4px;
            color: #c9d1d9;
            padding: 4px 8px;
            font-size: 0.82rem;
            width: 100%;
        }
        td input:focus {
            outline: none;
            border-color: #58a6ff;
        }
        .status-online  { color: #3fb950; font-weight: 600; }
        .status-offline { color: #f97316; font-weight: 600; }
        .devices-wrap form { margin-bottom: 0; }
        .devices-wrap table { margin-bottom: 0; }
        .devices-wrap form + form tr td { border-top: none; }
        .no-data {
            text-align: center;
            color: #6e7681;
            padding: 32px 0;
            font-size: 0.875rem;
        }
        .btn-sm {
            background: #238636;
            color: #fff;
            border: 1px solid #2ea043;
            border-radius: 4px;
            padding: 3px 12px;
            font-size: 0.78rem;
            font-weight: 600;
            cursor: pointer;
        }
        .btn-sm:hover { background: #2ea043; }
    </style>
</head>
<body>

<header>
    <div>
        <h1>&#9881; Settings</h1>
        <p>Global settings and per-device configuration</p>
    </div>
    <a href="index.php" class="back-link">Back to Dashboard</a>
</header>

<?php if ($db_error): ?>
    <div class="error">Database error: <?= htmlspecialchars($db_error) ?></div>
<?php endif; ?>

<?php if ($success): ?>
    <div class="success"><?= htmlspecialchars($success) ?></div>
<?php endif; ?>

<!-- ── Global settings ──────────────────────────────────────────────────── -->
<div class="section">
    <h2>Global Settings</h2>
    <form method="post">
        <input type="hidden" name="action" value="save_settings">
        <div class="form-row">
            <label for="s-tz">Display Timezone</label>
            <input type="text" id="s-tz" name="display_timezone"
                   value="<?= htmlspecialchars($settings['display_timezone'] ?? 'Europe/Vienna') ?>">
        </div>
        <div class="form-row">
            <label for="s-timeout">Offline Timeout (minutes)</label>
            <input type="number" id="s-timeout" name="offline_timeout_minutes" min="1"
                   value="<?= htmlspecialchars($settings['offline_timeout_minutes'] ?? '5') ?>">
        </div>
        <div class="form-row">
            <label for="s-cpm">Default CPM Factor</label>
            <input type="text" id="s-cpm" name="default_cpm_factor"
                   value="<?= htmlspecialchars($settings['default_cpm_factor'] ?? '0.0057') ?>">
        </div>
        <div class="form-row">
            <label for="s-alert">Default Alert Threshold (&mu;Sv/h)</label>
            <input type="text" id="s-alert" name="default_alert_threshold"
                   value="<?= htmlspecialchars($settings['default_alert_threshold'] ?? '0.5') ?>">
        </div>
        <div class="form-row">
            <label>&nbsp;</label>
            <button type="submit" class="btn">Save Settings</button>
        </div>
    </form>
</div>

<!-- ── Devices ───────────────────────────────────────────────────────────── -->
<div class="section">
    <h2>Devices</h2>
    <?php if (empty($device_rows)): ?>
        <p class="no-data">No devices registered yet. Devices appear here automatically when they send their first measurement.</p>
    <?php else: ?>
    <div class="devices-wrap">
    <?php foreach ($device_rows as $dev): ?>
    <form method="post" style="margin-bottom:0;">
        <input type="hidden" name="action" value="save_device">
        <input type="hidden" name="device_id" value="<?= htmlspecialchars($dev['device_id']) ?>">
        <table style="margin-bottom:0;">
            <?php if ($dev === $device_rows[0]): ?>
            <thead>
                <tr>
                    <th>Device ID</th>
                    <th>Display Name</th>
                    <th>Status</th>
                    <th>Last Seen</th>
                    <th>CPM Factor</th>
                    <th>Alert Threshold</th>
                    <th></th>
                </tr>
            </thead>
            <?php endif; ?>
            <tbody>
                <tr>
                    <td><?= htmlspecialchars($dev['device_id']) ?></td>
                    <td><input type="text" name="display_name"
                               value="<?= htmlspecialchars($dev['display_name'] ?? '') ?>"
                               placeholder="<?= htmlspecialchars($dev['device_id']) ?>"></td>
                    <td class="<?= $dev['status'] === 'online' ? 'status-online' : 'status-offline' ?>">
                        <?= htmlspecialchars($dev['status']) ?>
                    </td>
                    <td><?= $dev['last_seen'] ? htmlspecialchars($dev['last_seen']) : '&mdash;' ?></td>
                    <td><input type="text" name="cpm_factor"
                               value="<?= $dev['cpm_factor'] !== null ? htmlspecialchars($dev['cpm_factor']) : '' ?>"
                               placeholder="<?= htmlspecialchars($settings['default_cpm_factor'] ?? '0.0057') ?>"></td>
                    <td><input type="text" name="alert_threshold"
                               value="<?= $dev['alert_threshold'] !== null ? htmlspecialchars($dev['alert_threshold']) : '' ?>"
                               placeholder="<?= htmlspecialchars($settings['default_alert_threshold'] ?? '0.5') ?>"></td>
                    <td><button type="submit" class="btn-sm">Save</button></td>
                </tr>
            </tbody>
        </table>
    </form>
    <?php endforeach; ?>
    </div>
    <?php endif; ?>
</div>

</body>
</html>
