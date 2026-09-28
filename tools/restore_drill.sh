#!/usr/bin/env bash
# Учебное восстановление — НЕ на рабочем сервере: на отдельной машине с закрытым ключом gpg (компьютер администратора
# или отдельная ВМ без доступа из интернета). Ежемесячно по таймеру simpad-restore-drill или вручную:
#   bash tools/restore_drill.sh                  — берёт последнюю копию из хранилища (SIMPAD_BACKUP_BUCKET, доступ только на чтение)
#   bash tools/restore_drill.sh файл.dump.gpg    — локальный файл (рядом обязателен файл .sha256)
# Результат — в ~/simpad-drill/drill.log (права 600) и оповещение в Telegram.
set -euo pipefail
umask 077
WORK="${SIMPAD_DRILL_DIR:-$HOME/simpad-drill}"; mkdir -p "$WORK"; chmod 700 "$WORK"
IMG="postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
S3=(aws --endpoint-url https://storage.yandexcloud.net)
tmp="$(mktemp -d)"; name="simpad-restore-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
if [ $# -ge 1 ]; then
  f="$1"
else
  BUCKET="${SIMPAD_BACKUP_BUCKET:?укажите файл или SIMPAD_BACKUP_BUCKET}"
  key="$("${S3[@]}" s3 ls "s3://$BUCKET/" | awk '{print $4}' | grep -E '\.dump\.gpg$' | sort | tail -1)"
  [ -n "$key" ] || { echo "ОШИБКА: в хранилище нет копий" >&2; exit 1; }
  "${S3[@]}" s3 cp --only-show-errors "s3://$BUCKET/$key" "$tmp/$key"
  "${S3[@]}" s3 cp --only-show-errors "s3://$BUCKET/$key.sha256" "$tmp/$key.sha256"
  f="$tmp/$key"
fi
[ -f "$f.sha256" ] || { echo "ОШИБКА: нет файла контрольной суммы $f.sha256" >&2; exit 1; }
( cd "$(dirname "$f")" && sha256sum -c --quiet "$(basename "$f").sha256" )
gpg --batch --decrypt --output "$tmp/db.dump" "$f"
docker run -d --name "$name" -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=simpad -e POSTGRES_USER=simpad "$IMG" >/dev/null
for _ in $(seq 1 30); do docker exec "$name" pg_isready -U simpad -d simpad >/dev/null 2>&1 && break; sleep 2; done
docker exec -i "$name" pg_restore -U simpad -d simpad --no-owner --exit-on-error < "$tmp/db.dump"
counts="$(docker exec "$name" psql -U simpad -d simpad -Atc "select 'обращений '||count(*) from cases; select 'событий '||count(*) from bank_events; select 'миграция '||version_num from alembic_version;" | tr '\n' ' ')"
line="$(date -Is) OK $(basename "$f") $counts"
echo "$line" | tee -a "$WORK/drill.log"
bash "$(dirname "$0")/alert.sh" "учебное восстановление успешно: $line" || true
