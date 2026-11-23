import unittest
from unittest.mock import Mock, patch

from utils import globals
from utils.webdriver_util import ElementUtil, cleanup_stale_selenium_sessions, online_driver


class WebDriverOptionsTests(unittest.TestCase):
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
