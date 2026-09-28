#!/usr/bin/env bash
# Сертификаты mTLS для банков. Запускать на компьютере администратора (не в GitHub, не на общем диске).
#
#   bash tools/bank_cert.sh ca                 — один раз: корневой сертификат единого окна для банков (bank-ca.pem)
#   bash tools/bank_cert.sh bank tbank         — сертификат банка; выводит отпечаток SHA-256 для сервера
#
# Банку передаются по защищённому каналу: tbank.crt, tbank.key (или банк присылает свой CSR — см. docs/BANK_API.md).
# bank-ca.key хранится офлайн (флешка в сейфе). bank-ca.pem кладётся на сервер в server/bank-ca.pem.
set -euo pipefail
DIR="${SIMPAD_PKI_DIR:-./okno-pki}"
mkdir -p "$DIR" && chmod 700 "$DIR"
cd "$DIR"

case "${1:-}" in
  ca)
    [ -f bank-ca.key ] && { echo "bank-ca.key уже есть — не перезаписываю"; exit 1; }
    openssl req -x509 -newkey rsa:4096 -sha256 -days 1825 -nodes \
      -keyout bank-ca.key -out bank-ca.pem -subj "/C=RU/O=Edinoe okno/CN=Edinoe okno Bank Client CA" \
      -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign"
    chmod 600 bank-ca.key
    echo "Готово: $DIR/bank-ca.pem → на сервер (server/bank-ca.pem). $DIR/bank-ca.key — только офлайн."
    ;;
  bank)
    id="${2:?укажите код банка, например tbank}"
    [[ "$id" =~ ^[a-z0-9_-]+$ ]] || { echo "код банка: латиница, цифры, - _"; exit 1; }
    if [ -f "$id.csr" ]; then
      echo "Использую запрос банка $id.csr (закрытый ключ остаётся у банка)"
    else
      openssl req -newkey rsa:3072 -sha256 -nodes -keyout "$id.key" -out "$id.csr" -subj "/C=RU/O=$id/CN=$id.bank.okno"
      chmod 600 "$id.key"
    fi
    printf "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n" > "$id.ext"
    openssl x509 -req -in "$id.csr" -CA bank-ca.pem -CAkey bank-ca.key -CAcreateserial -days 397 -sha256 \
      -extfile "$id.ext" -out "$id.crt"
    fp=$(openssl x509 -in "$id.crt" -outform DER | openssl dgst -sha256 | awk '{print $NF}')
    echo "Сертификат: $DIR/$id.crt (действует 397 дней)"
    echo "Отпечаток SHA-256 для сервера: $fp"
    echo "Закрепить за банком:"
    echo "  curl -X PATCH https://<домен>/admin/v1/banks/$id -H 'X-Admin-Token: <ТОКЕН>' -H 'Content-Type: application/json' -d '{\"cert_fingerprint\":\"$fp\"}'"
    ;;
  *)
    sed -n '2,8p' "$0"; exit 1 ;;
esac
