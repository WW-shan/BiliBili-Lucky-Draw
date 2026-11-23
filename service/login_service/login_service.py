import json
import time
from urllib.parse import unquote, urlparse
from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidCookieDomainException, TimeoutException
from selenium.webdriver.support.wait import WebDriverWait
from service.bilibili_errors import (
    BilibiliAuthenticationRequired,
    BilibiliVerificationRequired,
    ensure_page_access,
)
from service.log_service.log_printer_service import MyLogger
from utils import globals
from utils.file_util import append_data_to_env
from utils.time_util import random_sleep
from utils.webdriver_util import ElementUtil, init_webdriver

mylogger = MyLogger('login_service.py').getLogger()


class LoginService(object):
    def __init__(self, bro, chains, my_user_id='0'):
        self.bro = bro
        self.chains = chains
        self.my_user_id = my_user_id
        mylogger.info('启动登录模块')

    def login_manual(self):
        self.bro.get(globals.home_url)
        while ElementUtil.is_xpath_exist(self.bro, self.chains,
                                         '//*[@id="i_cecream"]/div[2]/div[1]/div[1]/ul[2]/li[1]/li/div') is True:
            random_sleep()
        dict_cookies = self.bro.get_cookies()
        json_cookies = json.dumps(dict_cookies)
        try:
            url = self.bro.find_element(By.XPATH,
                                        '//*[@id="i_cecream"]/div[2]/div[1]/div[1]/ul[2]/li[1]/div[1]/a[1]').get_attribute(
                "href")
        except Exception as e:
            print(e)
        result = urlparse(url)
        id = str(result[2])[1:]
        cookie_path = './cookie/' + id + '.txt'
        with open(cookie_path, 'w') as f:
            f.write(json_cookies)

    def login_by_cookie(self):
        """
        根据保存的Cookie信息进行登录
        :param bro:
        :return:
        """
        cookie_value = str(globals.cookie_value or '').strip()
        if not cookie_value:
            raise BilibiliAuthenticationRequired('未配置 SESSDATA，请先配置有效登录会话。')

        try:
            self.bro.get(globals.home_url)
            try:
                WebDriverWait(self.bro, 10).until(
                    lambda driver: self._is_bilibili_host(
                        urlparse(driver.current_url).hostname
                    )
                )
            except TimeoutException:
                # Selenium occasionally starts on an intermediate/blank origin.
                # Navigate again before injecting domain cookies.
                self.bro.get(globals.home_url)
                WebDriverWait(self.bro, 10).until(
                    lambda driver: self._is_bilibili_host(
                        urlparse(driver.current_url).hostname
                    )
                )
            ensure_page_access(self.bro, globals.home_url)

            cookies = self._parse_cookie_value(cookie_value)
            if not any(cookie['name'] == 'SESSDATA' for cookie in cookies):
                raise BilibiliAuthenticationRequired(
                    'Cookie 配置中缺少 SESSDATA，请更新登录会话。'
                )
            for cookie in cookies:
                cookie.setdefault('domain', '.bilibili.com')
                cookie.setdefault('path', '/')
                if cookie.get('expiry') and cookie['expiry'] <= time.time():
                    if cookie['name'] == 'SESSDATA':
                        raise BilibiliAuthenticationRequired(
                            'SESSDATA 已过期，请更新登录会话。'
                        )
                    continue
                try:
                    self.bro.add_cookie(cookie)
                except InvalidCookieDomainException:
                    # Navigation can still be settling on an intermediate
                    # origin; re-enter the Bilibili page before injecting.
                    self.bro.get(globals.home_url)
                    WebDriverWait(self.bro, 10).until(
                        lambda driver: self._is_bilibili_host(
                            urlparse(driver.current_url).hostname
                        )
                    )
                    self.bro.add_cookie(cookie)
            self.bro.refresh()
            ensure_page_access(self.bro, globals.home_url)

            nav_url = 'https://api.bilibili.com/x/web-interface/nav'
            self.bro.get(nav_url)
            WebDriverWait(self.bro, 10).until(
                lambda driver: urlparse(driver.current_url).hostname == 'api.bilibili.com'
            )
            ensure_page_access(self.bro, nav_url)

            try:
                nav_data = WebDriverWait(self.bro, 15).until(
                    self._read_nav_response
                )
            except TimeoutException as exc:
                ensure_page_access(self.bro, nav_url)
                raise RuntimeError('Bilibili 导航接口未及时返回 JSON。') from exc

            ensure_page_access(self.bro, nav_url)
            account = nav_data.get('data') or {}
            if not account.get('isLogin') or not account.get('mid'):
                raise BilibiliAuthenticationRequired(
                    'Bilibili 未确认当前登录状态，请更新 SESSDATA。'
                )

            uid = str(account['mid'])
            mylogger.info('Bilibili 登录校验通过，账号 UID=%s', uid)
            return uid
        except (BilibiliAuthenticationRequired, BilibiliVerificationRequired):
            raise
        except Exception as e:
            mylogger.error('登录校验失败：%s', e, exc_info=True)
            raise RuntimeError(
                '无法完成 Bilibili 登录校验，请检查网络和浏览器状态。'
            ) from e

    @staticmethod
    def _is_bilibili_host(hostname):
        host = (hostname or '').lower()
        return host == 'bilibili.com' or host.endswith('.bilibili.com')

    @staticmethod
    def _parse_cookie_value(cookie_value):
        value = str(cookie_value or '').strip()
        if value.startswith('['):
            cookies = json.loads(value)
            if not isinstance(cookies, list):
                raise BilibiliAuthenticationRequired('Cookie JSON 必须是 Cookie 列表。')
            return [dict(cookie) for cookie in cookies if isinstance(cookie, dict)]

        if ';' in value and '=' in value:
            if value.lower().startswith('cookie:'):
                value = value.split(':', 1)[1].strip()
            cookies = []
            for item in value.split(';'):
                name, separator, cookie_value = item.strip().partition('=')
                if separator and name:
                    cookies.append({
                        'name': name,
                        'value': cookie_value,
                        'domain': '.bilibili.com',
                        'path': '/',
                    })
            return cookies

        cookie = {'name': 'SESSDATA', 'value': value, 'domain': '.bilibili.com', 'path': '/'}
        cookie_parts = unquote(value).split(',', 2)
        if len(cookie_parts) > 1 and cookie_parts[1].isdigit():
            cookie['expiry'] = int(cookie_parts[1])
        return [cookie]

    def ensure_publish_cookie(self):
        cookies = self.bro.get_cookies()
        if not any(
            cookie.get('name') == 'bili_jct' and cookie.get('value')
            for cookie in cookies
        ):
            raise BilibiliAuthenticationRequired(
                '账号登录有效，但 Cookie 缺少 bili_jct；已停止评论和转发，请配置完整 Cookie。'
            )
        return True

    @staticmethod
    def _read_nav_response(browser):
        try:
            body_text = browser.find_element(By.TAG_NAME, 'body').text
            data = json.loads(body_text)
            return data if isinstance(data, dict) else False
        except (json.JSONDecodeError, ValueError):
            return False

    def login_by_cookie2(self):
        """
        根据保存的Cookie信息进行登录
        :param bro:
        :return:
        """
        try:
            cookie_path = './cookie/' + self.my_user_id + '.txt'
            self.bro.get(globals.home_url)
            with open(cookie_path, 'r', encoding='utf-8') as f:
                cookies = f.readlines()
            for cookie in cookies:
                cookie = cookie.replace(r'\n', '')
                cookie_li = json.loads(cookie)
                random_sleep(start=1, end=2)
                for cookie in cookie_li:
                    self.bro.add_cookie(cookie)
                self.bro.refresh()
            mylogger.info('使用cookie自动登录成功！')
            random_sleep(start=1, end=2)
        except Exception as e:
            mylogger.error('登录失败')
            mylogger.error("[出错原因为：%s]" % e)
