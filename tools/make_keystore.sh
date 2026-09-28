#!/usr/bin/env bash
# Ключ подписи приложения для планшетов. Создаётся ОДИН раз на компьютере администратора, не в GitHub.
# Нужна Java (keytool). Результат: simpad.jks и файл с паролем — хранить офлайн (флешка в сейфе + копия).
# Затем вручную внести в GitHub → Settings → Secrets and variables → Actions:
#   SIMPAD_KEYSTORE_B64 (содержимое simpad.jks.b64), SIMPAD_KEYSTORE_PASSWORD, SIMPAD_KEY_PASSWORD, SIMPAD_KEY_ALIAS=simpad
#   и переменную SIMPAD_SIGNING_CERT_SHA256 (файл SIGNING_CERT_SHA256.txt)
# Потеря ключа = невозможность обновить приложение на планшетах без переустановки.
set -euo pipefail
OUT="${1:-./okno-keystore}"
mkdir -p "$OUT" && chmod 700 "$OUT" && cd "$OUT"
[ -f simpad.jks ] && { echo "simpad.jks уже есть — не перезаписываю"; exit 1; }
PASS=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
keytool -genkeypair -keystore simpad.jks -alias simpad -keyalg RSA -keysize 3072 -validity 10000 \
  -storepass "$PASS" -keypass "$PASS" -dname "CN=Edinoe okno, O=Edinoe okno, C=RU"
base64 -w0 simpad.jks > simpad.jks.b64 2>/dev/null || base64 -i simpad.jks > simpad.jks.b64
umask 077; printf '%s' "$PASS" > PASSWORD.txt
fp="$(keytool -list -v -keystore simpad.jks -alias simpad -storepass "$PASS" | grep -m1 'SHA256:' | awk '{print $2}' | tr -d ':' | tr 'A-F' 'a-f')"
printf '%s' "$fp" > SIGNING_CERT_SHA256.txt
echo "Готово: $OUT/simpad.jks, $OUT/simpad.jks.b64, $OUT/PASSWORD.txt"
echo "SHA-256 сертификата подписи (переменная SIMPAD_SIGNING_CERT_SHA256): $fp"
echo "После внесения секретов в GitHub удалите simpad.jks.b64. simpad.jks и PASSWORD.txt — только офлайн."
