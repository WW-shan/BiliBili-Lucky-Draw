import unittest
from unittest.mock import Mock

from dao.lottery_ready_dao import LotteryReadyDao


class LotteryReadyDaoTests(unittest.TestCase):
    def test_constructor_creates_ready_table(self):
        db = Mock()

        LotteryReadyDao(db)

        statements = [call.args[0].lower() for call in db.executeCommit.call_args_list]
        self.assertTrue(any('create table if not exists t_lottery_ready' in sql for sql in statements))

    def test_upsert_records_official_lottery_metadata(self):
        db = Mock()
        dao = LotteryReadyDao(db)
        db.executeCommit.reset_mock()

        dao.upsert(
            'https://www.bilibili.com/opus/123',
            lottery_id='456',
            lottery_end_time='2026-11-25 20:00:00',
        )

        sql = db.executeCommit.call_args.args[0].lower()
        self.assertIn('insert into t_lottery_ready', sql)
        self.assertIn('on duplicate key update', sql)
        self.assertIn('lottery_id', sql)

    def test_existing_ready_table_is_migrated_for_text_lottery_columns(self):
        db = Mock()
        dao = LotteryReadyDao(db)

        statements = [call.args[0] for call in db.executeCommit.call_args_list]
        self.assertTrue(any('alter table' in sql.lower() for sql in statements))
        self.assertTrue(any('lottery_kind' in sql.lower() for sql in statements))
        self.assertTrue(any('lottery_rule' in sql.lower() for sql in statements))
        self.assertTrue(any('required_actions' in sql.lower() for sql in statements))

    def test_upsert_accepts_plain_text_lottery_without_lottery_id(self):
        db = Mock()
        dao = LotteryReadyDao(db)
        db.executeCommit.reset_mock()

        dao.upsert(
            'https://www.bilibili.com/opus/123',
            '',
            lottery_kind='plain_text',
            lottery_rule='{"action": "forward"}',
        )

        sql = db.executeCommit.call_args.args[0].lower()
        self.assertIn('plain_text', sql)
        self.assertIn('{"action": "forward"}', sql)

    def test_ready_table_is_migrated_to_utf8mb4_for_emoji_rules(self):
        db = Mock()
        db.select_db.return_value = [
            {'column_name': 'lottery_kind'},
            {'column_name': 'lottery_rule'},
            {'column_name': 'required_actions'},
        ]
        LotteryReadyDao(db)

        statements = [call.args[0] for call in db.executeCommit.call_args_list]
        self.assertTrue(any('CONVERT TO CHARACTER SET utf8mb4' in s for s in statements))
        self.assertFalse(any(
            'ADD COLUMN lottery_kind' in s for s in statements
        ))


    def test_restore_live_ready_statuses_prefers_rule_over_stale_url_status(self):
        db = Mock()
        dao = LotteryReadyDao(db)

        dao.restore_live_ready_statuses()

        sql = db.executeCommit.call_args.args[0].lower()
        self.assertIn("d.status = '5'", sql)
        self.assertIn("d.status in ('0', '2')", sql)
        self.assertIn('r.lottery_end_time > now()', sql)
        self.assertIn('not exists', sql)

    def test_restore_live_ready_statuses_syncs_alternate_url_forms(self):
        db = Mock()
        dao = LotteryReadyDao(db)

        dao.restore_live_ready_statuses()

        sql = db.executeCommit.call_args.args[0].lower()
        self.assertIn('substring_index', sql)
        self.assertIn("d.status in ('0', '2')", sql)
        self.assertIn('not exists', sql)

    def test_query_ready_skips_urls_already_recorded_for_the_same_user(self):
        db = Mock()
        db.select_db.return_value = []
        dao = LotteryReadyDao(db)
        db.select_db.reset_mock()

        dao.query_ready(limit=10)

        sql = db.select_db.call_args.args[0].lower()
        self.assertIn('not exists', sql)
        self.assertIn('t_share_info', sql)
        self.assertIn("s.user_id = '35549700'", sql)

    def test_restore_live_ready_statuses_honors_failure_tombstone(self):
        db = Mock()
        dao = LotteryReadyDao(db)

        dao.restore_live_ready_statuses()

        sql = db.executeCommit.call_args.args[0].lower()
        self.assertIn('t_scan_cache', sql)
        self.assertIn("c.status = '3'", sql)
        self.assertIn('interval 15 minute', sql)

    def test_query_by_url_returns_single_rule(self):
        db = Mock()
        db.select_db.return_value = [{'dyn_url': 'ready', 'lottery_end_time': '2099-01-01'}]
        dao = LotteryReadyDao(db)
        db.select_db.reset_mock()

        row = dao.query_by_url('ready')

        self.assertEqual(row['dyn_url'], 'ready')
        self.assertIn("dyn_url = 'ready'", db.select_db.call_args.args[0])

    def test_query_ready_uses_verified_status_and_expiry_order(self):
        db = Mock()
        db.select_db.return_value = [{'dyn_url': 'https://www.bilibili.com/opus/123'}]
        dao = LotteryReadyDao(db)
        db.select_db.reset_mock()

        rows = dao.query_ready(limit=10)

        self.assertEqual(rows, db.select_db.return_value)
        sql = db.select_db.call_args.args[0].lower()
        self.assertIn('join t_lottery_ready', sql)
        self.assertIn("d.status = '5'", sql)
        self.assertIn('r.lottery_end_time', sql)
        self.assertIn('now()', sql)
        self.assertIn('limit 10', sql)


if __name__ == '__main__':
    unittest.main()
