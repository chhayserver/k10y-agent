"""Kubernetes pages of the first cluster (skipped when the hub has none)."""
import pytest

from conftest import go

K8S = ["/k8s/overview", "/k8s/nodes", "/k8s/deployments", "/k8s/pods", "/k8s/services", "/k8s/storage", "/k8s/helm",
       "/k8s/rightsizing", "/k8s/certificates", "/k8s/events"]


@pytest.fixture()
def cluster_page(page):
    go(page, "/k8s/clusters")
    if page.locator("text=No clusters").count():
        pytest.skip("no cluster on this hub")
    return page


@pytest.mark.parametrize("path", K8S)
def test_kubernetes_page_opens(cluster_page, path):
    go(cluster_page, path, 3500)
    assert cluster_page.errors == [], cluster_page.errors


def test_deployment_panel_has_revisions_and_logs(cluster_page):
    go(cluster_page, "/k8s/deployments", 3500)
    rows = cluster_page.locator("table.data tbody tr")
    if not rows.count():
        pytest.skip("no deployments")
    rows.first.click()
    cluster_page.wait_for_timeout(2000)
    tabs = cluster_page.locator("button[role=tab]").all_inner_texts()
    assert any("Revisions" in t for t in tabs) and any("Logs" in t for t in tabs), tabs
