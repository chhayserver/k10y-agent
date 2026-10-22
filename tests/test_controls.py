import pytest

from k10y_agent.controls import ActionError, Controller, ProtectedNamespaceError


class _NoApi:
    """Any Kubernetes call fails the test: guards must reject before calling the API."""

    def __getattr__(self, name):
        raise AssertionError(f"API call {name} should not happen")


def _controller():
    c = Controller.__new__(Controller)
    c.core = c.apps = _NoApi()
    c.protected = {"kube-system"}
    c._dynamic = None
    return c


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.scale("kube-system", "x", 1),
        lambda c: c.restart("kube-system", "x"),
        lambda c: c.set_image("kube-system", "x", "c", "img"),
        lambda c: c.rollback("kube-system", "x"),
        lambda c: c.delete_deployment("kube-system", "x"),
        lambda c: c.delete_pod("kube-system", "x"),
        lambda c: c.delete_service("kube-system", "x"),
    ],
)
def test_protected_namespace_is_rejected(call):
    with pytest.raises(ProtectedNamespaceError):
        call(_controller())


def test_negative_replicas_rejected():
    with pytest.raises(ActionError):
        _controller().scale("demo", "x", -1)


@pytest.mark.parametrize("manifest", ["", "---\n", ": not yaml : [", "kind: ConfigMap\nmetadata: {name: a}\n"])
def test_apply_rejects_invalid_manifests(manifest):
    with pytest.raises(ActionError):
        _controller().apply(manifest)


def test_configmap_and_secret_delete_respect_protection():
    c = _controller()
    with pytest.raises(ProtectedNamespaceError):
        c.delete_configmap("kube-system", "x")
    with pytest.raises(ProtectedNamespaceError):
        c.delete_secret("kube-system", "x")
