#!/usr/bin/env bash
# v0.3.0: запустите после Fetch/Pull в GitHub Desktop и до Commit/Push.
# Не меняет исходный код и не публикует изменения.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: tools/preflight_v030.sh должен запускаться из рабочего Git-репозитория." >&2
  exit 2
fi

if [[ "${SIMPAD_SKIP_GIT_FETCH:-0}" != "1" ]]; then
  echo "[1/5] Fetch origin..."
  git fetch --prune origin
else
  echo "[1/5] Fetch origin пропущен (SIMPAD_SKIP_GIT_FETCH=1)."
fi

if git show-ref --verify --quiet refs/remotes/origin/main; then
  behind="$(git rev-list --count HEAD..origin/main)"
  if [[ "$behind" != "0" ]]; then
    echo "ERROR: Локальная ветка отстаёт от origin/main на $behind коммит(ов)." >&2
    echo "В GitHub Desktop выполните Pull origin, разрешите конфликты и запустите скрипт заново." >&2
    exit 3
  fi
else
  echo "ERROR: Нет ссылки origin/main после Fetch. Проверьте подключение к GitHub." >&2
  exit 3
fi

VENV="${SIMPAD_PREFLIGHT_VENV:-$ROOT/.venv-preflight}"
if [[ ! -x "$VENV/bin/python" ]]; then
  printf '[2/5] Creating isolated Python environment...\n'
  python3 -m venv "$VENV"
else
  printf '[2/5] Refreshing isolated Python environment...\n'
fi
"$VENV/bin/pip" install --quiet --require-hashes --no-deps -r server/requirements-dev.lock

printf '[3/5] Python syntax...\n'
"$VENV/bin/python" -m py_compile server/app/main.py server/app/models.py server/app/queue.py

printf '[4/5] Server tests...\n'
(
  cd server
  PYTHONPATH=. "$VENV/bin/python" -m pytest -q
)

printf '[5/5] Android demo APK...\n'
(
  cd android
  bash ./gradlew :app:assembleDemoDebug --stacktrace
)

echo "OK: origin/main актуален, тесты прошли, demo APK собран."
