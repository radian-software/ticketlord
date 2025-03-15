#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4 as get_random_uuid

import dotenv
import requests
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc


dotenv.load_dotenv()

TICKETMASTER_EMAIL = os.environ["TICKETMASTER_EMAIL"]
TICKETMASTER_PASSWORD = os.environ["TICKETMASTER_PASSWORD"]
TICKETMASTER_API_KEY = os.environ["TICKETMASTER_API_KEY"]

CREDS_FILE = Path.home() / ".cache/ticketlord/android.json"
PSDKTM_FILE = Path("/tmp/ticketlord-psdktm.dat")


@dataclass
class Creds:
    access_token: str

    @property
    def tmx_headers(self) -> dict[str, str]:
        return {
            "X-API-Key": TICKETMASTER_API_KEY,
            "Access-Token-Host": self.access_token,
        }

    def save(self) -> None:
        CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CREDS_FILE, "w") as f:
            json.dump(
                {
                    "access_token": self.access_token,
                    "timestamp": int(datetime.now().timestamp()),
                },
                f,
                indent=2,
            )
            f.write("\n")


@dataclass
class Order:
    order_id: str
    legacy_order_id: str


def load_creds() -> Creds | None:
    try:
        with open(CREDS_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    if datetime.now() - datetime.fromtimestamp(data["timestamp"]) > timedelta(hours=1):
        return None
    return Creds(data["access_token"])


def get_auth_url() -> str:
    query = {
        "redirect_uri": "psdktm://login",
        "response_type": "code",
        "state": str(get_random_uuid()),
        "scope": "openid profile phone email tm na",
        "lang": "en-us",
        "client_id": "ba33f3165c56.android.ticketmaster.us",
        "integratorId": "prd300.psdk",
        "placementId": "null",
        "visualPresets": "tm",
        "intSiteToken": "tm-us",
        "hideLeftPanel": "true",
        "deviceId": str(get_random_uuid()),
    }
    return "https://auth.ticketmaster.com/as/authorization.oauth2?" + urlencode(query)


def get_chromium_version() -> int:
    stdout = subprocess.run(
        ["chromium-browser", "--version"], check=True, stdout=subprocess.PIPE
    ).stdout.decode()
    match = re.search(r"[0-9]+", stdout)
    assert match, stdout
    return int(match.group(0))


def create_browser():
    # https://github.com/ultrafunkamsterdam/undetected-chromedriver/issues/491
    opts = uc.ChromeOptions()
    browser = uc.Chrome(version_main=get_chromium_version(), options=opts)
    browser.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {"width": 375, "height": 812, "deviceScaleFactor": 50, "mobile": True},
    )
    return browser


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


def check_authenticated(creds: Creds):
    resp = requests.get(
        "https://app.ticketmaster.com/tmx-prod/v1/member/account/details.json",
        headers=creds.tmx_headers,
    )
    resp.raise_for_status()


def exchange_auth_code(auth_code: str) -> str:
    resp = requests.post(
        "https://app.ticketmaster.com/tmx-prod/v1/accounts/exchange",
        headers={
            "X-API-Key": TICKETMASTER_API_KEY,
        },
        json={
            "authorizationCode": auth_code,
            "redirectUri": "psdktm://login",
        },
    )
    resp.raise_for_status()
    return resp.json()["accessToken"]


def get_events(creds: Creds) -> list[Any]:
    resp = requests.get(
        "https://app.ticketmaster.com/tmx-prod/v1/events.json",
        headers=creds.tmx_headers,
    )
    resp.raise_for_status()
    return resp.json()["events"]


def select_event_orders(creds: Creds, events: Any, args) -> list[Order]:
    assert events, "no events returned"
    event_names = set()
    for event in sorted(events, key=lambda event: event["event_date"]["datetime_utc"]):
        if args.event_name.lower() in event["name"].lower():
            return [
                Order(o["order_id"], o["legacy_order_id"]) for o in event["host_orders"]
            ]
        event_names.add(event["name"])
    raise RuntimeError(
        f"Event {repr(args.event_name)} not found in events: {repr(event_names)}"
    )


def get_tickets(creds: Creds, orders: list[Order]) -> list[Any]:
    resp = requests.get(
        "https://app.ticketmaster.com/tmx-prod/v1/events/securetickets.json?"
        + urlencode(
            {
                "orderIds[]": ",".join(o.order_id for o in orders),
                "tapOrderIds[]": ",".join(o.legacy_order_id for o in orders),
                "tapEventIds[]": ",".join("null" for o in orders),
            }
        ),
        headers=creds.tmx_headers,
    )
    resp.raise_for_status()
    return resp.json()["tickets"]


def display_tickets(tickets):
    for idx, ticket in enumerate(tickets, start=1):
        print()
        print(
            f"TICKET #{idx} ID :: {ticket['ticket_id']} :: STATUS={ticket['ticket_status']}"
        )
        print()
        if ticket["delivery"]["status"] == "DISABLED":
            print("  (ticket delivery disabled)")
        else:
            for text in ticket["ticket_text_lines"]:
                print(text)
            print()
            print(
                f"Gate {ticket['entry_gate']}, Section {ticket['section_label']}, Row {ticket['row_label']}, Seat {ticket['seat_label']}"
            )
            print()
            print(f"Rotating barcode token: {ticket['delivery']['secure_token']}")


parser = argparse.ArgumentParser()
parser.add_argument("event_name", nargs="?", default="")
args = parser.parse_args()

print("load creds")
creds = load_creds()

try:

    print("check authentication")
    assert creds
    check_authenticated(creds)

except Exception:

    PSDKTM_FILE.unlink(missing_ok=True)

    print("create browser")
    browser = create_browser()

    print("navigate to auth page")
    browser.get(get_auth_url())

    print("fill username and password")
    fill_username_and_password(browser)

    print("wait for callback file")
    for i in range(15):
        if PSDKTM_FILE.exists():
            break
        time.sleep(1)

    print("destroy browser")
    browser.close()

    print("read callback file")
    with open(PSDKTM_FILE) as f:
        callback_uri = f.read()

    print("extract authorization code")
    auth_code = parse_qs(urlparse(callback_uri).query)["code"][0]

    print("exchange authorization code")
    access_token = exchange_auth_code(auth_code)

    print("save authentication")
    creds = Creds(access_token)
    creds.save()

    print("check authentication")
    check_authenticated(creds)

print("query upcoming events")
events = get_events(creds)

print("identify selected orders")
orders = select_event_orders(creds, events, args)

print("retrieve tickets")
tickets = get_tickets(creds, orders)

print("display tickets")
display_tickets(tickets)
