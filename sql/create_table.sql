-- Run ONCE on the MariaDB server database, as a DB administrator.
-- DDL only; the Python ingestion account needs INSERT and SELECT, not CREATE.
CREATE DATABASE IF NOT EXISTS `ptechview_machine`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `ptechview_machine`;

CREATE TABLE IF NOT EXISTS `micro850_plc_samples` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `device_id` VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `edge_id` BIGINT UNSIGNED NOT NULL,
  `sampled_at_utc` DATETIME(3) NOT NULL,
  `received_at_utc` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `point_count` SMALLINT UNSIGNED NOT NULL,
  `points_json` LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `payload_sha256` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_micro850_device_edge` (`device_id`, `edge_id`),
  KEY `idx_micro850_device_time` (`device_id`, `sampled_at_utc`),
  CONSTRAINT `chk_micro850_points_json` CHECK (JSON_VALID(`points_json`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
