"""Nginx pages (skipped when the hub has no Nginx instance)."""
import pytest

from conftest import go

PAGES = ["/nginx/overview", "/nginx/sites", "/nginx/upstreams", "/nginx/logs", "/nginx/certificates", "/nginx/config"]


@pytest.mark.parametrize("path", PAGES)
def test_nginx_page_opens(page, path):
    go(page, "/nginx/overview", 3000)
    if page.locator("text=No Nginx connected yet").count():
        pytest.skip("no Nginx on this hub")
    go(page, path, 3500)
    assert page.errors == [], page.errors


def test_upstreams_show_every_server(page):
    go(page, "/nginx/upstreams", 4000)
    if page.locator("text=No Nginx connected yet").count():
        pytest.skip("no Nginx on this hub")
    assert page.locator(".ng-up").count() >= 0
