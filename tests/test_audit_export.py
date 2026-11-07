import json
import socket

from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub import audit_export

from test_hub import CSRF, ready_admin, client  # noqa: F401  (fixture)


def test_entries_go_to_files_and_syslog_and_download_works(client, tmp_path, monkeypatch):
    monkeypatch.setattr(hub.settings, "data_dir", str(tmp_path))
    hub.state.pop("audit_export", None)
    ready_admin(client)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(5)
    port = rx.getsockname()[1]
    r = client.put("/api/hub/settings/audit-export", json={"retention_days": 90, "file_enabled": True, "file_keep_days": 7,
                                                           "syslog_enabled": True, "syslog_host": "127.0.0.1", "syslog_port": port},
                   headers=CSRF)
    assert r.status_code == 200, r.text
    ex = hub.audit_exporter()
    ex.config(fresh=True)
    client.post("/api/hub/clusters", json={"name": "lab", "url": "https://x:1", "token": "t"}, headers=CSRF)   # audited
    batch = []
    while not ex.q.empty():
        batch.append(ex.q.get_nowait())
    assert any(e["action"] == "cluster-create" for e in batch)
    ex.send(ex.config(), batch)
    files = list((tmp_path / "audit").glob("audit-*.jsonl"))
    assert files and any(json.loads(l)["action"] == "cluster-create" for l in files[0].read_text().splitlines())
    line = rx.recv(9000).decode()
    assert line.startswith("<1") and " k10y " in line and '"source": "k10y"' in line
    assert client.post("/api/hub/settings/audit-export/test", headers=CSRF).status_code == 200

    csv = client.get("/api/hub/audit/export?format=csv&days=1")
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"] and "cluster-create" in csv.text
    assert json.loads(client.get("/api/hub/audit/export?format=json").text)[0]["action"]


def test_syslog_line_format():
    e = {"time": "2026-11-07T10:00:00+00:00", "action": "scale", "ok": False, "user": "a"}
    assert audit_export.syslog_line(e).startswith(b"<132>1 2026-11-07T10:00:00+00:00 k10y-hub k10y - scale - {")
