"""Создаёт server/.env со всеми секретами. Файл не перезаписывается, если уже есть.

  python3 tools/genkeys.py server/.env okno.simpad.ru bank-okno.simpad.ru --training     # учебный сервер
  python3 tools/genkeys.py server/.env okno.simpad.ru bank-okno.simpad.ru --production   # рабочий сервер

Копию готового .env сохраните офлайн (флешка в сейфе): без SIMPAD_DATA_KEY данные в базе не прочитать.
"""
import base64
import os
import secrets
import sys

if len(sys.argv) != 5 or sys.argv[4] not in ("--training", "--production"):
    print(__doc__)
    sys.exit(1)
path, domain, bank_domain, mode = sys.argv[1:5]
if os.path.exists(path):
    sys.exit(f"{path} уже существует — не перезаписываю. Удалите его сами, если уверены.")
training = mode == "--training"
lines = [
    "# Создано tools/genkeys.py. Никому не пересылать, в GitHub и в чат не загружать.",
    f"SIMPAD_DOMAIN={domain}",
    f"SIMPAD_BANK_DOMAIN={bank_domain}",
    "# IP банков через пробел, из приложения ИБ к договору. Пока банков нет — 127.0.0.1/32 (вход закрыт)",
    "SIMPAD_BANK_IPS=127.0.0.1/32",
    "# Администрирование — только на самом сервере командой bash adm.sh; из интернета закрыто",
    "SIMPAD_ADMIN_IPS=127.0.0.1/32",
    "POSTGRES_PASSWORD=" + secrets.token_urlsafe(24),
    "SIMPAD_DATA_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "SIMPAD_DUP_KEY=" + secrets.token_urlsafe(32),
    "SIMPAD_ADMIN_TOKEN=" + secrets.token_urlsafe(32),
    "SIMPAD_ALLOW_DEMO_SCAN=" + ("true" if training else "false"),
    "SIMPAD_BANK_MTLS=true",
    "# Минимальная версия приложения планшета (versionCode рабочей сборки); 0 — не проверять",
    "SIMPAD_MIN_APP_VERSION=0",
]
if training:
    lines.append("# Учебный сервер собирает образ сам: docker compose -f docker-compose.yml -f docker-compose.build.yml")
    lines.append("SIMPAD_API_IMAGE=simpad-api:training-local")
else:
    lines.append("# Образ API задаёт tools/update.sh (подписанный выпуск GitHub по digest)")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"Готово: {path} ({'учебный' if training else 'рабочий'} сервер). Права 600.")
