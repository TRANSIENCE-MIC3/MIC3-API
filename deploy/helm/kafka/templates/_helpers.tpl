{{- define "kafka.address" -}}
{{- printf "%s.%s.svc.%s" .Values.name .Release.Namespace .Values.clusterDomain -}}
{{- end -}}
{{- define "kafka.controller" -}}
{{- printf "%s-0.%s-headless.%s.svc.%s" .Values.name .Values.name .Release.Namespace .Values.clusterDomain -}}
{{- end -}}
{{- define "kafka.securityContext" -}}
{{- with .Values.podIdentity }}
{{ toYaml . }}
{{- end }}
runAsNonRoot: true
seccompProfile: {type: RuntimeDefault}
{{- end -}}
