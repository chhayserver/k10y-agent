from kubernetes import client

from k10y_agent import ai


def test_gemini_schema_uses_uppercase_types():
    s = ai._gemini_schema({"type": "object", "properties": {"n": {"type": "integer"}, "xs": {"type": "array", "items": {"type": "string"}}}})
    assert s["type"] == "OBJECT"
    assert s["properties"]["n"]["type"] == "INTEGER"
    assert s["properties"]["xs"]["items"]["type"] == "STRING"


def test_normalise_blocks_protected_and_incomplete_actions():
    out = ai._normalise({"summary": "s", "issues": [{"title": "t", "severity": "warning", "root_cause": "r"}], "actions": [
        {"type": "restart", "namespace": "shop", "name": "api", "reason": "r", "risk": "low"},
        {"type": "restart", "namespace": "kube-system", "name": "coredns", "reason": "r", "risk": "low"},
        {"type": "scale", "namespace": "shop", "name": "api", "reason": "r", "risk": "low"},
        {"type": "apply_yaml", "yaml": "metadata:\n  namespace: kube-system\n", "reason": "r", "risk": "low"},
        {"type": "drop_database", "reason": "r", "risk": "high"},
    ]}, ["kube-system", "k10y"])
    blocked = [a["blocked"] for a in out["actions"]]
    assert blocked[0] is None
    assert "protected" in blocked[1]
    assert blocked[2] == "missing replicas"
    assert "protected" in blocked[3]
    assert len(out["actions"]) == 4  # unknown action type dropped


def test_container_view_never_exposes_secret_values():
    c = client.V1Container(name="app", image="img", env=[
        client.V1EnvVar(name="PLAIN", value="hello"),
        client.V1EnvVar(name="PW", value_from=client.V1EnvVarSource(secret_key_ref=client.V1SecretKeySelector(name="db", key="pw"))),
    ])
    view = ai.ClusterTools._container(c)
    assert {"name": "PLAIN", "value": "hello"} in view["env"]
    assert {"name": "PW", "from_secret": "db/pw"} in view["env"]


class FakeChat:
    """Scripted model: inspects logs, then submits a diagnosis."""

    def __init__(self, *_):
        self.turn = 0
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.sent = []

    def user(self, text):
        self.sent.append(text)

    def step(self):
        self.turn += 1
        if self.turn == 1:
            return [{"id": "1", "name": "get_pod_logs", "args": {"namespace": "shop", "name": "worker-1", "previous": True}}], ""
        return [{"id": "2", "name": "submit_diagnosis", "args": {
            "summary": "worker needs ConfigMap worker-config",
            "issues": [{"title": "Missing ConfigMap", "severity": "critical", "root_cause": "worker-config not found"}],
            "actions": [{"type": "apply_yaml", "yaml": "kind: ConfigMap\nmetadata:\n  name: worker-config\n  namespace: shop\n",
                         "reason": "create it", "risk": "low"}]}}], ""

    def results(self, results):
        self.last_results = results


class FakeTools:
    def __init__(self):
        self.calls = []

    def run(self, name, args):
        self.calls.append((name, args))
        return "Error: configmap worker-config not found"


def test_diagnose_loop_runs_tools_then_returns_structured_result(monkeypatch):
    monkeypatch.setattr(ai, "make_chat", lambda cfg, system: FakeChat())
    tools = FakeTools()
    out = ai.diagnose({"provider": "gemini", "model": "m", "api_key": "k", "base_url": "u"}, tools, "check shop", ["kube-system"])
    assert tools.calls == [("get_pod_logs", {"namespace": "shop", "name": "worker-1", "previous": True})]
    assert out["summary"].startswith("worker needs")
    assert out["actions"][0]["type"] == "apply_yaml" and out["actions"][0]["blocked"] is None
    assert out["steps"][0]["tool"] == "get_pod_logs"


def test_missing_key_is_a_clear_error():
    try:
        ai.make_chat({"provider": "openai", "api_key": "", "model": "x", "base_url": "u"}, "s")
    except ai.ProviderError as e:
        assert "API key" in str(e)
    else:
        raise AssertionError("expected ProviderError")
