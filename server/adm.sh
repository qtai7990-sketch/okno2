#!/usr/bin/env bash
# Администрирование на сервере:  cd ~/okno/server && bash adm.sh <команда> ...   (список: bash adm.sh help)
# Файлы .json из аргументов передаются в контейнер вместе со скриптом (контейнер только для чтения).
set -euo pipefail
cd "$(dirname "$0")"
args=()
prelude="FILES = {}"$'\n'
for a in "$@"; do
  if [[ "$a" == *.json && -f "$a" ]]; then
    name="upload/$(basename "$a")"
    prelude+="FILES[$(printf '%q' "$name" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().strip()))')] = \"$(base64 -w0 "$a")\""$'\n'
    args+=("$name")
  else
    args+=("$a")
  fi
done
{ printf '%s' "$prelude"; cat ../tools/admin.py; } | docker compose --env-file .env exec -T api python - "${args[@]}"
