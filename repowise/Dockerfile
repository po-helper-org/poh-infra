# Образ Repowise: сам пакет, git и индексатор.
#
# Отличается от `mcp/poh-org/images/Dockerfile.repowise` (локальный MCP Toolkit
# для интерактивных сессий) двумя вещами, и обе выяснены спайком FR-1
# (`po-helper-org/poh-issue-agents`, `docs/spikes/2026-08-19-repowise-headless.md`):
#
# 1. **`git` обязателен.** `python:3.11-slim` его не несёт, а без него
#    `repowise doctor` даёт `Git repository FAIL — Not a git repo`: история и
#    blame недоступны, то есть половина ценности индекса.
#
# 2. **Версия не ниже 0.44.0.** В 0.36.0 у `repowise mcp` нет флага `--host`,
#    сервер слушает только 127.0.0.1, и по сети эндпоинт недостижим — с хоста
#    соединение не устанавливается вовсе. Версия пинуется точно: индекс кода —
#    не то место, где хочется получить другое поведение от свежего релиза.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ARG REPOWISE_VERSION=0.44.0
RUN pip install --no-cache-dir "repowise==${REPOWISE_VERSION}" pyyaml

# Контейнер работает от root, а смонтированные клоны принадлежат другому
# пользователю — без этого git отказывается их читать («dubious ownership»),
# и выглядит это как «репозиторий не является git-репозиторием».
RUN git config --system --add safe.directory '*'

# Токен для клонирования приватных репозиториев отдаётся git через хелпер, а не
# подстановкой в URL: подставленный в argv токен уезжает в текст ЛЮБОГО
# исключения subprocess — в логи и в историю прогонов.
RUN printf '#!/bin/sh\ncase "$1" in\n  Username*) echo "x-access-token" ;;\n  Password*) echo "$REPOWISE_GIT_TOKEN" ;;\nesac\n' \
      > /usr/local/bin/git-askpass \
    && chmod +x /usr/local/bin/git-askpass

COPY indexer/indexer.py /app/indexer.py
WORKDIR /workspaces
