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
    monkeypatch.setattr(ai, "make_chat", lambda cfg, system, tools=None: FakeChat())
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


class FakeSecretCore:
    """In-memory Secret API for AIConfigStore tests."""

    def __init__(self, initial=None):
        import base64 as b64
        self.b64 = b64
        self.data = {k: b64.b64encode(v.encode()).decode() for k, v in (initial or {}).items()} if initial is not None else None

    def read_namespaced_secret(self, name, ns):
        from kubernetes.client.rest import ApiException
        if self.data is None:
            raise ApiException(status=404)
        return type("S", (), {"data": dict(self.data)})()

    def replace_namespaced_secret(self, name, ns, body):
        from kubernetes.client.rest import ApiException
        if self.data is None:
            raise ApiException(status=404)
        self.data = {k: self.b64.b64encode(v.encode()).decode() for k, v in body.string_data.items()}

    def create_namespaced_secret(self, ns, body):
        self.data = {k: self.b64.b64encode(v.encode()).decode() for k, v in body.string_data.items()}

    def delete_namespaced_secret(self, name, ns):
        self.data = None


def _store(core):
    return ai.AIConfigStore(lambda: core, lambda: "k10y", {})


def test_both_provider_keys_are_kept_and_switchable():
    core = FakeSecretCore()
    store = _store(core)
    store.save("gemini", "AIza-gemini-key-1111", "gemini-3.8-flash", "")
    store.save("openai", "cc_codecraft-key-2222", "gemini-3.7-flash", "", activate=True)
    pub = ai.AIConfigStore.public(store.load_all())
    assert pub["provider"] == "openai"
    assert pub["providers"]["gemini"]["key_hint"] == "…1111" and pub["providers"]["openai"]["key_hint"] == "…2222"
    store.set_active("gemini")
    assert store.load()["api_key"] == "AIza-gemini-key-1111"
    assert store.load("openai")["api_key"] == "cc_codecraft-key-2222"
    store.save("openai", None, "other-model", "", activate=False)   # change model, keep key, stay on gemini
    assert store.load("openai")["api_key"] == "cc_codecraft-key-2222"
    assert store.load_all()["active"] == "gemini"


def test_legacy_single_provider_secret_is_migrated():
    core = FakeSecretCore({"provider": "openai", "api_key": "cc_old-key-3333", "model": "m1", "base_url": "https://codecraftapi.com/v1"})
    store = _store(core)
    assert store.load()["provider"] == "openai" and store.load()["api_key"] == "cc_old-key-3333"
    store.save("gemini", "AIza-new-4444", "", "", activate=False)
    data = store.load_all()
    assert data["active"] == "openai"
    assert data["providers"]["openai"]["api_key"] == "cc_old-key-3333"
    assert data["providers"]["gemini"]["model"] == "gemini-3.8-flash"


def test_removing_active_key_falls_back_to_other_provider():
    store = _store(FakeSecretCore())
    store.save("gemini", "AIza-gemini-key-1111", "", "")
    store.save("openai", "cc_codecraft-key-2222", "x", "")
    store.clear("openai")
    assert store.load_all()["active"] == "gemini"
    store.clear("gemini")
    assert store._raw() == {}


def test_openrouter_uses_openai_chat_with_app_headers():
    chat = ai.make_chat({"provider": "openrouter", "api_key": "sk-or-test", "model": "vendor/model",
                         "base_url": ai.PROVIDERS["openrouter"]["base_url"]}, "system")
    assert isinstance(chat, ai.OpenAIChat)
    assert chat.url == "https://openrouter.ai/api/v1/chat/completions"
    assert chat.headers["Authorization"] == "Bearer sk-or-test" and chat.headers["X-Title"] == "k10y"
    other = ai.make_chat({"provider": "openai", "api_key": "k", "model": "m", "base_url": "https://x/v1"}, "s")
    assert "X-Title" not in other.headers


def test_openrouter_lists_only_models_with_tool_calling(monkeypatch):
    class R:
        status_code = 200
        def json(self):
            return {"data": [{"id": "b/tools", "supported_parameters": ["tools", "temperature"]},
                             {"id": "a/no-tools", "supported_parameters": ["temperature"]}, {"id": "c/unknown"}]}
    monkeypatch.setattr(ai.httpx, "get", lambda *a, **k: R())
    cfg = {"provider": "openrouter", "api_key": "k", "base_url": "https://openrouter.ai/api/v1"}
    assert ai.list_models(cfg) == ["b/tools"]
    assert ai.list_models({**cfg, "provider": "openai"}) == ["a/no-tools", "b/tools", "c/unknown"]
