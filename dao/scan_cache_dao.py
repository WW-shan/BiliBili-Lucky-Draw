import hashlib
from datetime import datetime


class ScanCacheDao(object):
    table_name = 't_scan_cache'

    def __init__(self, db):
        self.db = db
        self.ensure_table()

    def ensure_table(self):
        sql = """
        CREATE TABLE IF NOT EXISTS t_scan_cache
        (
            scan_key   char(64)      not null primary key,
            scan_type  varchar(50)   not null,
            scan_url   varchar(1024) not null,
            source     varchar(255)  null,
            note       varchar(255)  null,
            status     varchar(20)   null,
            link_count int           null,
            last_error varchar(500)  null,
            insert_time datetime     null,
            update_time datetime     null
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """
        self.db.executeCommit(sql)

    @staticmethod
    def build_key(scan_type, scan_url):
        value = '%s:%s' % (scan_type, scan_url)
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def query(self, scan_type, scan_url):
        self.db.con.ping(reconnect=True)
        self.db.cur.execute(
            'SELECT status, update_time, insert_time, last_error FROM %s WHERE scan_key = %%s'
            % self.table_name,
            (self.build_key(scan_type, scan_url),),
        )
        return self.db.cur.fetchone()

    def has_success(self, scan_type, scan_url):
        row = self.query(scan_type, scan_url)
        return row is not None and str(row.get('status')) == '1'

    def has_recent_failure(self, scan_type, scan_url, retry_hours):
        if float(retry_hours) <= 0:
            return False
        row = self.query(scan_type, scan_url)
        if row is None or str(row.get('status')) == '1':
            return False
        if (row.get('update_time') or row.get('insert_time')) is None:
            return False
        # Compare inside MySQL so container/app timezone differences do not
        # turn a 15-minute cooldown into an 8-hour one.
        try:
            self.db.con.ping(reconnect=True)
            self.db.cur.execute(
                '''SELECT 1 FROM %s
                   WHERE scan_key = %%s
                     AND COALESCE(update_time, insert_time) > NOW() - INTERVAL %%s MINUTE
                   LIMIT 1'''
                % self.table_name,
                (self.build_key(scan_type, scan_url), int(float(retry_hours) * 60)),
            )
            return self.db.cur.fetchone() is not None
        except Exception:
            return True

    def save_success(self, scan_type, scan_url, source, note, link_count):
        self._upsert(scan_type, scan_url, source, note, '1', link_count, '')

    def save_failure(self, scan_type, scan_url, source, note, error):
        self._upsert(scan_type, scan_url, source, note, '3', 0, str(error)[:500])

    def delete(self, scan_type, scan_url):
        self.db.con.ping(reconnect=True)
        self.db.cur.execute(
            'DELETE FROM %s WHERE scan_key = %%s' % self.table_name,
            (self.build_key(scan_type, scan_url),),
        )
        self.db.con.commit()

    def _upsert(self, scan_type, scan_url, source, note, status, link_count, error):
        now = datetime.now()
        sql = """
        INSERT INTO t_scan_cache
            (scan_key, scan_type, scan_url, source, note, status, link_count,
             last_error, insert_time, update_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            source = VALUES(source),
            note = VALUES(note),
            status = VALUES(status),
            link_count = VALUES(link_count),
            last_error = VALUES(last_error),
            update_time = VALUES(update_time)
        """
        self.db.con.ping(reconnect=True)
        self.db.cur.execute(
            sql,
            (
                self.build_key(scan_type, scan_url),
                scan_type,
                scan_url,
                source,
                note,
                status,
                link_count,
                error,
                now,
                now,
            ),
        )
        self.db.con.commit()
