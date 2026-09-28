"""Проверка настроек развёртывания в CI: образы по digest, mTLS не отключён по умолчанию, закрытая сеть, нет лишних портов."""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
errors = []
compose = (root / "server/docker-compose.yml").read_text(encoding="utf-8")
docker = (root / "server/Dockerfile").read_text(encoding="utf-8")
caddy = (root / "server/Caddyfile").read_text(encoding="utf-8")
for img in re.findall(r"^\s*image:\s*(\S+)", compose, re.M):
    if img.startswith("${SIMPAD_API_IMAGE"):
        continue  # образ API задаётся при обновлении и проверяется tools/update.sh (digest + подпись)
    if "@sha256:" not in img:
        errors.append(f"образ без digest: {img}")
if "image: ${SIMPAD_API_IMAGE:?" not in compose or re.search(r"^\s*build:", compose, re.M):
    errors.append("рабочий compose: API только из выпущенного образа (SIMPAD_API_IMAGE), без сборки на сервере")
if not (root / ".dockerignore").exists() or "**/.env" not in (root / ".dockerignore").read_text(encoding="utf-8"):
    errors.append("нет .dockerignore, исключающего .env и ключи")
for img in re.findall(r"^FROM\s+(\S+)", docker, re.M):
    if "@sha256:" not in img:
        errors.append(f"базовый образ без digest: {img}")
if "SIMPAD_BANK_MTLS: ${SIMPAD_BANK_MTLS:-true}" not in compose:
    errors.append("mTLS по умолчанию должен быть включён")
if not re.search(r"private:\s*\n\s*internal:\s*true", compose):
    errors.append("сеть private должна быть internal: true")
if len(re.findall(r"^\s*ports:", compose, re.M)) != 1:
    errors.append("порты наружу публикует только caddy")
if "--forwarded-allow-ips='*'" in docker or "--proxy-headers" in docker:
    errors.append("не доверять X-Forwarded-* от всех адресов")
if "require_and_verify" not in caddy or "header_up -X-Client-Cert-Sha256" not in caddy:
    errors.append("Caddy: mTLS банков или удаление заголовка сертификата отсутствует")
if "cert_issuer acme" not in caddy:
    errors.append("Caddy: сертификаты только Let's Encrypt (под закрепление в приложении)")
if errors:
    print("\n".join("ОШИБКА: " + e for e in errors))
    sys.exit(1)
print("Настройки развёртывания в порядке")
