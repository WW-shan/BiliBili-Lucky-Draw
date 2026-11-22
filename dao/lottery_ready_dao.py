from datetime import datetime


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
            lottery_end_time datetime null,
            verified_at datetime null,
            update_time datetime null
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """
        self.db.executeCommit(sql)
        # MySQL 的 CREATE TABLE IF NOT EXISTS 不会给旧表补列。
        # 用可重复执行的 ALTER 保证已有数据库也能支持普通抽奖。
        migrations = (
            "ALTER TABLE t_lottery_ready MODIFY lottery_id varchar(64) NULL",
            "ALTER TABLE t_lottery_ready ADD COLUMN lottery_kind varchar(32) NOT NULL DEFAULT 'official'",
            "ALTER TABLE t_lottery_ready ADD COLUMN lottery_rule text NULL",
        )
        for statement in migrations:
            try:
                self.db.executeCommit(statement)
            except Exception:
                # ADD COLUMN 在已迁移数据库上会报重复列；不阻断启动。
                pass

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
    ):
        now = datetime.now()
        sql = """
        INSERT INTO t_lottery_ready
            (dyn_url, lottery_id, lottery_kind, lottery_rule,
             lottery_end_time, verified_at, update_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            lottery_id = VALUES(lottery_id),
            lottery_kind = VALUES(lottery_kind),
            lottery_rule = VALUES(lottery_rule),
            lottery_end_time = VALUES(lottery_end_time),
            verified_at = VALUES(verified_at),
            update_time = VALUES(update_time)
        """ % (
            self._literal(dyn_url),
            self._literal(lottery_id),
            self._literal(lottery_kind),
            self._literal(lottery_rule),
            self._literal(lottery_end_time),
            self._literal(now),
            self._literal(now),
        )
        return self.db.executeCommit(sql)

    def query_ready(self, limit=10):
        sql = """
        SELECT d.*, r.lottery_id, r.lottery_kind, r.lottery_rule, r.lottery_end_time, r.verified_at
        FROM t_draw_dynamic d
        JOIN t_lottery_ready r ON r.dyn_url = d.dyn_url
        WHERE d.status = '5'
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
