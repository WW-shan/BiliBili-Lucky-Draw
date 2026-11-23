from selenium.webdriver.support.wait import WebDriverWait
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
import json
import urllib.error
import urllib.request

from utils import globals



class ElementUtil(object):
    def get_element_by_xpath(bro, chains, path):
        """
        获取element的通用方法
        :param bro:
        :param chains:
        :param path:
        :return:
        """
        return WebDriverWait(bro, 10).until(EC.element_to_be_clickable((By.XPATH, path)))

    def get_elementArr_by_xpath(bro, chains, path):
        return WebDriverWait(bro, 10).until(EC.presence_of_all_elements_located((By.XPATH, path)))

    def is_xpath_exist(bro, chains, path):
        try:
            WebDriverWait(bro, 5).until(EC.element_to_be_clickable((By.XPATH, path)))
            return True
        except:
            return False

    def wait_to_go(bro, previous_handles, timeout=10):
        return WebDriverWait(bro, timeout).until(
            EC.new_window_is_opened(previous_handles)
        )

    def wait_for_page_load(bro, timeout=10):
        return WebDriverWait(bro, timeout).until(
            lambda driver: driver.execute_script('return document.readyState')
            in ('interactive', 'complete')
        )

def local_driver():
    chrome_options = webdriver.ChromeOptions()
    # chrome_options.add_argument("--headless")  # 以无头模式运行Chrome
    chrome_options.add_argument("--no-sandbox")  # 取消沙盒模式
    chrome_options.add_argument("--disable-gpu")  # 取消GPU
    chrome_options.add_argument("--disable-extensions")  # 禁用插件加载
    chrome_options.add_argument("--disable-software-rasterizer")  # 禁用软件光栅化器
    chrome_options.add_argument('lang=zh_CN.UTF-8')
    chrome_options.add_argument('--enable-javascript')
    chrome_options.add_argument("--start-minimized")
    chrome_options.add_argument(
        '--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/115.0.5790.110 Safari/537.36')  # 替换User-Agent
    option = ChromeOptions()
    option.add_experimental_option('excludeSwitches', ['enable-automation'])
    s = Service(r"./lib/chromedriver.exe")
    bro = webdriver.Chrome(service=s, chrome_options=chrome_options, options=option)
    chains = ActionChains(bro)
    return bro, chains


def online_driver(proxy_url=None):
    """
        初始化Selenium信息———— 此版本用于生成Cookie，所以浏览器去掉无头模式
        :return:
        """
    # 设置浏览器信息
    chrome_options = webdriver.ChromeOptions()
    chrome_options.page_load_strategy = 'none'
    # chrome_options.add_argument("--headless")  # 以无头模式运行Chrome
    chrome_options.add_argument("--no-sandbox")  # 取消沙盒模式
    chrome_options.add_argument("--disable-gpu")  # 取消GPU
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    chrome_options.add_argument("--disable-extensions")  # 禁用插件加载
    chrome_options.add_argument('lang=zh_CN.UTF-8')
    chrome_options.add_argument('--window-size=1280,900')
    if proxy_url:
        chrome_options.add_argument('--proxy-server=' + str(proxy_url))
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    driver = webdriver.Remote(
        command_executor=globals.selenium_url,
        options=chrome_options
    )
    try:
        driver.set_page_load_timeout(globals.selenium_page_load_timeout)
        driver.set_script_timeout(globals.selenium_script_timeout)
        chains = ActionChains(driver)
        return driver, chains
    except Exception:
        # Remote 创建成功后，任何后续配置异常都必须主动释放会话；
        # 否则 Selenium 单会话槽会一直被占用，后续验证全部排队超时。
        try:
            driver.quit()
        except Exception:
            pass
        raise

def force_delete_selenium_session(driver):
    """Best-effort DELETE for a session whose quit() did not release the slot."""
    if driver is None:
        return False
    session_id = getattr(driver, 'session_id', None)
    if not session_id:
        return False
    base = globals.selenium_url.rstrip('/')
    request = urllib.request.Request(
        base + '/session/' + str(session_id), method='DELETE'
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except Exception:
        return False


def cleanup_stale_selenium_sessions():
    """Delete sessions left behind by a previous worker process."""
    base = globals.selenium_url.rstrip('/')
    try:
        with urllib.request.urlopen(base + '/status', timeout=10) as response:
            payload = json.load(response)
    except Exception:
        return 0
    removed = 0
    for node in (payload.get('value') or {}).get('nodes') or []:
        for slot in node.get('slots') or []:
            session_id = (slot.get('session') or {}).get('sessionId')
            if not session_id:
                continue
            request = urllib.request.Request(
                base + '/session/' + str(session_id), method='DELETE'
            )
            try:
                with urllib.request.urlopen(request, timeout=10):
                    removed += 1
            except Exception:
                continue
    return removed


def init_webdriver():

    if globals.DRIVER_VERSION == 'Local':
        return local_driver()
    if globals.DRIVER_VERSION == 'Online':
        return online_driver()
    # 默认本地驱动
    return local_driver()
