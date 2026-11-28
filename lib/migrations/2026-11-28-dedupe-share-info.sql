-- One-time migration for deployments created before 2026-11-28.
-- Stop dynamic_share before running this file.

CREATE TABLE IF NOT EXISTS t_share_info_duplicate_archive_20261128
    LIKE t_share_info;

ALTER TABLE t_share_info_duplicate_archive_20261128
    ADD COLUMN archived_at DATETIME NULL;

INSERT INTO t_share_info_duplicate_archive_20261128
SELECT s.*, NOW()
FROM t_share_info s
JOIN (
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(share_url, '/', -1), '?', 1) AS dyn_id,
        user_id,
        MIN(id) AS keep_id,
        COUNT(*) AS duplicate_count
    FROM t_share_info
    GROUP BY dyn_id, user_id
    HAVING duplicate_count > 1
) d
  ON SUBSTRING_INDEX(SUBSTRING_INDEX(s.share_url, '/', -1), '?', 1) = d.dyn_id
 AND s.user_id = d.user_id
 AND s.id <> d.keep_id;

DELETE s
FROM t_share_info s
JOIN (
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(share_url, '/', -1), '?', 1) AS dyn_id,
        user_id,
        MIN(id) AS keep_id,
        COUNT(*) AS duplicate_count
    FROM t_share_info
    GROUP BY dyn_id, user_id
    HAVING duplicate_count > 1
) d
  ON SUBSTRING_INDEX(SUBSTRING_INDEX(s.share_url, '/', -1), '?', 1) = d.dyn_id
 AND s.user_id = d.user_id
 AND s.id <> d.keep_id;

ALTER TABLE t_share_info
    ADD COLUMN share_dynamic_id VARCHAR(32)
        GENERATED ALWAYS AS (
            SUBSTRING_INDEX(SUBSTRING_INDEX(share_url, '/', -1), '?', 1)
        ) STORED;

ALTER TABLE t_share_info
    ADD UNIQUE KEY t_share_info_dynamic_user_uq (share_dynamic_id, user_id);
