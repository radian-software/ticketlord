#!/usr/bin/env python3

from datetime import datetime, timedelta
import os
import re
import subprocess
import time

import dotenv
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc


dotenv.load_dotenv()

TICKETMASTER_EMAIL = os.environ["TICKETMASTER_EMAIL"]
TICKETMASTER_PASSWORD = os.environ["TICKETMASTER_PASSWORD"]


def get_chromium_version():
    stdout = subprocess.run(
        ["chromium-browser", "--version"], check=True, stdout=subprocess.PIPE
    ).stdout.decode()
    match = re.search(r"[0-9]+", stdout)
    assert match, stdout
    return int(match.group(0))


def create_browser():
    # https://github.com/ultrafunkamsterdam/undetected-chromedriver/issues/491
    browser = uc.Chrome(version_main=get_chromium_version())
    browser.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {"width": 375, "height": 812, "deviceScaleFactor": 50, "mobile": True},
    )
    return browser


def click_login_button(browser):
    span = browser.find_element(By.CSS_SELECTOR, "button[data-testid='accountLink']")
    span.click()


def fill_username_and_password(browser):
    start_time = datetime.now()
    while True:
        time.sleep(1)
        try:
            email_input = browser.find_element(By.CSS_SELECTOR, "input[name='email']")
            password_input = browser.find_element(
                By.CSS_SELECTOR, "input[name='password']"
            )
            break
        except NoSuchElementException:
            pass
        assert (datetime.now() - start_time) < timedelta(seconds=60), "timed out"
    email_input.clear()
    email_input.send_keys(TICKETMASTER_EMAIL)
    password_input.clear()
    password_input.send_keys(TICKETMASTER_PASSWORD)
    try:
        rememberme_box = browser.find_element(
            By.CSS_SELECTOR, "input[name='rememberMe']"
        )
        rememberme_label = rememberme_box.find_element(By.XPATH, ".//ancestor::label")
        if not rememberme_box.is_selected():
            rememberme_label.click()
    except NoSuchElementException:
        pass
    login_button = browser.find_element(By.CSS_SELECTOR, "button[name='sign-in']")
    login_button.click()


def wait_for_login_to_finish(browser):
    start_time = datetime.now()
    while True:
        time.sleep(1)
        if browser.current_url == "https://www.ticketmaster.com/":
            break
        assert (datetime.now() - start_time) < timedelta(seconds=60), "timed out"


browser = create_browser()
browser.get("https://www.ticketmaster.com/")
click_login_button(browser)
fill_username_and_password(browser)
wait_for_login_to_finish(browser)
