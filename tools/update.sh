#!/usr/bin/env bash
# Обновление рабочего сервера до выпуска. Образ сервера собирает и подписывает GitHub (workflow «Выпуск образа сервера»);
# на сервере ничего не собирается. Перед обновлением — резервная копия (миграции схемы необратимы).
#   bash tools/update.sh v0.2.4 ghcr.io/<организация>/simpad-api@sha256:<digest>
# Digest берётся из итогов выпуска в GitHub (файл release-digest.txt).
set -euo pipefail
tag="${1:?укажите тег версии, например v0.2.4}"
image="${2:?укажите образ с digest: ghcr.io/…/simpad-api@sha256:…}"
[[ "$image" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$ ]] || { echo "ОШИБКА: образ должен быть указан по digest" >&2; exit 1; }
cd "$(dirname "$0")/.."
REPO="${SIMPAD_GITHUB_REPO:?не задан SIMPAD_GITHUB_REPO, например okno-zia/okno}"
# Подпись образа: только workflow выпуска этого репозитория по тегу версии (подпись без ключей, sigstore)
cosign verify "$image" \
  --certificate-identity-regexp "^https://github.com/${REPO}/\.github/workflows/server-release\.yml@refs/tags/v" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com > /dev/null
echo "Подпись образа проверена"
prev="$(git rev-parse --short HEAD)"
if [ -n "$(cd server && docker compose --env-file .env ps -q db 2>/dev/null)" ]; then
  bash tools/backup.sh          # копия базы до любых изменений
else
  echo "Первый запуск: базы ещё нет, резервная копия не нужна"
fi
git fetch --tags --force
git -c advice.detachedHead=false checkout --quiet "$tag"
cd server
sed -i '/^SIMPAD_API_IMAGE=/d' .env && echo "SIMPAD_API_IMAGE=$image" >> .env
docker compose --env-file .env pull api
docker compose --env-file .env up -d
for _ in $(seq 1 40); do
  st="$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose --env-file .env ps -q api)" 2>/dev/null || true)"
  [ "$st" = "healthy" ] && { echo "Обновлено до $tag ($image), сервер здоров"; exit 0; }
  sleep 3
done
bash ../tools/alert.sh "обновление до $tag не поднялось" || true
echo "ОШИБКА: сервер не стал здоровым. Откат: git checkout $prev, в server/.env вернуть прежний SIMPAD_API_IMAGE," >&2
echo "docker compose --env-file .env up -d. Если миграция повредила данные — восстановить копию из ${SIMPAD_BACKUP_DIR:-\$HOME/backup} (docs/DEPLOY.md, раздел 8)." >&2
exit 1
