import unittest
from unittest.mock import patch

from selenium.webdriver.common.by import By

from service.bilibili_errors import BilibiliAuthenticationRequired
from service.login_service.login_service import LoginService
from utils import globals


class FakeBody:
    def __init__(self, text=''):
        self.text = text


class FakeBrowser:
    def __init__(self, nav_body):
        self.nav_body = nav_body
        self.current_url = ''
        self.title = ''
        self.visited = []
        self.cookies = []

    def get(self, url):
        self.current_url = url
        self.visited.append(url)

    def refresh(self):
        return None

    def add_cookie(self, cookie):
        self.cookies.append(cookie)

    def get_cookies(self):
        return list(self.cookies)

    def get_log(self, name):
        return []

    def find_element(self, by, value):
        self.assertion = (by, value)
        if by == By.TAG_NAME and value == 'body':
            if 'x/web-interface/nav' in self.current_url:
                return FakeBody(self.nav_body)
            return FakeBody('')
        raise AssertionError('unexpected element lookup')


class LoginServiceTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.multiple(
            globals,
            cookie_value='test-session',
            home_url='https://www.bilibili.com/',
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_cookie_login_returns_verified_account_uid(self):
        browser = FakeBrowser('{"code":0,"data":{"isLogin":true,"mid":35549700}}')

        uid = LoginService(browser, None, 'placeholder').login_by_cookie()

        self.assertEqual(uid, '35549700')
        self.assertEqual(browser.visited[0], globals.home_url)
        self.assertIn('https://api.bilibili.com/x/web-interface/nav', browser.visited)
        self.assertEqual(browser.cookies[0]['name'], 'SESSDATA')

    def test_cookie_login_retries_home_navigation_before_cookie_injection(self):
        browser = FakeBrowser('{"code":0,"data":{"isLogin":true,"mid":35549700}}')
        original_get = browser.get
        calls = {'n': 0}

        def flaky_get(url):
            original_get(url)
            calls['n'] += 1
            if calls['n'] == 1:
                browser.current_url = 'https://example.com/'

        browser.get = flaky_get

        uid = LoginService(browser, None, 'placeholder').login_by_cookie()

        self.assertEqual(uid, '35549700')
        self.assertGreaterEqual(calls['n'], 2)
        self.assertEqual(browser.cookies[0]['name'], 'SESSDATA')

    def test_cookie_login_restores_all_cookies_from_header(self):
        browser = FakeBrowser('{"code":0,"data":{"isLogin":true,"mid":35549700}}')

        with patch.object(
            globals,
            'cookie_value',
            'SESSDATA=test-session; bili_jct=test-csrf',
        ):
            uid = LoginService(browser, None, 'placeholder').login_by_cookie()

        self.assertEqual(uid, '35549700')
        self.assertEqual(
            [cookie['name'] for cookie in browser.cookies],
            ['SESSDATA', 'bili_jct'],
        )

    def test_write_actions_require_bili_jct_cookie(self):
        browser = FakeBrowser('{}')
        service = LoginService(browser, None, 'placeholder')
        browser.cookies = [{'name': 'SESSDATA', 'value': 'test-session'}]

        with self.assertRaisesRegex(BilibiliAuthenticationRequired, 'bili_jct'):
            service.ensure_publish_cookie()

        browser.cookies.append({'name': 'bili_jct', 'value': 'test-csrf'})
        service.ensure_publish_cookie()

    def test_cookie_login_rejects_expired_or_invalid_session(self):
        browser = FakeBrowser('{"code":0,"data":{"isLogin":false,"mid":0}}')

        with self.assertRaises(BilibiliAuthenticationRequired):
            LoginService(browser, None, 'placeholder').login_by_cookie()


if __name__ == '__main__':
    unittest.main()
