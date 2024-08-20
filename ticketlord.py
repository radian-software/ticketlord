#!/usr/bin/env python3

import argparse
from datetime import datetime, timedelta
import os
import re
import subprocess
import time

import dotenv
import requests
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
    opts = uc.ChromeOptions()
    opts.add_argument("--headless=new")
    browser = uc.Chrome(version_main=get_chromium_version(), options=opts)
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


def extract_cookies(browser):
    return {
        c["name"]: c["value"]
        for c in browser.get_cookies()
        if c["domain"] == ".ticketmaster.com"
    }


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def get_order_history(cookies):
    resp = requests.get(
        "https://www.ticketmaster.com/api/user/orders",
        params={"archive": "false"},
        headers={
            "user-agent": USER_AGENT,
            "x-tmlangcode": "en-us",
            "x-tmplatform": "global",
            "x-tmregion": "200",
            "x-username": TICKETMASTER_EMAIL,
        },
        cookies=cookies,
    )
    resp.raise_for_status()
    return resp.json()


def select_order(order_history, event_name):
    events_by_id = {event["id"]: event for event in order_history["events"]}
    event_names = set()
    for order in order_history["orders"]:
        events = [events_by_id[event["id"]] for event in order["events"]]
        if any(args.event_name.lower() in event["title"].lower() for event in events):
            return {**order, "events": events}
            event_names.add(event["title"] for event in events)
    raise RuntimeError(
        f"Event {repr(args.event_name)} not found in events: {repr(event_names)}"
    )


def get_tickets(order, cookies):
    tickets = {}
    order_id = order["usOrderId"]
    for event in order["events"]:
        event_id = event["id"]
        resp = requests.get(
            f"https://my.ticketmaster.com/deliver-tickets/async/json/order/{order_id}/view",
            params={"eventId": event_id},
            headers={"user-agent": USER_AGENT},
            cookies=cookies,
        )
        resp.raise_for_status()
        polling = resp.json()["pollingToken"]
        start_time = datetime.now()
        while True:
            resp = requests.get(
                f"https://my.ticketmaster.com/deliver-tickets/async/json/{order_id}/poll",
                params={"eventId": event_id, "token": polling},
                headers={"user-agent": USER_AGENT},
                cookies=cookies,
            )
            resp.raise_for_status()
            if resp.status_code == 200:
                break
            time.sleep(2)
            assert (datetime.now() - start_time) < timedelta(seconds=60), "timed out"
        tickets.update(resp.json()["tokenMap"])
    return tickets


parser = argparse.ArgumentParser()
parser.add_argument("event_name")
args = parser.parse_args()


browser = create_browser()
browser.get("https://www.ticketmaster.com/")
click_login_button(browser)
fill_username_and_password(browser)
wait_for_login_to_finish(browser)
cookies = extract_cookies(browser)

browser.get("https://www.ticketmaster.com/user/orders")
order_history = get_order_history(cookies)
order = select_order(order_history, args.event_name)
tickets = get_tickets(order, cookies)
