{{- define "k10y-hub.labels" -}}
app.kubernetes.io/name: k10y-hub
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* A generated value that stays the same across upgrades: reuse it from the existing Secret. */}}
{{- define "k10y-hub.keep" -}}
{{- $s := lookup "v1" "Secret" .ctx.Release.Namespace (printf "%s-secrets" .ctx.Release.Name) -}}
{{- if and $s (index $s.data .key) -}}{{ index $s.data .key }}{{- else -}}{{ .new | b64enc }}{{- end -}}
{{- end }}
