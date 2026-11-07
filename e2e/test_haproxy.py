"""HAProxy pages (skipped when the hub has no HAProxy instance)."""
import pytest

from conftest import go

PAGES = ["/haproxy/overview", "/haproxy/frontends", "/haproxy/backends", "/haproxy/topology", "/haproxy/certificates",
         "/haproxy/stick-tables", "/haproxy/logs", "/haproxy/history"]


@pytest.mark.parametrize("path", PAGES)
def test_haproxy_page_opens(page, path):
    go(page, "/haproxy/overview", 2500)
    if page.locator("text=No HAProxy").count():
        pytest.skip("no HAProxy on this hub")
    go(page, path, 3500)
    assert page.errors == [], page.errors


def test_topology_draws_every_server(page):
    go(page, "/haproxy/topology", 4000)
    if page.locator("text=No HAProxy").count():
        pytest.skip("no HAProxy on this hub")
    assert page.locator("g.node.srv").count() == page.locator("g.edge.srv").count() > 0
