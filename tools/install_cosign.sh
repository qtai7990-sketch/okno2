#!/usr/bin/env bash
# Установка cosign (проверка подписи образа сервера) с проверкой контрольной суммы. Версия закреплена.
set -euo pipefail
VER=v2.6.5
SUM=c3b4f5410e608af03a5eb0aaac84a4313d8da131248e08ff1759ac70c79d1644
tmp="$(mktemp)"
curl -fsSL -o "$tmp" "https://github.com/sigstore/cosign/releases/download/$VER/cosign-linux-amd64"
echo "$SUM  $tmp" | sha256sum -c --quiet
sudo install -m 0755 "$tmp" /usr/local/bin/cosign
rm -f "$tmp"
cosign version | grep GitVersion
