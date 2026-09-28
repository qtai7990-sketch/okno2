#!/usr/bin/env bash
# Оповещение ответственному в Telegram (бот единого окна). Настройки — в /etc/simpad/ops.env:
#   SIMPAD_ALERT_TG_TOKEN=<токен бота>  SIMPAD_ALERT_TG_CHAT=<id чата>
# Без настроек сообщение пишется только в системный журнал (и это само по себе считается ошибкой настройки).
set -uo pipefail
msg="Единое окно $(hostname): $*"
logger -t simpad-alert -- "$msg"
if [ -n "${SIMPAD_ALERT_TG_TOKEN:-}" ] && [ -n "${SIMPAD_ALERT_TG_CHAT:-}" ]; then
  curl -fsS -m 20 "https://api.telegram.org/bot${SIMPAD_ALERT_TG_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${SIMPAD_ALERT_TG_CHAT}" --data-urlencode "text=${msg}" >/dev/null
else
  echo "ВНИМАНИЕ: оповещения не настроены (SIMPAD_ALERT_TG_TOKEN / SIMPAD_ALERT_TG_CHAT)" >&2
  exit 1
fi
