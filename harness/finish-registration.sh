#!/usr/bin/env bash
# Обмен одноразового кода манифеста на учётные данные GitHub App.
#
# Запускается ЛОКАЛЬНО: приватный ключ приложения не должен появляться нигде,
# кроме файла, который ты сам вставишь в Environment. Код одноразовый и живёт
# около часа с момента нажатия «Create GitHub App».
#
# Использование:  bash finish-registration.sh <code> [файл.env]
set -euo pipefail

CODE="${1:?использование: finish-registration.sh <code> [файл.env]}"
OUT="${2:-./harness-app.env}"

for tool in curl python3; do
  command -v "$tool" >/dev/null || { echo "нужен $tool"; exit 1; }
done

resp="$(curl -fsS -X POST -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/app-manifests/${CODE}/conversions")" || {
  echo "обмен не прошёл — код просрочен или уже использован"; exit 1; }

python3 - "$resp" "$OUT" <<'PY'
import json, base64, sys, os
data, out = json.loads(sys.argv[1]), sys.argv[2]
if "id" not in data:
    sys.exit(f"в ответе нет приложения: {str(data)[:200]}")
pem = base64.b64encode(data["pem"].encode()).decode()
# Имена — те, что ждёт харнесс (см. .env.example, раздел «Два GitHub App»).
lines = [
    f"ISSUE_APP_ID={data['id']}",
    f"ISSUE_APP_WEBHOOK_SECRET={data['webhook_secret']}",
    f"ISSUE_APP_PRIVATE_KEY_B64={pem}",
]
# 0600 сразу при создании: файл с приватным ключом не должен ни секунды лежать
# с правами по умолчанию.
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"приложение {data['slug']} (id {data['id']}) → {out}")
print("установить:", data["html_url"] + "/installations/new")
PY
