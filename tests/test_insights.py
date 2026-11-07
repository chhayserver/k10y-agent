"""Agent insights: Helm release records, masking, TLS certificate expiry, CronJob health, workload owners,
and the new problems (OOMKilled, stuck rollouts)."""
import pytest
import base64
import gzip
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import yaml
from kubernetes import client

from k10y_agent import insights
from k10y_agent.collectors import deployment_info, pod_info, pod_problem

NOW = datetime.now(timezone.utc)


def helm_secret(name, ns, version, status="deployed", config=None, manifest=""):
    rel = {"name": name, "namespace": ns, "version": version, "config": config or {}, "manifest": manifest,
           "info": {"status": status, "last_deployed": "2026-11-01T10:00:00Z", "description": f"Upgrade {version}"},
           "chart": {"metadata": {"name": "nginx", "version": f"1.{version}.0", "appVersion": "1.27"}}}
    data = base64.b64encode(base64.b64encode(gzip.compress(json.dumps(rel).encode()))).decode()
    return SimpleNamespace(data={"release": data}, metadata=SimpleNamespace(
        namespace=ns, labels={"owner": "helm", "name": name, "version": str(version), "status": status}, creation_timestamp=NOW))


class FakeCore:
    def __init__(self, secrets):
        self.secrets = secrets

    def list_secret_for_all_namespaces(self, label_selector=""):
        return SimpleNamespace(items=self.secrets)

    def list_namespaced_secret(self, namespace, label_selector="", field_selector=""):
        want = dict(x.split("=") for x in label_selector.split(",") if x)
        return SimpleNamespace(items=[s for s in self.secrets if s.metadata.namespace == namespace
                                      and all(s.metadata.labels.get(k) == v for k, v in want.items())])


def test_helm_releases_history_and_masked_values():
    manifest = "---\napiVersion: v1\nkind: Secret\nmetadata: {name: db}\ndata: {password: c2VjcmV0}\n---\napiVersion: v1\nkind: Service\nmetadata: {name: web}\n"
    c = SimpleNamespace(core=FakeCore([
        helm_secret("web", "shop", 1, "superseded"),
        helm_secret("web", "shop", 2, config={"replicas": 3, "db": {"password": "hunter2", "host": "db"}, "apiKey": "abc"}, manifest=manifest),
        helm_secret("redis", "cache", 1)]))
    rel = insights.helm_releases(c)
    assert [(r["namespace"], r["name"], r["revision"], r["revisions"], r["chart_version"]) for r in rel] == [
        ("cache", "redis", 1, 1, "1.1.0"), ("shop", "web", 2, 2, "1.2.0")]
    hist = insights.helm_history(c, "shop", "web")
    assert [h["revision"] for h in hist] == [2, 1] and hist[1]["status"] == "superseded"
    r2 = insights.helm_revision(c, "shop", "web", 2)
    values = yaml.safe_load(r2["values"])
    assert values == {"replicas": 3, "db": {"password": insights.MASK, "host": "db"}, "apiKey": insights.MASK}
    assert "c2VjcmV0" not in r2["manifest"] and insights.MASK in r2["manifest"] and "kind: Service" in r2["manifest"]


def test_mask_leaves_harmless_values():
    assert insights.mask({"keyspace": "x", "monkey": "y", "tls": {"key": "k", "enabled": True}, "port": 80, "token_ttl": 30}) == {
        "keyspace": "x", "monkey": "y", "tls": {"key": insights.MASK, "enabled": True}, "port": 80, "token_ttl": 30}


def test_certificate_view_and_problems():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "shop.example.com")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(NOW - timedelta(days=80)).not_valid_after(NOW + timedelta(days=5, hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("shop.example.com")]), critical=False).sign(key, hashes.SHA256()))
    view = insights._cert_view(cert.public_bytes(serialization.Encoding.PEM))
    assert view["days_left"] == 5 and view["dns_names"] == ["shop.example.com"]
    insights._cert_cache.update({"t": 9e18, "items": [{"namespace": "shop", "name": "tls-shop", "hosts": ["shop.example.com"], **view},
                                                        {"namespace": "x", "name": "ok", "hosts": [], "days_left": 60, "ready": True}]})
    try:
        assert insights.certificate_problems(None) == [
            {"kind": "Certificate", "namespace": "shop", "name": "tls-shop", "reason": "TLS certificate expires in 5 days (shop.example.com)"}]
    finally:
        insights._cert_cache.update({"t": 0.0, "items": []})


def test_cronjob_health():
    cj = {"namespace": "ops", "name": "backup", "suspended": False, "active": 0, "last_schedule_s": 3600, "last_success_s": 90000, "age_s": 10**6}
    jobs = [{"namespace": "ops", "name": "backup-2", "owner": "CronJob/backup", "status": "Failed", "failure_reason": "BackoffLimitExceeded", "age_s": 3600},
            {"namespace": "ops", "name": "backup-1", "owner": "CronJob/backup", "status": "Complete", "age_s": 90000},
            {"namespace": "ops", "name": "other", "owner": "CronJob/other", "status": "Failed", "age_s": 10}]
    runs = insights.cronjob_runs(jobs, "ops", "backup")
    assert [r["name"] for r in runs] == ["backup-2", "backup-1"]
    assert insights.cronjob_problem(cj, runs) == "last run failed: BackoffLimitExceeded"
    assert insights.cronjob_problem(cj, runs[1:]) == "the latest scheduled run didn't succeed"
    assert insights.cronjob_problem({**cj, "last_success_s": 3500}, runs[1:]) is None
    assert insights.cronjob_problem({**cj, "suspended": True}, runs) is None


def pod(owner_kind="ReplicaSet", owner_name="web-7d9f8b6c5", hash_="7d9f8b6c5", last_reason=None):
    meta = client.V1ObjectMeta(name="p", namespace="ns", labels={"pod-template-hash": hash_} if hash_ else {},
                               creation_timestamp=NOW, owner_references=[client.V1OwnerReference(
                                   api_version="apps/v1", kind=owner_kind, name=owner_name, uid="u", controller=True)])
    last = client.V1ContainerState(terminated=client.V1ContainerStateTerminated(exit_code=137, reason=last_reason)) if last_reason else None
    return client.V1Pod(metadata=meta, spec=client.V1PodSpec(containers=[client.V1Container(name="c")]),
                        status=client.V1PodStatus(phase="Running", container_statuses=[client.V1ContainerStatus(
                            name="c", image="i", image_id="", ready=True, restart_count=1, last_state=last,
                            state=client.V1ContainerState(running=client.V1ContainerStateRunning()))]))


def test_workload_owner_and_oomkilled():
    assert pod_info(pod())["owner"] == "Deployment/web"
    assert pod_info(pod("StatefulSet", "db", None))["owner"] == "StatefulSet/db"
    assert pod_problem(pod_info(pod(last_reason="OOMKilled"))) == "OOMKilled: out of memory (memory limit too low?)"
    assert pod_problem(pod_info(pod(last_reason="Error"))) is None             # one ordinary restart is fine


def test_stuck_rollout():
    dep = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="web", namespace="ns", creation_timestamp=NOW),
        spec=client.V1DeploymentSpec(replicas=2, selector=client.V1LabelSelector(), template=client.V1PodTemplateSpec(
            spec=client.V1PodSpec(containers=[client.V1Container(name="c", image="web:2")]))),
        status=client.V1DeploymentStatus(ready_replicas=1, conditions=[client.V1DeploymentCondition(
            type="Progressing", status="False", reason="ProgressDeadlineExceeded")]))
    assert deployment_info(dep)["stuck"] == "rollout stuck: ProgressDeadlineExceeded"


def test_volume_usage_from_kubelet_summary_and_full_problem(monkeypatch):
    from k10y_agent import insights
    summary = {"node": {"nodeName": "n1"}, "pods": [
        {"podRef": {"name": "db-0", "namespace": "demo"}, "volume": [
            {"name": "data", "pvcRef": {"name": "data-db-0", "namespace": "demo"}, "capacityBytes": 10 * 2 ** 30,
             "usedBytes": int(9.5 * 2 ** 30), "availableBytes": int(0.5 * 2 ** 30), "inodes": 1000, "inodesUsed": 10},
            {"name": "tmp", "capacityBytes": 100, "usedBytes": 99}]},           # not a PVC: ignored
        {"podRef": {"name": "web-0", "namespace": "demo"}, "volume": [
            {"name": "files", "pvcRef": {"name": "files", "namespace": "demo"}, "capacityBytes": 1000, "usedBytes": 100,
             "inodes": 100, "inodesUsed": 95}]}]}
    vols = insights.parse_stats_summary(summary)
    assert set(vols) == {"demo/data-db-0", "demo/files"}
    assert vols["demo/data-db-0"]["used_percent"] == 95.0 and vols["demo/data-db-0"]["node"] == "n1"
    monkeypatch.setattr(insights, "volume_usage", lambda c, fresh=False: {"volumes": vols, "errors": [], "available": True})
    problems = insights.volume_problems(None)
    assert [(p["name"], p["reason"]) for p in problems] == [("data-db-0", "volume 95% full (9.5Gi of 10.0Gi)"),
                                                            ("files", "volume out of inodes (95% used)")]
    monkeypatch.setenv("K10Y_VOLUME_FULL_PERCENT", "96")
    assert insights.volume_problems(None) == []


def test_volume_usage_outside_cluster_is_reported_not_raised(monkeypatch):
    from k10y_agent import insights
    monkeypatch.setattr(insights, "SA_DIR", "/nonexistent")
    insights._vol_cache.update(t=0.0, data=None)
    data = insights.volume_usage(object(), fresh=True)
    assert data["available"] is False and "inside the cluster" in data["errors"][0]


def test_helm_unmask_and_chart_rebuild(tmp_path):
    import base64, os
    from k10y_agent import insights
    M = insights.MASK
    old = {"db": {"password": "s3cret", "host": "pg"}, "tokens": ["a", "b"]}
    assert insights.unmask({"db": {"password": M, "host": "pg2"}, "tokens": [M, "c"]}, old) == \
        {"db": {"password": "s3cret", "host": "pg2"}, "tokens": ["a", "c"]}
    with pytest.raises(ValueError):
        insights.unmask({"newkey": M}, old)                          # nothing to put back

    b64 = lambda t: base64.b64encode(t.encode()).decode()
    rel = {"chart": {"metadata": {"apiVersion": "v2", "name": "web", "version": "1.2.3", "appVersion": None},
                     "values": {"replicas": 1}, "schema": b64('{"type": "object"}'),
                     "templates": [{"name": "templates/deploy.yaml", "data": b64("kind: Deployment")}],
                     "files": [{"name": "crds/x.yaml", "data": b64("kind: CRD")}]}}
    base = insights.write_chart(rel, str(tmp_path))
    assert open(os.path.join(base, "templates/deploy.yaml")).read() == "kind: Deployment"
    assert "appVersion" not in open(os.path.join(base, "Chart.yaml")).read()
    assert os.path.exists(os.path.join(base, "values.schema.json")) and os.path.exists(os.path.join(base, "crds/x.yaml"))
    with pytest.raises(ValueError):
        insights.write_chart({"chart": {**rel["chart"], "templates": [{"name": "../../etc/x", "data": ""}]}}, str(tmp_path / "e"))
    with pytest.raises(ValueError, match="subcharts"):
        insights.write_chart({"chart": {**rel["chart"], "metadata": {**rel["chart"]["metadata"], "dependencies": [{"name": "redis"}]}}}, str(tmp_path / "d"))
