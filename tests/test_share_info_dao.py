import unittest
from unittest.mock import Mock

from dao.share_info_dao import ShareInfoDao


class ShareInfoDaoTests(unittest.TestCase):
    def test_dynamic_id_lookup_matches_both_url_forms(self):
        db = Mock()
        db.select_db.return_value = [{'id': 1, 'share_url': 'existing'}]
        dao = ShareInfoDao(db)

        rows = dao.query_shareInfo_by_dynamic_id(
            'https://t.bilibili.com/1270000000000000001', '35549700'
        )

        self.assertEqual(rows, db.select_db.return_value)
        sql = db.select_db.call_args.args[0].lower()
        self.assertIn('substring_index', sql)
        self.assertIn('1270000000000000001', sql)

    def test_insert_is_idempotent_for_alternate_url_form(self):
        db = Mock()
        db.select_db.return_value = [{'id': 1}]
        dao = ShareInfoDao(db)
        share_info = Mock(
            upId='123',
            upUrl='https://space.bilibili.com/123',
            share_url='https://www.bilibili.com/opus/1270000000000000001',
            status=0,
            machine_ip='127.0.0.1',
            share_time='2026-11-28 00:00:00',
            user_id='35549700',
            share_status=0,
        )

        inserted = dao.insert_shareInfo(share_info)

        self.assertFalse(inserted)
        db.insert.assert_not_called()
