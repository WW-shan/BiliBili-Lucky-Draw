from datetime import datetime
import re


class LotteryReadyDao(object):
    table_name = 't_lottery_ready'

    def __init__(self, db):
        self.db = db
        self.ensure_table()

    def ensure_table(self):
        sql = """
        CREATE TABLE IF NOT EXISTS t_lottery_ready
        (
            dyn_url varchar(255) not null primary key,
            lottery_id varchar(64) null,
            lottery_kind varchar(32) not null default 'official',
            lottery_rule text null,
            required_actions text null,
            lottery_end_time datetime null,
            verified_at datetime null,
            update_time datetime null
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """
        self.db.executeCommit(sql)
        # MySQL 的 CREATE TABLE IF NOT EXISTS 不会给旧表补列。
        # 用可重复执行的 ALTER 保证已有数据库也能支持普通抽奖。
        required_columns = {
            'lottery_kind': "varchar(32) NOT NULL DEFAULT 'official'",
            'lottery_rule': 'text NULL',
            'required_actions': 'text NULL',
        }
        try:
            existing = self.db.select_db(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "AND table_name = 't_lottery_ready'"
            ) or []
            existing_names = {str(row.get('column_name') or '').lower() for row in existing}
        except Exception:
            existing_names = set()
        for column, definition in required_columns.items():
            if column in existing_names:
                continue
            self.db.executeCommit(
                "ALTER TABLE t_lottery_ready ADD COLUMN %s %s" % (column, definition)
            )
        self.db.executeCommit(
            "ALTER TABLE t_lottery_ready CONVERT TO CHARACTER SET utf8mb4 "
            "COLLATE utf8mb4_general_ci"
        )

    @staticmethod
    def _literal(value):
        if value is None:
            return 'NULL'
        return "'" + str(value).replace("'", "''") + "'"

    def upsert(
        self,
        dyn_url,
        lottery_id,
        lottery_end_time=None,
        lottery_kind='official',
        lottery_rule=None,
        required_actions=None,
    ):
        now = datetime.now()
        sql = """
        INSERT INTO t_lottery_ready
            (dyn_url, lottery_id, lottery_kind, lottery_rule, required_actions,
             lottery_end_time, verified_at, update_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            lottery_id = VALUES(lottery_id),
            lottery_kind = VALUES(lottery_kind),
            lottery_rule = VALUES(lottery_rule),
            required_actions = VALUES(required_actions),
            lottery_end_time = VALUES(lottery_end_time),
            verified_at = VALUES(verified_at),
            update_time = VALUES(update_time)
        """ % (
            self._literal(dyn_url),
            self._literal(lottery_id),
            self._literal(lottery_kind),
            self._literal(lottery_rule),
            self._literal(required_actions),
            self._literal(lottery_end_time),
            self._literal(now),
            self._literal(now),
        )
        return self.db.executeCommit(sql)

    @staticmethod
    def _dynamic_id(value):
        match = re.search(r'/(\d+)(?:\?|$)', str(value or ''))
        return match.group(1) if match else None

    def restore_live_ready_statuses(self):
        """Restore live rules and synchronize duplicate URL representations."""
        sql = """
        UPDATE t_draw_dynamic d
        JOIN t_lottery_ready r
          ON r.dyn_url = d.dyn_url
          OR SUBSTRING_INDEX(SUBSTRING_INDEX(r.dyn_url, '/', -1), '?', 1)
             = SUBSTRING_INDEX(SUBSTRING_INDEX(d.dyn_url, '/', -1), '?', 1)
        SET d.status = '5'
        WHERE d.status IN ('0', '2')
          AND (r.lottery_end_time IS NULL OR r.lottery_end_time > NOW())
          AND NOT EXISTS (
              SELECT 1 FROM t_share_info s
              WHERE SUBSTRING_INDEX(SUBSTRING_INDEX(s.share_url, '/', -1), '?', 1)
                    = SUBSTRING_INDEX(SUBSTRING_INDEX(d.dyn_url, '/', -1), '?', 1)
                AND s.user_id = '35549700'
          )
          AND SUBSTRING_INDEX(SUBSTRING_INDEX(d.dyn_url, '/', -1), '?', 1)
              REGEXP '^[0-9]+$'
        """
        return self.db.executeCommit(sql)

    def query_by_url(self, dyn_url):
        sql = "SELECT * FROM t_lottery_ready WHERE dyn_url = %s LIMIT 1" % self._literal(dyn_url)
        try:
            rows = self.db.select_db(sql) or []
            return rows[0] if rows else None
        except Exception:
            return None

    def delete_by_url(self, dyn_url):
        sql = "DELETE FROM t_lottery_ready WHERE dyn_url = %s" % self._literal(dyn_url)
        return self.db.executeCommit(sql)

    def query_ready(self, limit=10):
        sql = """
        SELECT d.*, r.lottery_id, r.lottery_kind, r.lottery_rule, r.required_actions, r.lottery_end_time, r.verified_at
        FROM t_draw_dynamic d
        JOIN t_lottery_ready r ON r.dyn_url = d.dyn_url
        WHERE d.status = '5'
          AND (r.lottery_end_time IS NULL OR r.lottery_end_time > NOW())
          AND NOT EXISTS (
              SELECT 1 FROM t_share_info s
              WHERE SUBSTRING_INDEX(SUBSTRING_INDEX(s.share_url, '/', -1), '?', 1)
                    = SUBSTRING_INDEX(SUBSTRING_INDEX(d.dyn_url, '/', -1), '?', 1)
                AND s.user_id = '35549700'
          )
        ORDER BY
            CASE WHEN r.lottery_end_time IS NULL THEN 1 ELSE 0 END,
            r.lottery_end_time ASC,
            CAST(SUBSTRING_INDEX(d.dyn_url, '/', -1) AS UNSIGNED) DESC
        LIMIT %s
        """ % max(1, int(limit))
        try:
            return self.db.select_db(sql) or []
        except Exception:
            return []
