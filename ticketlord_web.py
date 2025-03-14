#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback
from urllib.parse import parse_qs, urlparse
import uuid

import dotenv
import requests
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc


dotenv.load_dotenv()

TICKETMASTER_EMAIL = os.environ["TICKETMASTER_EMAIL"]
TICKETMASTER_PASSWORD = os.environ["TICKETMASTER_PASSWORD"]

COOKIES_FILE = Path.home() / ".cache/ticketlord/cookies.json"


def get_chromium_version():
    stdout = subprocess.run(
        ["chromium-browser", "--version"], check=True, stdout=subprocess.PIPE
    ).stdout.decode()
    match = re.search(r"[0-9]+", stdout)
    assert match, stdout
    return int(match.group(0))


def load_cookies():
    try:
        with open(COOKIES_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    if datetime.now() - datetime.fromtimestamp(data["timestamp"]) > timedelta(days=1):
        return None
    return data["cookies"]


def save_cookies(cookies):
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(
            {
                "cookies": cookies,
                "timestamp": int(datetime.now().timestamp()),
            },
            f,
            indent=2,
        )
        f.write("\n")


def create_browser(cookies=None):
    # https://github.com/ultrafunkamsterdam/undetected-chromedriver/issues/491
    opts = uc.ChromeOptions()
    # opts.add_argument("--headless=new")
    browser = uc.Chrome(version_main=get_chromium_version(), options=opts)
    browser.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {"width": 375, "height": 812, "deviceScaleFactor": 50, "mobile": True},
    )
    if cookies:
        browser.get("https://www.ticketmaster.com/robots.txt")
        for cookie in cookies:
            browser.add_cookie(cookie)
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


def extract_cookies(cookies):
    if cookies is None:
        return None
    return {
        c["name"]: c["value"] for c in cookies if c["domain"] == ".ticketmaster.com"
    }


USER_AGENT = "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Mobile Safari/537.36"


def check_authenticated(cookies):
    resp = requests.get(
        "https://identity.ticketmaster.com/json/user",
        params={"hard": "false"},
        headers={
            "user-agent": USER_AGENT,
        },
        cookies=cookies,
    )
    resp.raise_for_status()
    actual_email = resp.json()["emailAddress"]
    assert actual_email == TICKETMASTER_EMAIL, actual_email


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


def poll_until_success(requestor):
    start_time = datetime.now()
    while True:
        print("wait for polling")
        resp = requestor()
        resp.raise_for_status()
        if resp.status_code == 200:
            return resp
        time.sleep(2)
        assert (datetime.now() - start_time) < timedelta(seconds=60), "timed out"


def get_order_items(order_id, cookies):
    resp = requests.get(
        f"https://my.ticketmaster.com/view-order/async/json/order/{order_id}",
        params={"lang": "en-us"},
        headers={"user-agent": USER_AGENT},
        cookies=cookies,
    )
    resp.raise_for_status()
    polling = resp.json()["pollingToken"]
    resp = poll_until_success(
        lambda: requests.get(
            f"https://my.ticketmaster.com/view-order/async/json/order/token/{polling}",
            headers={"user-agent": USER_AGENT},
            cookies=cookies,
        )
    )
    assert "items" in resp.json(), resp.json()
    return resp.json()["items"]


def get_tickets_info(order_item, cookies):
    assert "viewTickets" in order_item["_links"], order_item["_links"].keys()
    view_link = order_item["_links"]["viewTickets"]["source"]
    event_id = parse_qs(urlparse(view_link).query)["eventId"][0]
    statuses = {
        ticket["barcode"]: ticket["status"]
        for ticket in order_item["tickets"]
        if ticket.get("barcode")
    }
    resp = requests.get(
        f"https://my.ticketmaster.com/deliver-tickets/async/json/order/{order_id}/view",
        params={"eventId": event_id},
        headers={"user-agent": USER_AGENT},
        cookies=cookies,
    )
    resp.raise_for_status()
    polling = resp.json()["pollingToken"]
    resp = poll_until_success(
        lambda: requests.get(
            f"https://my.ticketmaster.com/deliver-tickets/async/json/order/{order_id}/poll",
            params={"eventId": event_id, "token": polling},
            headers={"user-agent": USER_AGENT},
            cookies=cookies,
        )
    )
    assert "outputs" in resp.json(), resp.json()
    outputs = resp.json()["outputs"]
    for ticket in outputs:
        ticket["status"] = statuses[ticket["data"]["value"].removesuffix("e")]
    return outputs


def get_tickets_detail(tickets_info, cookies):
    tickets = []
    resp = requests.post(
        f"https://my.ticketmaster.com/deliver-tickets/async/json/order/{order_id}/ret",
        params={"safeTix": "true"},
        headers={"user-agent": USER_AGENT},
        cookies=cookies,
        json={
            "deviceId": str(uuid.uuid4()),
            "deviceOs": "ANDROID",
            "deviceType": "WEB",
            "tickets": [
                {
                    "barcode": ticket["data"]["value"],
                    "eventId": ticket["eventId"],
                    "generalAdmission": ticket["data"]["generalAdmission"],
                    "row": ticket["data"]["row"],
                    "seat": ticket["data"]["seat"],
                    "section": ticket["data"]["section"],
                }
                for ticket in tickets_info
            ],
        },
    )
    resp.raise_for_status()
    polling = resp.json()["pollingToken"]
    start_time = datetime.now()
    resp = poll_until_success(
        lambda: requests.get(
            f"https://my.ticketmaster.com/deliver-tickets/async/json/{order_id}/ret/poll",
            params={"token": polling},
            headers={"user-agent": USER_AGENT},
            cookies=cookies,
        )
    )
    assert "tokenMap" in resp.json(), resp.json()
    token_map = resp.json()["tokenMap"]
    for ticket in tickets_info:
        tickets.append(
            {
                **ticket,
                **token_map[ticket["data"]["value"]],
            }
        )
    return tickets


def display_tickets(tickets):
    for idx, ticket in enumerate(tickets, start=1):
        print()
        print(
            f"TICKET #{idx} ID :: {ticket['data']['value']} :: STATUS={ticket['status']}"
        )
        print()
        for text in ticket["data"]["texts"]:
            print(text)
        print()
        print(
            f"Section {ticket['data']['section']}, Row {ticket['data']['row']}, Seat {ticket['data']['seat']}"
        )
        print()
        print(f"Rotating barcode token: {ticket['barcode']}")


parser = argparse.ArgumentParser()
parser.add_argument("event_name")
args = parser.parse_args()


print("load cookies")
raw_cookies = load_cookies()
cookies = extract_cookies(raw_cookies)


try:

    print("check authentication")
    check_authenticated(cookies)

except Exception:

    print("create browser")
    browser = create_browser(cookies)

    print("navigate to homepage")
    browser.get("https://www.ticketmaster.com/")

    print("click login button")
    click_login_button(browser)

    print("fill username and password")
    fill_username_and_password(browser)

    print("wait for login to finish")
    wait_for_login_to_finish(browser)

    print("navigate to orders page")
    browser.get("https://www.ticketmaster.com/user/orders")

    print("save cookies")
    raw_cookies = browser.get_cookies()
    cookies = extract_cookies(raw_cookies)
    save_cookies(raw_cookies)

    print("recheck authentication")
    check_authenticated(cookies)

print("get order history")
order_history = get_order_history(cookies)

print("identify selected order")
order = select_order(order_history, args.event_name)
order_id = order["usOrderId"]

print("get order items")
order_items = get_order_items(order_id, cookies)

tickets = []
for item in order_items:
    print(f"get tickets info (order item {item['id']})")
    tickets_info = get_tickets_info(item, cookies)
    print(f"get tickets detail (order item {item['id']})")
    tickets.extend(get_tickets_detail(tickets_info, cookies))

print("display tickets")
display_tickets(tickets)
