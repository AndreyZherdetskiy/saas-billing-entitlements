{{- define "billing-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "billing-platform.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "billing-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "billing-platform.labels" -}}
helm.sh/chart: {{ include "billing-platform.chart" . }}
{{ include "billing-platform.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "billing-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "billing-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "billing-platform.componentLabels" -}}
{{ include "billing-platform.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "billing-platform.configMapName" -}}
{{- printf "%s-config" (include "billing-platform.fullname" .) }}
{{- end }}

{{- define "billing-platform.secretName" -}}
{{- printf "%s-secrets" (include "billing-platform.fullname" .) }}
{{- end }}

{{- define "billing-platform.image" -}}
{{- $root := index . 0 -}}
{{- $tag := index . 1 -}}
{{- printf "%s:%s" $root.Values.image.repository $tag }}
{{- end }}

{{- define "billing-platform.apiLivenessProbe" -}}
livenessProbe:
  httpGet:
    path: {{ .Values.probes.api.liveness.path }}
    port: {{ .Values.api.containerPort }}
  initialDelaySeconds: {{ .Values.probes.api.liveness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.api.liveness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.api.liveness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.api.liveness.failureThreshold }}
{{- end }}

{{- define "billing-platform.apiReadinessProbe" -}}
readinessProbe:
  httpGet:
    path: {{ .Values.probes.api.readiness.path }}
    port: {{ .Values.api.containerPort }}
  initialDelaySeconds: {{ .Values.probes.api.readiness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.api.readiness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.api.readiness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.api.readiness.failureThreshold }}
{{- end }}

{{- define "billing-platform.workerLivenessProbe" -}}
livenessProbe:
  exec:
    command:
      - /bin/sh
      - -c
      - celery -A billing_platform.workers.celery_app inspect ping -d celery@$(hostname)
  initialDelaySeconds: {{ .Values.probes.worker.liveness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.worker.liveness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.worker.liveness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.worker.liveness.failureThreshold }}
{{- end }}

{{- define "billing-platform.workerReadinessProbe" -}}
readinessProbe:
  exec:
    command:
      - /bin/sh
      - -c
      - celery -A billing_platform.workers.celery_app inspect ping -d celery@$(hostname)
  initialDelaySeconds: {{ .Values.probes.worker.readiness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.worker.readiness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.worker.readiness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.worker.readiness.failureThreshold }}
{{- end }}

{{- define "billing-platform.relayLivenessProbe" -}}
livenessProbe:
  exec:
    command:
      - python
      - -c
      - "import os, signal; os.kill(1, 0)"
  initialDelaySeconds: {{ .Values.probes.relay.liveness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.relay.liveness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.relay.liveness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.relay.liveness.failureThreshold }}
{{- end }}

{{- define "billing-platform.relayReadinessProbe" -}}
readinessProbe:
  exec:
    command:
      - python
      - -c
      - "import os, signal; os.kill(1, 0)"
  initialDelaySeconds: {{ .Values.probes.relay.readiness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.relay.readiness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.relay.readiness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.relay.readiness.failureThreshold }}
{{- end }}

{{- define "billing-platform.podSecurityContext" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .Values.securityContext.runAsUser }}
  runAsGroup: {{ .Values.securityContext.runAsGroup }}
  fsGroup: {{ .Values.securityContext.fsGroup }}
  seccompProfile:
    type: RuntimeDefault
{{- end }}

{{- define "billing-platform.containerSecurityContext" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: false
  capabilities:
    drop:
      - ALL
{{- end }}
