#!/bin/sh
set -eu

cat > /tmp/alertmanager.yml <<EOF
global:
  smtp_smarthost: '${ALERT_SMTP_SMARTHOST}'
  smtp_from: '${ALERT_EMAIL_FROM}'
  smtp_auth_username: '${ALERT_SMTP_USERNAME}'
  smtp_auth_password: '${ALERT_SMTP_PASSWORD}'
  smtp_require_tls: true

route:
  receiver: email-notifications
  group_by: ['alertname', 'service', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: email-notifications
    email_configs:
      - to: '${ALERT_EMAIL_TO}'
        send_resolved: true
        headers:
          subject: '[Bobo Alert] {{ .CommonLabels.severity | toUpper }} {{ .CommonLabels.alertname }}'

templates: []
EOF

exec /bin/alertmanager --config.file=/tmp/alertmanager.yml --storage.path=/alertmanager
