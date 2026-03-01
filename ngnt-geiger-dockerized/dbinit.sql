-- ngnt-geiger-counter v3.0 — schema
-- MariaDB 11.4+  |  charset: utf8mb4  |  engine: InnoDB

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `ngnt-geigercounter`
  /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci */;

USE `ngnt-geigercounter`;


-- ── users ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `users` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `username`      VARCHAR(50)  NOT NULL UNIQUE,
  `email`         VARCHAR(255) DEFAULT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `role`          ENUM('admin','user') NOT NULL DEFAULT 'user',
  `pepper`        VARCHAR(64)  DEFAULT NULL,
  `public`        BOOLEAN      NOT NULL DEFAULT FALSE,
  `pw_version`    INT          NOT NULL DEFAULT 0    COMMENT 'Incremented on password change to invalidate sessions',
  `created_at`    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


-- ── devices ───────────────────────────────────────────────────────────────────
-- Tracks every Geiger counter registered by a user.  Status is updated by
-- the Python subscriber on measurement, connect, and will (offline) messages.

CREATE TABLE IF NOT EXISTS `devices` (
  `id`              INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`         INT          NOT NULL,
  `device_id`       VARCHAR(50)  NOT NULL UNIQUE COMMENT 'MQTT username (geiger_AABBCC)',
  `mac_address`     VARCHAR(17)  NOT NULL,
  `display_name`    VARCHAR(100) DEFAULT NULL,
  `mqtt_password`   VARCHAR(64)  NOT NULL,
  `status`          ENUM('online','offline') NOT NULL DEFAULT 'offline',
  `last_seen`       DATETIME     DEFAULT NULL,
  `cpm_factor`      FLOAT        DEFAULT NULL   COMMENT 'NULL = use global default',
  `alert_threshold` FLOAT        DEFAULT NULL   COMMENT 'NULL = use global default',
  `provisioned`     BOOLEAN      NOT NULL DEFAULT FALSE,
  `created_at`      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


-- ── password_resets ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `password_resets` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`    INT NOT NULL,
  `token`      VARCHAR(64) NOT NULL UNIQUE,
  `expires_at` DATETIME NOT NULL,
  `used`       BOOLEAN NOT NULL DEFAULT FALSE,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


-- ── measurements ─────────────────────────────────────────────────────────────
-- Partitioned by measured_at (quarterly RANGE COLUMNS).
-- InnoDB requires the partition key to be part of every unique index, so the
-- primary key is (id, measured_at).  id remains AUTO_INCREMENT as before.
--
-- The table is created with only the p_future catch-all partition. The stored
-- procedure ensure_partitions() (called below and monthly by an event) creates
-- the actual quarterly partitions automatically — no manual maintenance needed.

CREATE TABLE IF NOT EXISTS `measurements` (
  `id`          int          NOT NULL AUTO_INCREMENT,
  `device_id`   varchar(50)  NOT NULL COMMENT 'Geiger counter identifier (MQTT user)',
  `measured_at` datetime     NOT NULL COMMENT 'UTC timestamp from the device',
  `cpm`         int          NOT NULL COMMENT 'Counts per minute',
  `usvh`        float        NOT NULL COMMENT 'Microsieverts per hour',
  `created_at`  timestamp    NOT NULL DEFAULT current_timestamp() COMMENT 'When this row was stored',
  PRIMARY KEY (`id`, `measured_at`),
  KEY `idx_device_measured` (`device_id`, `measured_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  PARTITION BY RANGE COLUMNS(`measured_at`) (
    PARTITION p_future VALUES LESS THAN (MAXVALUE)
  );


-- ── Partition maintenance ─────────────────────────────────────────────────────
-- ensure_partitions(): creates quarterly partitions up to 2 years ahead.
-- Safe to call at any time — only adds partitions that do not yet exist.

DELIMITER //

CREATE OR REPLACE PROCEDURE ensure_partitions()
BEGIN
  DECLARE v_max_date DATE;
  DECLARE v_new_end  DATE;
  DECLARE v_pname    VARCHAR(20);
  DECLARE v_max_str  VARCHAR(50) DEFAULT NULL;

  -- Find the current highest explicitly-defined upper boundary.
  -- RANGE COLUMNS stores the datetime boundary as a datetime string in
  -- PARTITION_DESCRIPTION (e.g. '2026-04-01 00:00:00').  We take MAX() on
  -- the raw string (ISO dates sort correctly) to avoid CAST per row, which
  -- triggers a '0000-00-00' error under strict SQL mode in MariaDB 11.4.
  -- RANGE COLUMNS descriptions include embedded quotes (e.g. '2028-04-01 …')
  -- so we strip them before comparison / conversion.
  SELECT MAX(REPLACE(PARTITION_DESCRIPTION, '''', '')) INTO v_max_str
  FROM information_schema.PARTITIONS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'measurements'
    AND PARTITION_NAME != 'p_future'
    AND PARTITION_DESCRIPTION != 'MAXVALUE';

  IF v_max_str IS NOT NULL THEN
    SET v_max_date = CAST(v_max_str AS DATE);
  ELSE
    SET v_max_date = MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL (QUARTER(CURDATE()) - 1) QUARTER;
  END IF;

  -- Keep adding quarterly partitions until we have at least 2 years of headroom.
  WHILE v_max_date < DATE_ADD(CURDATE(), INTERVAL 2 YEAR) DO
    SET v_new_end = DATE_ADD(v_max_date, INTERVAL 3 MONTH);
    -- Name format: p{YEAR}_q{Q}  e.g. p2026_q2
    SET v_pname   = CONCAT('p', YEAR(v_max_date), '_q', QUARTER(v_max_date));

    SET @sql = CONCAT(
      'ALTER TABLE measurements REORGANIZE PARTITION p_future INTO (',
        'PARTITION `', v_pname, '` VALUES LESS THAN (\'', v_new_end, '\'), ',
        'PARTITION p_future VALUES LESS THAN (MAXVALUE)',
      ')'
    );
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;

    SET v_max_date = v_new_end;
  END WHILE;
END //

-- Monthly event — fires on the 1st of every month, keeps 2 years of headroom.
CREATE EVENT IF NOT EXISTS maintain_partitions
  ON SCHEDULE EVERY 1 MONTH
  STARTS (CURDATE() + INTERVAL 1 MONTH)
  DO CALL ensure_partitions() //

DELIMITER ;

-- Bootstrap: create initial partitions immediately on first start.
CALL ensure_partitions();


-- ── settings ──────────────────────────────────────────────────────────────────
-- Key-value store for global dashboard / system settings.
-- INSERT IGNORE preserves existing values on re-run.

CREATE TABLE IF NOT EXISTS `settings` (
  `key`   VARCHAR(50)  NOT NULL PRIMARY KEY,
  `value` VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

INSERT IGNORE INTO `settings` (`key`, `value`) VALUES
  ('offline_timeout_minutes', '5'),
  ('display_timezone',        'Europe/Vienna'),
  ('default_cpm_factor',      '0.0057'),
  ('default_alert_threshold', '0.5'),
  ('smtp_host',               ''),
  ('smtp_port',               '587'),
  ('smtp_user',               ''),
  ('smtp_password',           ''),
  ('smtp_from',               ''),
  ('smtp_tls',                '1'),
  ('base_url',                ''),
  ('registration_enabled',    '1'),
  ('session_timeout_minutes', '1440');
