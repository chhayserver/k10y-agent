"""Every main page opens without script errors, and the key controls are there."""
import pytest

from conftest import go

PAGES = ["/home", "/k8s/clusters", "/alerts", "/activity", "/settings", "/admin/users", "/admin/roles", "/admin/system"]


@pytest.mark.parametrize("path", PAGES)
def test_page_opens_without_errors(page, path):
    go(page, path)
    assert "/login" not in page.url
    assert page.locator("main, .content").first.is_visible()
    assert page.errors == [], page.errors


def test_search_opens_with_shortcut(page):
    go(page, "/home")
    page.keyboard.press("Control+k")
    page.keyboard.type("zz-no-such-thing")
    page.wait_for_selector('.gs-pop:has-text("Nothing named")', timeout=30000)   # every cluster is asked


def test_my_settings_has_two_factor_and_devices(page):
    go(page, "/settings")
    assert page.locator('h2:has-text("Two-factor login")').count() == 1
    assert page.locator('h2:has-text("Signed-in devices")').count() == 1


def test_system_settings_sections(page):
    go(page, "/admin/system", 3500)
    titles = page.locator(".section-bar h2").all_inner_texts()
    for t in ("Protected namespaces", "Sign-in security", "AI providers", "Alert channels"):
        assert any(t.lower() == x.lower() for x in titles), titles


def test_channel_dialog_offers_every_type(page):
    go(page, "/admin/system", 3000)
    page.click('button:has-text("Add channel")')
    for t in ("Telegram", "Slack", "Email", "Webhook"):
        assert page.locator(f'dialog[open] .segmented button:has-text("{t}")').count() == 1
    page.click('dialog[open] button:has-text("Cancel")')
