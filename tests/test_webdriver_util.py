import unittest
from unittest.mock import Mock, patch

from utils import globals
from utils.webdriver_util import ElementUtil, cleanup_stale_selenium_sessions, force_delete_selenium_session, online_driver


class WebDriverOptionsTests(unittest.TestCase):
    @patch('utils.webdriver_util.webdriver.Remote')
    def test_online_driver_can_use_verification_proxy_only(self, remote):
        remote.return_value = Mock()

        with patch.object(globals, 'verification_proxy_url', 'http://host.docker.internal:7897'):
            online_driver(proxy_url=globals.verification_proxy_url)

        options = remote.call_args.kwargs['options']
        self.assertIn('--proxy-server=http://host.docker.internal:7897', options.arguments)

    @patch('utils.webdriver_util.webdriver.Remote')
    def test_online_driver_uses_nonblocking_navigation_strategy(self, remote):
        remote.return_value = Mock()

        online_driver()

        options = remote.call_args.kwargs['options']
        self.assertEqual(options.page_load_strategy, 'none')
        self.assertIn('--blink-settings=imagesEnabled=false', options.arguments)
        self.assertIn('--disable-dev-shm-usage', options.arguments)
        remote.return_value.set_page_load_timeout.assert_called_once_with(
            globals.selenium_page_load_timeout
        )
        remote.return_value.set_script_timeout.assert_called_once_with(
            globals.selenium_script_timeout
        )

    def test_init_webdriver_forwards_proxy_to_online_driver(self):
        with patch.object(globals, 'DRIVER_VERSION', 'Online'), patch(
            'utils.webdriver_util.online_driver', return_value=('driver', 'chains')
        ) as online:
            from utils.webdriver_util import init_webdriver
            result = init_webdriver(proxy_url='http://172.21.0.1:7897')

        self.assertEqual(result, ('driver', 'chains'))
        online.assert_called_once_with(proxy_url='http://172.21.0.1:7897')

    def test_startup_cleanup_removes_every_stale_selenium_session(self):
        status = {
            'value': {
                'nodes': [{
                    'slots': [
                        {'session': {'sessionId': 'one'}},
                        {'session': {'sessionId': 'two'}},
                        {'session': None},
                    ],
                }],
            },
        }
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read = Mock(return_value=b'')
        response.__iter__ = Mock(return_value=iter([]))

        with patch('utils.webdriver_util.urllib.request.urlopen') as urlopen, patch(
            'utils.webdriver_util.json.load', return_value=status
        ):
            urlopen.return_value.__enter__ = Mock(return_value=response)
            urlopen.return_value.__exit__ = Mock(return_value=False)
            removed = cleanup_stale_selenium_sessions()

        self.assertEqual(removed, 2)
        self.assertEqual(urlopen.call_count, 3)

    def test_force_delete_session_calls_selenium_delete_endpoint(self):
        driver = Mock()
        driver.session_id = 'stale-session'
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch('utils.webdriver_util.urllib.request.urlopen') as urlopen:
            urlopen.return_value.__enter__ = Mock(return_value=response)
            urlopen.return_value.__exit__ = Mock(return_value=False)
            result = force_delete_selenium_session(driver)

        self.assertTrue(result)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), 'DELETE')
        self.assertTrue(request.full_url.endswith('/session/stale-session'))

    @patch('utils.webdriver_util.webdriver.Remote')
    def test_online_driver_cleans_up_when_timeout_configuration_fails(self, remote):
        driver = Mock()
        remote.return_value = driver
        driver.set_page_load_timeout.side_effect = RuntimeError('timeout config failed')

        with self.assertRaises(RuntimeError):
            online_driver()

        driver.quit.assert_called_once_with()

    def test_wait_for_page_load_accepts_interactive_document(self):
        class ReadyBrowser:
            def execute_script(self, script):
                return 'interactive'

        self.assertTrue(ElementUtil.wait_for_page_load(ReadyBrowser(), timeout=1))

    def test_wait_to_go_waits_for_a_new_window_from_previous_handles(self):
        class BrowserWithNewWindow:
            window_handles = ['main', 'profile']

        self.assertTrue(
            ElementUtil.wait_to_go(
                BrowserWithNewWindow(), ['main'], timeout=1
            )
        )


if __name__ == '__main__':
    unittest.main()
