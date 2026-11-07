from fastapi.testclient import TestClient

from k10y_agent import api


def test_token_check_works_on_every_route(monkeypatch):
    monkeypatch.setattr(api.settings, "api_token", "t0ken")
    client = TestClient(api.app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/audit").status_code == 401
    ok = client.get("/api/v1/audit", headers={"Authorization": "Bearer t0ken"})
    assert ok.status_code == 200, ok.text


def test_module_names_not_shadowed():
    import secrets

    assert api.secrets is secrets


def test_agent_serves_no_ui_and_no_cors_by_default():
    client = TestClient(api.app)
    root = client.get("/")
    assert root.status_code == 200 and root.json()["service"] == "k10y-agent"
    assert "<html" not in root.text.lower()
    r = client.options("/api/v1/summary", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_fails_closed_when_token_secret_cannot_be_read(monkeypatch):
    monkeypatch.setattr(api.settings, "api_token", "")
    monkeypatch.setattr(api.settings, "token_secret", "k10y-agent")
    monkeypatch.setattr(api, "NAMESPACE_FILE", api.Path("/nonexistent/namespace"))
    r = TestClient(api.app).get("/api/v1/audit")
    assert r.status_code == 503
    assert "API token not available" in r.json()["detail"]


def test_token_created_then_reused(monkeypatch, tmp_path):
    import base64 as b64
    from kubernetes.client.rest import ApiException

    store = {}

    class FakeCore:
        def read_namespaced_secret(self, name, ns):
            if name not in store:
                raise ApiException(status=404)
            return type("S", (), {"data": {"api-token": b64.b64encode(store[name].encode()).decode()}})()

        def create_namespaced_secret(self, ns, body):
            store[body.metadata.name] = body.string_data["api-token"]

    nsfile = tmp_path / "namespace"
    nsfile.write_text("k10y")
    monkeypatch.setattr(api, "NAMESPACE_FILE", nsfile)
    monkeypatch.setattr(api.client, "CoreV1Api", lambda *_: FakeCore())
    monkeypatch.setattr(api, "get_api_client", lambda: None)
    monkeypatch.setattr(api.settings, "token_secret", "k10y-agent")
    for _ in range(2):
        monkeypatch.setattr(api.settings, "api_token", "")
        api.provision_token()
        assert api.settings.api_token == store["k10y-agent"] and len(store["k10y-agent"]) == 48
    assert len(store) == 1


def test_edit_resource_goes_through_control_and_audit(monkeypatch):
    calls = []

    class FakeController:
        def replace_object(self, manifest, kind, namespace, name, dry_run):
            calls.append((kind, namespace, name, dry_run))
            return {"message": f"saved {kind} {namespace}/{name}"}

    monkeypatch.setattr(api.settings, "api_token", "")
    monkeypatch.setattr(api.settings, "control_enabled", True)
    monkeypatch.setattr(api, "get_controller", lambda: FakeController())
    client = TestClient(api.app)
    r = client.put("/api/v1/resource", json={"kind": "ConfigMap", "namespace": "demo", "name": "app", "yaml": "x: 1", "dry_run": True})
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert calls == [("ConfigMap", "demo", "app", True)]
    monkeypatch.setattr(api.settings, "control_enabled", False)
    assert client.put("/api/v1/resource", json={"kind": "ConfigMap", "namespace": "demo", "name": "app", "yaml": "x"}).status_code == 403


def test_replace_object_rejects_renames_secrets_and_missing_version():
    from k10y_agent.controls import ActionError, Controller
    ctl = Controller.__new__(Controller)
    ctl.protected = {"kube-system"}
    good = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: app, namespace: demo, resourceVersion: '5'}\n"
    for manifest, kind, ns, name, err in [
        (good, "Secret", "demo", "app", "Secrets cannot be edited"),
        (good, "ConfigMap", "demo", "other", "cannot change"),
        (good.replace(", resourceVersion: '5'", ""), "ConfigMap", "demo", "app", "resourceVersion"),
        (good + "---\n" + good, "ConfigMap", "demo", "app", "exactly one"),
    ]:
        with __import__("pytest").raises(ActionError, match=err):
            ctl.replace_object(manifest, kind, ns, name)
    from k10y_agent.controls import ProtectedNamespaceError
    with __import__("pytest").raises(ProtectedNamespaceError):
        ctl.replace_object(good.replace("demo", "kube-system"), "ConfigMap", "kube-system", "app")


def test_exec_websocket_needs_token_and_control(monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(api.settings, "api_token", "t0ken")
    monkeypatch.setattr(api.settings, "control_enabled", True)
    client = TestClient(api.app)
    with __import__("pytest").raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/v1/pods/demo/p/exec"):
            pass
    assert e.value.code == 4401
    with __import__("pytest").raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/v1/pods/kube-system/p/exec", headers={"Authorization": "Bearer t0ken"}):
            pass
    assert e.value.code == 4403


def test_protected_namespaces_saved_in_token_secret_and_restored(monkeypatch, tmp_path):
    import base64 as b64

    secret = {"api-token": b64.b64encode(b"t").decode()}

    class FakeCore:
        def read_namespaced_secret(self, name, ns):
            return type("S", (), {"data": dict(secret)})()

        def patch_namespaced_secret(self, name, ns, body):
            assert name == "k10y-agent" and ns == "k10y"
            secret.update({k: b64.b64encode(v.encode()).decode() for k, v in body["stringData"].items()})

    class FakeController:
        protected = {"kube-system"}

    class FakeCollector:
        def version(self):
            return "v1.30"

    nsfile = tmp_path / "namespace"
    nsfile.write_text("k10y")
    ctl = FakeController()
    monkeypatch.setattr(api, "NAMESPACE_FILE", nsfile)
    monkeypatch.setattr(api.client, "CoreV1Api", lambda *_: FakeCore())
    monkeypatch.setattr(api, "get_api_client", lambda: None)
    monkeypatch.setattr(api, "get_controller", lambda: ctl)
    monkeypatch.setattr(api, "get_collector", lambda: FakeCollector())
    monkeypatch.setattr(api.settings, "token_secret", "k10y-agent")
    monkeypatch.setattr(api.settings, "api_token", "")
    monkeypatch.setattr(api.settings, "protected_namespaces", "kube-system")
    monkeypatch.setattr(api, "protected_source", "env")
    monkeypatch.setattr(api, "audit_log", type("A", (), {"entries": [], "record": lambda self, e: self.entries.append(e)})())
    client = TestClient(api.app, headers={"Authorization": "Bearer t"})  # token read from the same Secret

    assert client.get("/api/v1/info").json()["protected_source"] == "env"
    r = client.put("/api/v1/settings/protected-namespaces", json={"namespaces": ["k10y", "kube-system", "cert-manager", "k10y"]},
                   headers={"X-K10Y-Actor": "hub:alice"})
    assert r.status_code == 200, r.text
    assert r.json()["protected_namespaces"] == ["cert-manager", "k10y", "kube-system"] and r.json()["protected_source"] == "hub"
    assert ctl.protected == {"cert-manager", "k10y", "kube-system"}
    entry = api.audit_log.entries[-1]
    assert entry["actor"] == "hub:alice" and entry["params"]["added"] == ["cert-manager", "k10y"]

    assert client.put("/api/v1/settings/protected-namespaces", json={"namespaces": ["Bad_Name"]}).status_code == 400

    # After a restart the list comes back from the Secret, not the env default.
    monkeypatch.setattr(api.settings, "protected_namespaces", "kube-system")
    monkeypatch.setattr(api, "protected_source", "env")
    api.load_protected()
    assert api.settings.protected == ["cert-manager", "k10y", "kube-system"] and api.protected_source == "hub"

    # An empty list is allowed and kept (nothing protected).
    assert client.put("/api/v1/settings/protected-namespaces", json={"namespaces": []}).json()["protected_namespaces"] == []
    api.load_protected()
    assert api.settings.protected == []


def test_helm_rollback_is_opt_in_guarded_and_runs_helm(monkeypatch):
    import subprocess

    calls = []

    class FakeController:
        protected = {"kube-system"}

        def guard(self, ns):
            if ns in self.protected:
                raise api.ProtectedNamespaceError(f"namespace {ns!r} is protected")

    monkeypatch.setattr(api.settings, "api_token", "")
    monkeypatch.setattr(api.settings, "token_secret", "")
    monkeypatch.setattr(api.settings, "control_enabled", True)
    monkeypatch.setattr(api, "get_controller", lambda: FakeController())
    monkeypatch.setattr(api, "audit_log", type("A", (), {"record": lambda self, e: None})())
    client = TestClient(api.app)
    url = "/api/v1/helm/releases/demo/web/rollback"

    monkeypatch.setattr(api.settings, "helm_enabled", False)
    r = client.post(url, json={"revision": 2})
    assert r.status_code == 400 and "K10Y_HELM_ENABLED" in r.json()["detail"]

    monkeypatch.setattr(api.settings, "helm_enabled", True)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/local/bin/helm")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "Rollback was a success!", ""))
    assert client.post("/api/v1/helm/releases/kube-system/x/rollback", json={"revision": 1}).status_code == 403
    assert client.post("/api/v1/helm/releases/demo/Bad;rm/rollback", json={"revision": 1}).status_code == 400
    r = client.post(url, json={"revision": 2, "dry_run": True})
    assert r.status_code == 200, r.text
    assert calls[-1][:6] == ["/usr/local/bin/helm", "rollback", "web", "2", "--namespace", "demo"] and "--dry-run" in calls[-1]
    assert client.post(url, json={"revision": 2}).json()["message"] == "Rolled back web in demo to revision 2"
