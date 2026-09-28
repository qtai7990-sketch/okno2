#!/usr/bin/env bash
# Фиксация зависимостей Android на своей машине (обычно это делает workflow «Фиксация зависимостей Android»).
# Создаёт android/app/gradle.lockfile и android/gradle/verification-metadata.xml; их проверяет человек и добавляет
# в репозиторий через pull request. Одноразовый ключ и адрес нужны только чтобы учесть все библиотеки рабочей сборки.
set -euo pipefail
cd "$(dirname "$0")/../android"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
keytool -genkeypair -keystore "$tmp/throwaway.jks" -alias t -keyalg RSA -keysize 2048 -validity 1 \
  -storepass throwaway -keypass throwaway -dname "CN=throwaway" >/dev/null
export SIMPAD_KEYSTORE_PATH="$tmp/throwaway.jks" SIMPAD_KEYSTORE_PASSWORD=throwaway SIMPAD_KEY_ALIAS=t SIMPAD_KEY_PASSWORD=throwaway
export SIMPAD_SERVER_URL=https://lock.invalid.example
export SIMPAD_CERT_PINS="C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=,diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI="
chmod +x gradlew
./gradlew --no-daemon :app:dependencies --write-locks > /dev/null
./gradlew --no-daemon --write-verification-metadata sha256 assembleDemoDebug assembleProdRelease
echo "Готово: android/app/gradle.lockfile и android/gradle/verification-metadata.xml — проверить и добавить в репозиторий."
