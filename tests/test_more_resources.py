from datetime import datetime, timezone

from kubernetes import client as k

from k10y_agent.collectors import cronjob_info, hpa_info, ingress_info, pv_info, pvc_info
from k10y_agent.hub.permissions import required_permission

NOW = datetime.now(timezone.utc)
META = lambda name, ns="demo": k.V1ObjectMeta(name=name, namespace=ns, creation_timestamp=NOW)
POD_TMPL = k.V1PodTemplateSpec(spec=k.V1PodSpec(containers=[k.V1Container(name="c", image="busybox:1.36")]))


def test_cronjob_info():
    cj = k.V1CronJob(metadata=META("backup"), spec=k.V1CronJobSpec(
        schedule="0 3 * * *", suspend=True, job_template=k.V1JobTemplateSpec(spec=k.V1JobSpec(template=POD_TMPL))),
        status=k.V1CronJobStatus(active=[k.V1ObjectReference(name="backup-1")], last_schedule_time=NOW))
    d = cronjob_info(cj)
    assert d["schedule"] == "0 3 * * *" and d["suspended"] and d["active"] == 1 and d["images"] == ["busybox:1.36"]


def test_ingress_info():
    backend = k.V1IngressBackend(service=k.V1IngressServiceBackend(name="web", port=k.V1ServiceBackendPort(number=80)))
    ing = k.V1Ingress(metadata=META("site"), spec=k.V1IngressSpec(
        ingress_class_name="nginx", tls=[k.V1IngressTLS(hosts=["a.example"])],
        rules=[k.V1IngressRule(host="a.example", http=k.V1HTTPIngressRuleValue(paths=[
            k.V1HTTPIngressPath(path="/", path_type="Prefix", backend=backend)]))]))
    d = ingress_info(ing)
    assert d["hosts"] == ["a.example"] and d["rules"][0] == {"host": "a.example", "path": "/", "service": "web", "port": 80}
    assert d["tls_hosts"] == ["a.example"] and d["class"] == "nginx"


def test_pv_and_pvc_info():
    pv = k.V1PersistentVolume(metadata=META("pv-1", None), spec=k.V1PersistentVolumeSpec(
        capacity={"storage": "10Gi"}, access_modes=["ReadWriteOnce"], persistent_volume_reclaim_policy="Delete",
        claim_ref=k.V1ObjectReference(namespace="demo", name="data"), storage_class_name="local-path"),
        status=k.V1PersistentVolumeStatus(phase="Bound"))
    d = pv_info(pv)
    assert d["claim"] == "demo/data" and d["capacity_bytes"] == 10 * 2 ** 30 and d["status"] == "Bound"
    pvc = k.V1PersistentVolumeClaim(metadata=META("data"), spec=k.V1PersistentVolumeClaimSpec(
        access_modes=["ReadWriteOnce"], volume_name="pv-1", resources=k.V1VolumeResourceRequirements(requests={"storage": "5Gi"})),
        status=k.V1PersistentVolumeClaimStatus(phase="Pending"))
    d = pvc_info(pvc)
    assert d["capacity"] == "5Gi" and d["status"] == "Pending" and d["volume"] == "pv-1"


def test_hpa_info():
    h = k.V2HorizontalPodAutoscaler(metadata=META("web"), spec=k.V2HorizontalPodAutoscalerSpec(
        scale_target_ref=k.V2CrossVersionObjectReference(kind="Deployment", name="web"), min_replicas=2, max_replicas=5,
        metrics=[k.V2MetricSpec(type="Resource", resource=k.V2ResourceMetricSource(
            name="cpu", target=k.V2MetricTarget(type="Utilization", average_utilization=70)))]),
        status=k.V2HorizontalPodAutoscalerStatus(current_replicas=3, desired_replicas=3, current_metrics=[
            k.V2MetricStatus(type="Resource", resource=k.V2ResourceMetricStatus(
                name="cpu", current=k.V2MetricValueStatus(average_utilization=55)))]))
    d = hpa_info(h)
    assert d["target"] == "Deployment/web" and (d["min"], d["max"], d["current"]) == (2, 5, 3)
    assert d["metrics"] == [{"type": "Resource", "name": "cpu", "target": "70%", "current": "55%"}]


def test_new_permissions():
    assert required_permission("GET", "cronjobs") == "cluster.view"
    assert required_permission("GET", "pods/ns/p/logs/stream") == "logs.view"
    assert required_permission("GET", "deployments/ns/d/logs/stream") == "logs.view"
    assert required_permission("POST", "cronjobs/ns/c/trigger") == "workloads.restart"
    assert required_permission("POST", "cronjobs/ns/c/delete") is None


def test_log_stream_merges_pods_with_prefix():
    from k10y_agent import logstream

    class Resp:
        def __init__(self, chunks):
            self.chunks = chunks

        def stream(self, n):
            yield from self.chunks

        def release_conn(self):
            pass

        def close(self):
            pass

    class Core:
        def read_namespaced_pod_log(self, pod, ns, **kw):
            assert kw["follow"] is True and kw["_preload_content"] is False
            return Resp([b"hello ", b"world\nsecond", b" line\n"] if pod == "a" else [b"from b\n"])

    out = "".join(logstream.stream(Core(), [("a", None), ("b", "app")], "demo", prefix=True))
    lines = sorted(out.strip().split("\n"))
    assert lines == ["[a] hello world", "[a] second line", "[b/app] from b"]
