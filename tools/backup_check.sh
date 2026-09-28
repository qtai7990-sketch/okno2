#!/usr/bin/env bash
# Ежедневная проверка копии (таймер simpad-backup-check): последняя копия не старше 26 ч, файл на месте,
# контрольная сумма совпадает, копия есть в хранилище. Любое расхождение — код 1 и оповещение (OnFailure).
set -euo pipefail
DIR="${SIMPAD_BACKUP_DIR:-$HOME/backup}"
BUCKET="${SIMPAD_BACKUP_BUCKET:-}"
[ -f "$DIR/LAST_OK" ] || { echo "ТРЕВОГА: резервных копий нет"; exit 1; }
read -r ts file sum remote < "$DIR/LAST_OK"
age=$(( $(date +%s) - $(date -d "$ts" +%s) ))
[ "$age" -le $((26 * 3600)) ] || { echo "ТРЕВОГА: последняя копия $((age / 3600)) ч назад"; exit 1; }
[ -f "$DIR/$file" ] || { echo "ТРЕВОГА: файл $file отсутствует"; exit 1; }
[ "$(sha256sum "$DIR/$file" | cut -d' ' -f1)" = "$sum" ] || { echo "ТРЕВОГА: контрольная сумма $file не совпадает"; exit 1; }
if [ -n "$BUCKET" ]; then
  [ "$remote" = "yes" ] || { echo "ТРЕВОГА: копия не выгружена в хранилище"; exit 1; }
  aws --endpoint-url https://storage.yandexcloud.net s3api head-object --bucket "$BUCKET" --key "$file" >/dev/null \
    || { echo "ТРЕВОГА: копии $file нет в хранилище"; exit 1; }
elif [ "${SIMPAD_BACKUP_LOCAL_ONLY:-0}" != "1" ]; then
  echo "ТРЕВОГА: хранилище вне сервера не настроено"; exit 1
fi
echo "OK: $file, $((age / 3600)) ч назад, сумма совпадает, в хранилище: $remote"
