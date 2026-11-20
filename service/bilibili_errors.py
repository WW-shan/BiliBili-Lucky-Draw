import json
from urllib.parse import urlparse

from selenium.webdriver.common.by import By


class BilibiliVerificationRequired(RuntimeError):
    """Raised when Bilibili blocks a page pending manual verification."""


class BilibiliAuthenticationRequired(RuntimeError):
    """Raised when the configured Bilibili session is not authenticated."""


def is_verification_block(status_code=None, page_title='', page_text=''):
    if status_code is not None:
        try:
            if int(status_code) == 412:
                return True
        except (TypeError, ValueError):
            pass

    content = (str(page_title or '') + '\n' + str(page_text or '')).casefold()
    markers = (
        'http 412',
        'status code 412',
        'precondition failed',
        'request rejected',
        '安全风控',
        '请求被拒绝',
        '访问被拒绝',
    )
    return any(marker.casefold() in content for marker in markers)


def _url_key(url):
    parsed = urlparse(str(url or ''))
    return parsed.netloc.lower(), parsed.path.rstrip('/') or '/'


def document_response_status(browser, target_url):
    """Read a navigation status from Chrome performance logs when available."""
    try:
        entries = browser.get_log('performance')
    except Exception:
        return None

    target_host, target_path = _url_key(target_url)
    for entry in reversed(entries):
        try:
            message = json.loads(entry.get('message', '{}')).get('message', {})
            if message.get('method') != 'Network.responseReceived':
                continue
            params = message.get('params', {})
            response = params.get('response', {})
            if params.get('type') != 'Document':
                continue
            response_host, response_path = _url_key(response.get('url'))
            if (response_host, response_path) == (target_host, target_path):
                return int(response.get('status'))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def ensure_page_access(browser, target_url):
    status_code = document_response_status(browser, target_url)
    try:
        title = browser.title
    except Exception:
        title = ''
    try:
        body_text = browser.find_element(By.TAG_NAME, 'body').text or ''
    except Exception:
        body_text = getattr(browser, 'page_source', '') or ''

    if is_verification_block(status_code, title, body_text):
        raise BilibiliVerificationRequired(
            'Bilibili blocked %s with HTTP 412 or a verification page; '
            'complete the site verification manually before the next scan.'
            % target_url
        )
