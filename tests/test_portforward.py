import types

import pytest

from k10y_agent import portforward as pf


def pod(name, ready=True, ports=None):
    return types.SimpleNamespace(
        metadata=types.SimpleNamespace(name=name, deletion_timestamp=None),
        status=types.SimpleNamespace(phase="Running", conditions=[types.SimpleNamespace(type="Ready", status="True" if ready else "False")]),
        spec=types.SimpleNamespace(containers=[types.SimpleNamespace(ports=[types.SimpleNamespace(name=n, container_port=p) for n, p in (ports or {}).items()])]))


class Core:
    def __init__(self, svc_ports, pods):
        self.svc = types.SimpleNamespace(spec=types.SimpleNamespace(selector={"app": "db"}, ports=svc_ports))
        self.pods = pods

    def read_namespaced_service(self, name, ns):
        return self.svc

    def list_namespaced_pod(self, ns, label_selector):
        assert label_selector == "app=db"
        return types.SimpleNamespace(items=self.pods)

    def read_namespaced_pod(self, name, ns):
        return next(p for p in self.pods if p.metadata.name == name)


def port(p, target, proto="TCP"):
    return types.SimpleNamespace(port=p, target_port=target, protocol=proto)


def test_service_resolves_to_a_ready_pod_and_target_port():
    core = Core([port(5432, "pg")], [pod("db-1", ready=False), pod("db-0", ports={"pg": 5433})])
    assert pf.resolve(core, "service", "demo", "db", 5432) == ("db-0", 5433)
    assert pf.resolve(Core([port(80, 8080)], [pod("web")]), "service", "demo", "db", 80) == ("web", 8080)
    with pytest.raises(pf.ForwardError, match="no port 81"):
        pf.resolve(core, "service", "demo", "db", 81)
    with pytest.raises(pf.ForwardError, match="only TCP"):
        pf.resolve(Core([port(53, 53, "UDP")], [pod("dns")]), "service", "demo", "db", 53)
    with pytest.raises(pf.ForwardError, match="no ready pod"):
        pf.resolve(Core([port(80, 80)], [pod("web", ready=False)]), "service", "demo", "db", 80)
    with pytest.raises(pf.ForwardError, match="not running"):
        pf.resolve(Core([], [pod("web", ready=False)]), "pod", "demo", "web", 80)


def test_portforward_permission():
    from k10y_agent.hub.permissions import BUILTIN_ROLES, CLUSTER_PERMISSIONS
    roles = {name: perms for name, _, perms in BUILTIN_ROLES}
    assert "pods.portforward" in CLUSTER_PERMISSIONS and "pods.portforward" in roles["Kubernetes admin"]
    assert "pods.portforward" not in roles["Kubernetes operator"]
