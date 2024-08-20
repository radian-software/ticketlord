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


browser = uc.Chrome(version_main=get_chromium_version())
browser.get(
    "https://auth.ticketmaster.com/as/authorization.oauth2?redirect_uri=psdktm://login&response_type=code&state=2979eb01-b7ee-4954-bc81-8fd82ba31d93&scope=openid%20profile%20phone%20email%20tm%20na&lang=en-us&client_id=ba33f3165c56.android.ticketmaster.us&integratorId=prd300.psdk&placementId=hostOnlyLogin&visualPresets=tm&intSiteToken=tm-us&hideLeftPanel=true&deviceId=554f315b-ba41-41b7-9de5-62a94271c41a"
)

fill_username_and_password(browser)
