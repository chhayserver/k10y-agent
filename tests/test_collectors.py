from datetime import datetime, timedelta, timezone

from kubernetes import client

from k10y_agent.collectors import node_info, pod_info, pod_problem, service_info


def _pod(phase="Running", waiting=None, restarts=0, age_s=60, ready=True):
    state = client.V1ContainerState(
        waiting=client.V1ContainerStateWaiting(reason=waiting) if waiting else None,
        running=None if waiting else client.V1ContainerStateRunning(),
    )
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name="p", namespace="ns", creation_timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_s)
        ),
        spec=client.V1PodSpec(containers=[client.V1Container(name="c")], node_name="n1"),
        status=client.V1PodStatus(
            phase=phase,
            container_statuses=[
                client.V1ContainerStatus(
                    name="c", image="img", image_id="", ready=ready, restart_count=restarts, state=state
                )
            ]
            if phase != "Pending"
            else None,
        ),
    )


def test_healthy_pod_has_no_problem():
    info = pod_info(_pod())
    assert info["status"] == "Running"
    assert info["ready_containers"] == 1
    assert pod_problem(info) is None


def test_crashloop_is_reported():
    info = pod_info(_pod(waiting="CrashLoopBackOff", restarts=3, ready=False))
    assert info["status"] == "CrashLoopBackOff"
    assert pod_problem(info) == "CrashLoopBackOff"


def test_many_restarts_is_reported():
    assert pod_problem(pod_info(_pod(restarts=7))) == "7 restarts"


def test_pending_only_reported_after_grace_period():
    assert pod_problem(pod_info(_pod(phase="Pending", age_s=30))) is None
    assert pod_problem(pod_info(_pod(phase="Pending", age_s=600))) == "Pending for more than 5m"


def test_node_info_ready_and_roles():
    node = client.V1Node(
        metadata=client.V1ObjectMeta(
            name="n1",
            labels={"node-role.kubernetes.io/control-plane": "true", "node-role.kubernetes.io/etcd": "true"},
            creation_timestamp=datetime.now(timezone.utc),
        ),
        spec=client.V1NodeSpec(),
        status=client.V1NodeStatus(
            conditions=[
                client.V1NodeCondition(type="Ready", status="True"),
                client.V1NodeCondition(type="MemoryPressure", status="True"),
                client.V1NodeCondition(type="EtcdIsVoter", status="True"),  # RKE2, healthy
            ],
            addresses=[client.V1NodeAddress(type="InternalIP", address="10.0.0.1")],
            allocatable={"cpu": "2", "memory": "4Gi"},
            node_info=client.V1NodeSystemInfo(
                architecture="arm64", boot_id="", container_runtime_version="", kernel_version="",
                kube_proxy_version="", kubelet_version="v1.36.5", machine_id="", operating_system="linux",
                os_image="Ubuntu", system_uuid="",
            ),
        ),
    )
    info = node_info(node)
    assert info["ready"] is True
    assert info["roles"] == ["control-plane", "etcd"]
    assert info["pressure"] == ["MemoryPressure"]
    assert info["cpu_allocatable_m"] == 2000
    assert info["memory_allocatable_bytes"] == 4 * 2**30


def test_service_info_nodeport():
    svc = client.V1Service(
        metadata=client.V1ObjectMeta(name="web", namespace="demo", creation_timestamp=datetime.now(timezone.utc)),
        spec=client.V1ServiceSpec(
            type="NodePort",
            cluster_ip="10.43.0.10",
            selector={"app": "web"},
            ports=[client.V1ServicePort(name="http", port=80, target_port=8080, node_port=30080, protocol="TCP")],
        ),
        status=client.V1ServiceStatus(),
    )
    info = service_info(svc)
    assert info["type"] == "NodePort"
    assert info["external_ips"] == []
    assert info["ports"] == [
        {"name": "http", "protocol": "TCP", "port": 80, "target_port": "8080", "node_port": 30080}
    ]


def test_job_status_and_duration():
    from k10y_agent.collectors import job_info

    start = datetime.now(timezone.utc) - timedelta(seconds=90)
    def job(conds, succeeded=0, failed=0, completion=None):
        return client.V1Job(
            metadata=client.V1ObjectMeta(name="j", namespace="ns", creation_timestamp=start),
            spec=client.V1JobSpec(template=client.V1PodTemplateSpec(spec=client.V1PodSpec(containers=[client.V1Container(name="c", image="img")]))),
            status=client.V1JobStatus(start_time=start, completion_time=completion, succeeded=succeeded, failed=failed,
                                      conditions=[client.V1JobCondition(type=t, status="True", reason=r) for t, r in conds]))
    done = job_info(job([("Complete", None)], succeeded=1, completion=start + timedelta(seconds=30)))
    assert done["status"] == "Complete" and done["duration_s"] == 30
    failed = job_info(job([("Failed", "BackoffLimitExceeded")], failed=6))
    assert failed["status"] == "Failed" and failed["failure_reason"] == "BackoffLimitExceeded"
    assert job_info(job([]))["status"] == "Running"
