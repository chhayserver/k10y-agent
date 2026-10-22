from datetime import datetime, timezone
from kubernetes import client

from k10y_agent.resources import ResourceViewer


def _viewer():
    v = ResourceViewer.__new__(ResourceViewer)
    v.san = client.ApiClient().sanitize_for_serialization
    return v


def test_secret_values_are_never_returned():
    sec = client.V1Secret(api_version="v1", kind="Secret",
                          metadata=client.V1ObjectMeta(name="db", namespace="shop", annotations={
                              "kubectl.kubernetes.io/last-applied-configuration": '{"data":{"pw":"czNjcmV0"}}'}),
                          data={"pw": "czNjcmV0"}, string_data={"x": "plain"}, type="Opaque")
    plain = _viewer()._plain("Secret", sec)
    assert plain["data"] == {"pw": "<hidden: 6 bytes>"}
    assert "stringData" not in plain
    assert "czNjcmV0" not in str(plain)


def test_managed_fields_removed_and_kind_filled():
    cm = client.V1ConfigMap(metadata=client.V1ObjectMeta(name="cfg", namespace="demo", creation_timestamp=datetime.now(timezone.utc),
                                                         managed_fields=[client.V1ManagedFieldsEntry(manager="kubectl")]), data={"a": "1"})
    plain = _viewer()._plain("ConfigMap", cm)
    assert plain["kind"] == "ConfigMap" and plain["apiVersion"] == "v1"
    assert plain["data"] == {"a": "1"}
    assert "managedFields" not in plain["metadata"]
