#!/usr/bin/env bash
# Зашифрованная резервная копия базы «Единого окна». Запускается systemd‑таймером и перед каждым обновлением.
# Шифрование — открытым ключом gpg; закрытый ключ на сервере НЕ хранится (сервер свою копию прочитать не может).
# Копия вне сервера обязательна: без SIMPAD_BACKUP_BUCKET скрипт завершается ошибкой
# (исключение — учебный сервер: SIMPAD_BACKUP_LOCAL_ONLY=1).
set -euo pipefail
cd "$(dirname "$0")/../server"
DIR="${SIMPAD_BACKUP_DIR:-$HOME/backup}"
RECIPIENT="${SIMPAD_BACKUP_GPG:?не задан SIMPAD_BACKUP_GPG (отпечаток открытого ключа gpg)}"
KEEP_DAYS="${SIMPAD_BACKUP_KEEP_DAYS:-14}"
BUCKET="${SIMPAD_BACKUP_BUCKET:-}"
S3=(aws --endpoint-url https://storage.yandexcloud.net)
if [ -z "$BUCKET" ] && [ "${SIMPAD_BACKUP_LOCAL_ONLY:-0}" != "1" ]; then
  echo "ОШИБКА: не задан SIMPAD_BACKUP_BUCKET — копия вне сервера обязательна" >&2; exit 1
fi
umask 077
mkdir -p "$DIR"
exec 9>"$DIR/.lock"
flock -n 9 || { echo "ОШИБКА: резервное копирование уже выполняется" >&2; exit 1; }
name="simpad-$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N4 -tx4 /dev/urandom | tr -d ' ')"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

docker compose --env-file .env exec -T db pg_dump -U simpad -d simpad --format=custom > "$tmp/db.dump"
[ -s "$tmp/db.dump" ] || { echo "ОШИБКА: пустой дамп" >&2; exit 1; }
docker compose --env-file .env exec -T db pg_restore --list < "$tmp/db.dump" > "$tmp/list.txt"
grep -q "TABLE DATA public cases" "$tmp/list.txt" || { echo "ОШИБКА: в дампе нет таблицы cases" >&2; exit 1; }

out="$DIR/$name.dump.gpg"
gpg --batch --yes --trust-model always --encrypt --recipient "$RECIPIENT" --output "$out" "$tmp/db.dump"
sum="$(sha256sum "$out" | cut -d' ' -f1)"
echo "$sum  $name.dump.gpg" > "$out.sha256"

remote="no"
if [ -n "$BUCKET" ]; then
  "${S3[@]}" s3 cp --only-show-errors "$out" "s3://$BUCKET/$name.dump.gpg"
  "${S3[@]}" s3 cp --only-show-errors "$out.sha256" "s3://$BUCKET/$name.dump.gpg.sha256"
  size_remote="$("${S3[@]}" s3api head-object --bucket "$BUCKET" --key "$name.dump.gpg" --query ContentLength --output text)"
  [ "$size_remote" = "$(stat -c %s "$out")" ] || { echo "ОШИБКА: размер копии в хранилище не совпадает" >&2; exit 1; }
  remote="yes"
fi

find "$DIR" -maxdepth 1 -name 'simpad-*.dump.gpg*' -mtime +"$KEEP_DAYS" -delete
printf '%s %s %s %s\n' "$(date -Is)" "$name.dump.gpg" "$sum" "$remote" > "$DIR/LAST_OK"
echo "Готово: $out (вне сервера: $remote)"
