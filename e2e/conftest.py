"""Browser tests against a running hub. They only read: no clusters, HAProxy configs or settings are changed.

K10Y_E2E_URL       hub address (default https://host.docker.internal:8443)
K10Y_E2E_USER      a user with the Admin role (a test account, not your own)
K10Y_E2E_PASSWORD  its password (the account must not use two-factor login)
"""
import os

import pytest
from playwright.sync_api import sync_playwright

URL = os.environ.get("K10Y_E2E_URL", "https://host.docker.internal:8443").rstrip("/")


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    user, password = os.environ.get("K10Y_E2E_USER"), os.environ.get("K10Y_E2E_PASSWORD")
    if not user or not password:
        pytest.skip("set K10Y_E2E_USER and K10Y_E2E_PASSWORD")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    pg = ctx.new_page()
    pg.set_default_timeout(20000)
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    # Script errors fail a test. Failed requests don't: they depend on the environment (an agent that is down or
    # older than the hub), and the pages show those as messages.
    pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and pg.errors.append(m.text))
    pg.goto(URL + "/login")
    pg.fill("#u", user)
    pg.fill("#p", password)
    pg.click("button[type=submit]")
    pg.wait_for_url(lambda u: "/login" not in u)
    yield pg
    ctx.close()


def go(pg, path: str, wait_ms: int = 2500):
    pg.goto(URL + path)
    pg.wait_for_timeout(wait_ms)
