"""Generate the Helm chart deploy/helm/k10y-agent from deploy/k8s/*.yaml, so it can't drift from install.yaml.

    python scripts/gen_chart.py 2.8.0

Install:  helm install k10y-agent oci://ghcr.io/chhayserver/charts/k10y-agent -n k10y --create-namespace
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
K8S = ROOT / "deploy" / "k8s"
OUT = ROOT / "deploy" / "helm" / "k10y-agent"


def load(name):
    return [d for d in yaml.safe_load_all((K8S / name).read_text()) if d]


def rules(name, kind):
    return next(d for d in load(name) if d["kind"] == kind)["rules"]


def block(obj, indent):
    text = yaml.safe_dump(obj, sort_keys=False, default_flow_style=False).rstrip("\n")
    return "\n".join(" " * indent + line for line in text.splitlines())


VALUES = """# k10y agent. The hub connects to it at https://<any-node-ip>:<nodePort> with the token from
#   kubectl -n <namespace> get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d
image:
  repository: docker.io/k10y/k10y-agent
  tag: ""                      # defaults to the chart's appVersion
  pullPolicy: IfNotPresent

clusterName: ""                # how the agent names this cluster (the hub can use its own name)

# Control actions (scale, restart, roll back, edit, apply, shell, port-forward). false = read-only monitoring.
control: true
# Namespaces where every action, edit and shell is refused. Changing them in the hub overrides this.
protectedNamespaces: [kube-system, k10y]

# Roll back and upgrade Helm releases from the hub. Gives the agent Kubernetes' built-in "edit" role.
helm: false

service:
  type: NodePort
  nodePort: 30800              # empty for a random one; ignored unless type is NodePort

resources:
  requests: {cpu: 50m, memory: 128Mi}
  limits: {memory: 320Mi}

# Extra environment variables, e.g. [{name: K10Y_VOLUME_FULL_PERCENT, value: "85"}]
extraEnv: []

nodeSelector: {}
tolerations: []
affinity: {}
"""

HELPERS = """{{- define "k10y.labels" -}}
app.kubernetes.io/name: k10y-agent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}
"""


def rbac_template() -> str:
    subject = "  - kind: ServiceAccount\n    name: k10y-agent\n    namespace: {{ .Release.Namespace }}"
    return f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: k10y-agent
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: k10y-agent-read
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
rules:
{block(rules("rbac.yaml", "ClusterRole"), 2)}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: k10y-agent-read
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: k10y-agent-read}}
subjects:
{subject}
---
# The agent's own Secrets (its API token, AI settings) in this namespace only.
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: k10y-agent-self
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
rules:
{block(rules("rbac-self.yaml", "Role"), 2)}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: k10y-agent-self
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: k10y-agent-self}}
subjects:
{subject}
{{{{- if .Values.control }}}}
---
# Control actions. No RBAC, CRDs or other cluster-scoped kinds (except Namespace), so "apply" can't escalate.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: k10y-agent-control
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
rules:
{block(rules("rbac-control.yaml", "ClusterRole"), 2)}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: k10y-agent-control
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: k10y-agent-control}}
subjects:
{subject}
{{{{- end }}}}
{{{{- if .Values.helm }}}}
---
# Helm rollback and upgrade re-apply whatever a chart contains: Kubernetes' built-in "edit" role.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: k10y-agent-helm
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: edit}}
subjects:
{subject}
{{{{- end }}}}
"""


def agent_template() -> str:
    deployment = next(d for d in load("agent.yaml") if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    c = pod["containers"][0]
    probes = {k: c[k] for k in ("readinessProbe", "livenessProbe")}
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: k10y-agent
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
spec:
  replicas: 1
  selector:
    matchLabels: {{app: k10y-agent}}
  template:
    metadata:
      labels:
        app: k10y-agent
        {{{{- include "k10y.labels" . | nindent 8 }}}}
    spec:
      serviceAccountName: k10y-agent
      securityContext:
{block(pod["securityContext"], 8)}
      containers:
        - name: agent
          image: "{{{{ .Values.image.repository }}}}:{{{{ .Values.image.tag | default .Chart.AppVersion }}}}"
          imagePullPolicy: {{{{ .Values.image.pullPolicy }}}}
          ports:
            - {{name: http, containerPort: 8080}}
          env:
            - name: K10Y_CLUSTER_NAME
              value: {{{{ .Values.clusterName | default "kubernetes" | quote }}}}
            - name: K10Y_TOKEN_SECRET
              value: k10y-agent
            - name: K10Y_CONTROL_ENABLED
              value: {{{{ .Values.control | quote }}}}
            - name: K10Y_HELM_ENABLED
              value: {{{{ and .Values.control .Values.helm | quote }}}}
            - name: K10Y_PROTECTED_NAMESPACES
              value: {{{{ join "," .Values.protectedNamespaces | quote }}}}
            - name: K10Y_AUDIT_PATH
              value: /data/audit.jsonl
            - name: K10Y_TLS
              value: "true"
            {{{{- with .Values.extraEnv }}}}
            {{{{- toYaml . | nindent 12 }}}}
            {{{{- end }}}}
{block(probes, 10)}
          resources: {{{{- toYaml .Values.resources | nindent 12 }}}}
          securityContext:
{block(c["securityContext"], 12)}
          volumeMounts:
            - {{name: data, mountPath: /data}}
            - {{name: tmp, mountPath: /tmp}}
      volumes:
        - {{name: data, emptyDir: {{}}}}
        - {{name: tmp, emptyDir: {{}}}}
      {{{{- with .Values.nodeSelector }}}}
      nodeSelector: {{{{- toYaml . | nindent 8 }}}}
      {{{{- end }}}}
      {{{{- with .Values.tolerations }}}}
      tolerations: {{{{- toYaml . | nindent 8 }}}}
      {{{{- end }}}}
      {{{{- with .Values.affinity }}}}
      affinity: {{{{- toYaml . | nindent 8 }}}}
      {{{{- end }}}}
---
apiVersion: v1
kind: Service
metadata:
  name: k10y-agent
  labels: {{{{- include "k10y.labels" . | nindent 4 }}}}
spec:
  type: {{{{ .Values.service.type }}}}
  selector: {{app: k10y-agent}}
  ports:
    - name: http
      port: 8080
      targetPort: http
      {{{{- if and (eq .Values.service.type "NodePort") .Values.service.nodePort }}}}
      nodePort: {{{{ .Values.service.nodePort }}}}
      {{{{- end }}}}
"""


NOTES = """k10y agent {{ .Chart.AppVersion }} is starting in namespace {{ .Release.Namespace }}.

1. Its API token (created on first start):
   kubectl -n {{ .Release.Namespace }} get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d; echo
{{- if eq .Values.service.type "NodePort" }}
2. In the k10y hub: Administration -> Clusters -> Add cluster, address <any-node-ip>:{{ .Values.service.nodePort | default "<nodePort>" }}
{{- else }}
2. In the k10y hub: Administration -> Clusters -> Add cluster, with an address that reaches Service k10y-agent port 8080 (HTTPS).
{{- end }}
   and the token above. The agent serves HTTPS with a self-signed certificate.
{{- if not .Values.control }}

Read-only: control actions are off (control: false).
{{- end }}
"""


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "0.0.0"
    (OUT / "templates").mkdir(parents=True, exist_ok=True)
    (OUT / "Chart.yaml").write_text(yaml.safe_dump({
        "apiVersion": "v2", "name": "k10y-agent", "description": "k10y agent: lets a k10y hub monitor and operate this Kubernetes cluster",
        "type": "application", "version": version, "appVersion": version, "home": "https://k10y-agents.pages.dev",
        "sources": ["https://github.com/chhayserver/k10y-agent"], "keywords": ["kubernetes", "dashboard", "monitoring"],
        "kubeVersion": ">=1.24.0-0"}, sort_keys=False))
    files = {"values.yaml": VALUES, "templates/_helpers.tpl": HELPERS, "templates/rbac.yaml": rbac_template(),
             "templates/agent.yaml": agent_template(), "templates/NOTES.txt": NOTES,
             ".helmignore": "*.tgz\n.DS_Store\n"}
    header = "# Generated by scripts/gen_chart.py from deploy/k8s/*.yaml. Do not edit by hand.\n"
    for name, text in files.items():
        (OUT / name).write_text((header if name.endswith(".yaml") and name.startswith("templates/") else "") + text)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
