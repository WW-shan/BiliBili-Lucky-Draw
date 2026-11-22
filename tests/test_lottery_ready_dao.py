import unittest
from unittest.mock import Mock

from dao.lottery_ready_dao import LotteryReadyDao


class LotteryReadyDaoTests(unittest.TestCase):
    def test_constructor_creates_ready_table(self):
        db = Mock()

        LotteryReadyDao(db)

        sql = db.executeCommit.call_args.args[0].lower()
        self.assertIn('create table if not exists t_lottery_ready', sql)

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
        self.assertIn('limit 10', sql)


if __name__ == '__main__':
    unittest.main()
