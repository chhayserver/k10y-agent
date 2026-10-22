from datetime import datetime, timezone

from kubernetes import client

from k10y_agent.topology import build_topology, config_refs

NOW = datetime.now(timezone.utc)


def meta(name, uid=None, labels=None, owner=None, annotations=None):
    return client.V1ObjectMeta(
        name=name, namespace="demo", uid=uid or f"uid-{name}", labels=labels, annotations=annotations,
        creation_timestamp=NOW,
        owner_references=[client.V1OwnerReference(api_version="v1", kind=owner[0], name=owner[1], uid=owner[2], controller=True)] if owner else None,
    )


def pod_spec():
    return client.V1PodSpec(
        containers=[client.V1Container(
            name="app", image="img",
            env=[client.V1EnvVar(name="PW", value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name="db-pass", key="pw")))],
            env_from=[client.V1EnvFromSource(config_map_ref=client.V1ConfigMapEnvSource(name="app-cfg"))],
        )],
        volumes=[
            client.V1Volume(name="data", persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name="data")),
            client.V1Volume(name="missing", config_map=client.V1ConfigMapVolumeSource(name="nope")),
            client.V1Volume(name="kube-api-access", projected=client.V1ProjectedVolumeSource(sources=[
                client.V1VolumeProjection(config_map=client.V1ConfigMapProjection(name="kube-root-ca.crt"))])),
        ],
    )


def graph():
    template = client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(labels={"app": "web"}), spec=pod_spec())
    dep = client.V1Deployment(metadata=meta("web"), spec=client.V1DeploymentSpec(
        replicas=1, selector=client.V1LabelSelector(match_labels={"app": "web"}), template=template),
        status=client.V1DeploymentStatus(ready_replicas=1))
    rs = client.V1ReplicaSet(metadata=meta("web-1", owner=("Deployment", "web", "uid-web"),
                                           annotations={"deployment.kubernetes.io/revision": "1"}),
                             spec=client.V1ReplicaSetSpec(selector=client.V1LabelSelector(), template=template),
                             status=client.V1ReplicaSetStatus(replicas=1, ready_replicas=1))
    pod = client.V1Pod(metadata=meta("web-1-abc", labels={"app": "web"}, owner=("ReplicaSet", "web-1", "uid-web-1")),
                       spec=pod_spec(),
                       status=client.V1PodStatus(phase="Running", container_statuses=[client.V1ContainerStatus(
                           name="app", image="img", image_id="", ready=True, restart_count=0,
                           state=client.V1ContainerState(running=client.V1ContainerStateRunning()))]))
    svc = client.V1Service(metadata=meta("web"), spec=client.V1ServiceSpec(
        type="NodePort", selector={"app": "web"}, ports=[client.V1ServicePort(port=80, node_port=30080)]))
    orphan_svc = client.V1Service(metadata=meta("ghost"), spec=client.V1ServiceSpec(type="ClusterIP", selector={"app": "ghost"}))
    cm = client.V1ConfigMap(metadata=meta("app-cfg"))
    pvc = client.V1PersistentVolumeClaim(metadata=meta("data"), spec=client.V1PersistentVolumeClaimSpec(),
                                         status=client.V1PersistentVolumeClaimStatus(phase="Bound", capacity={"storage": "1Gi"}))
    return build_topology(pods=[pod], deployments=[dep], replicasets=[rs], statefulsets=[], daemonsets=[],
                          services=[svc, orphan_svc], configmaps=[cm], pvcs=[pvc], ingresses=[])


def edges_of(g, etype):
    return {(e["from"], e["to"]) for e in g["edges"] if e["type"] == etype}


def test_config_refs_ignores_root_ca():
    refs = {(k, n) for k, n, _, _ in config_refs(pod_spec())}
    assert refs == {("Secret", "db-pass"), ("ConfigMap", "app-cfg"), ("PersistentVolumeClaim", "data"), ("ConfigMap", "nope")}


def test_ownership_chain():
    g = graph()
    assert ("Deployment/demo/web", "ReplicaSet/demo/web-1") in edges_of(g, "rs-owns")
    assert ("ReplicaSet/demo/web-1", "Pod/demo/web-1-abc") in edges_of(g, "rs-pod")
    assert ("Deployment/demo/web", "Pod/demo/web-1-abc") in edges_of(g, "owns")


def test_service_selects_pods_and_workload():
    g = graph()
    assert ("Service/demo/web", "Pod/demo/web-1-abc") in edges_of(g, "selects")
    assert ("Service/demo/web", "Deployment/demo/web") in edges_of(g, "selects-workload")
    ghost = next(n for n in g["nodes"] if n["id"] == "Service/demo/ghost")
    assert ghost["problem"] == "selector matches no pods"


def test_config_edges_and_missing_reference():
    g = graph()
    uses = edges_of(g, "uses")
    for target in ("Secret/demo/db-pass", "ConfigMap/demo/app-cfg", "PersistentVolumeClaim/demo/data", "ConfigMap/demo/nope"):
        assert ("Pod/demo/web-1-abc", target) in uses
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["ConfigMap/demo/nope"]["missing"] is True
    assert nodes["ConfigMap/demo/nope"]["status"] == "critical"
    assert nodes["ConfigMap/demo/app-cfg"]["status"] == "neutral"
    assert "ConfigMap/demo/kube-root-ca.crt" not in nodes


def test_missing_secret_detected_when_secrets_listed():
    g = graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    assert "missing" not in nodes["Secret/demo/db-pass"]  # unknown when secrets are not listed
