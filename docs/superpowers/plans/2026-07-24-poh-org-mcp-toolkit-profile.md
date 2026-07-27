# POH Org — профиль Docker MCP Toolkit: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (инлайн, инфра-раннбук). Шаги — чекбоксы `- [ ]`.

**Goal:** собрать каталог `poh-org` + профиль `poh_` в Docker MCP Toolkit с 10 серверами и подключить к клиенту Claude Code.

**Architecture:** 5 серверов из официального каталога включаются в профиль напрямую; 5 серверов без готового образа собираются локально (wrapper-образы) и описываются в кастомном каталоге `poh-org`. Профиль `poh_` объединяет оба каталога; gateway активирует профиль для клиента `claude-code`.

**Tech Stack:** Docker MCP Toolkit CLI (`docker mcp`), Docker (сборка образов), Python 3.11 / Node 22 базовые образы, OS Keychain (секреты).

## Global Constraints

- Секреты только в OS Keychain через `docker mcp secret set`. Никогда не писать токены в YAML/Dockerfile/git.
- Артефакты (Dockerfile, catalog YAML, README) коммитятся в репозиторий `poh-infra`.
- Не трогать серверы, уже поднятые в gateway `MCP_DOCKER` (Jira/Confluence/Grafana/Obsidian/Fetch) и прямые серверы Claude Code (Temporal/Context7).
- Существующие профиль `mts_` и каталог `mts:latest` не изменять.
- Схема монтажей в catalog-def: `volumes:` + config-параметры с шаблоном `{{param|volume|into}}` / `{{param|volume-target|into}}` (как в официальном `filesystem`).

## Референсы (проверенные факты)

| Сервер | Источник | Образ/пакет | Секрет/конфиг |
|--------|----------|-------------|---------------|
| sentry | офиц. каталог `sentry-remote` | remote SSE `https://mcp.sentry.dev/sse` | OAuth `sentry-remote` |
| render | офиц. каталог `render` | `mcp/render` | `render.api_key` → `RENDER_API_KEY` |
| github | офиц. каталог `github-official` | `ghcr.io/github/github-mcp-server` | OAuth провайдер github |
| filesystem | офиц. каталог `filesystem` | `mcp/filesystem` | config `filesystem.paths` |
| sequentialthinking | офиц. каталог | image | — |
| dokploy | npm `@dokploy/mcp` | wrapper `mcp/poh-dokploy` | `DOKPLOY_URL`, `dokploy.api_key`→`DOKPLOY_API_KEY` |
| docker | `ckreiling/mcp-server-docker` (build) | `mcp/poh-docker` | volume `/var/run/docker.sock` |
| repowise | pip `repowise` | wrapper `mcp/poh-repowise` | опц. `repowise.anthropic_api_key`→`ANTHROPIC_API_KEY` |
| backlog | npm `backlog.md`@1.44 | wrapper `mcp/poh-backlog` | env `BACKLOG_CWD` (монтаж репо) |
| postgres | `crystaldba/postgres-mcp` | образ есть | `postgres.database_uri`→`DATABASE_URI` |

Файлы-артефакты (репозиторий `poh-infra`):
- `mcp/poh-org/images/Dockerfile.repowise`
- `mcp/poh-org/images/Dockerfile.backlog`
- `mcp/poh-org/images/Dockerfile.dokploy`
- `mcp/poh-org/images/Dockerfile.docker` (или клон ckreiling)
- `mcp/poh-org/poh-org.catalog.yaml`
- `mcp/poh-org/README.md`

---

## Task 1: Baseline и проверка окружения

**Files:** — (только проверки)

- [ ] Проверить Docker и toolkit:
  - Run: `docker version --format '{{.Server.Version}}' && docker mcp version`
  - Expected: версии печатаются без ошибок.
- [ ] Зафиксировать текущее состояние (не должно меняться у mts):
  - Run: `docker mcp profile ls && docker mcp catalog ls`
  - Expected: виден профиль `mts_` и каталоги `mcp/docker-mcp-catalog:latest`, `mts:latest`.
- [ ] Commit каркаса артефактов:
  ```bash
  cd /Users/aleksishmanov/projects/poh-org/poh-infra
  git add docs/superpowers/plans mcp/poh-org
  git commit -m "chore(mcp): scaffold poh-org toolkit profile plan"
  ```

## Task 2: wrapper-образ repowise

**Files:** Create `mcp/poh-org/images/Dockerfile.repowise`

- [ ] Написать Dockerfile:
  ```dockerfile
  FROM python:3.11-slim
  RUN pip install --no-cache-dir repowise
  WORKDIR /workspace
  ENTRYPOINT ["repowise", "mcp"]
  ```
- [ ] Собрать:
  - Run: `docker build -t mcp/poh-repowise:latest -f mcp/poh-org/images/Dockerfile.repowise mcp/poh-org/images`
  - Expected: `naming to docker.io/mcp/poh-repowise:latest ... DONE`.
- [ ] Smoke (сервер стартует по stdio и не падает мгновенно):
  - Run: `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n' | docker run -i --rm -v "$PWD":/workspace mcp/poh-repowise:latest 2>&1 | head -3`
  - Expected: JSON-ответ `initialize` с `serverInfo` (repowise), без stacktrace.

## Task 3: wrapper-образ backlog

**Files:** Create `mcp/poh-org/images/Dockerfile.backlog`

- [ ] Написать Dockerfile:
  ```dockerfile
  FROM node:22-alpine
  RUN npm install -g backlog.md@1.44.0
  ENV BACKLOG_CWD=/workspace
  WORKDIR /workspace
  ENTRYPOINT ["backlog", "mcp", "start"]
  ```
- [ ] Собрать:
  - Run: `docker build -t mcp/poh-backlog:latest -f mcp/poh-org/images/Dockerfile.backlog mcp/poh-org/images`
  - Expected: `naming to ... mcp/poh-backlog:latest ... DONE`.
- [ ] Smoke:
  - Run: `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n' | docker run -i --rm -v "$PWD":/workspace mcp/poh-backlog:latest 2>&1 | head -3`
  - Expected: JSON `initialize` с `serverInfo` (backlog), без ошибок.

## Task 4: wrapper-образ dokploy

**Files:** Create `mcp/poh-org/images/Dockerfile.dokploy`

- [ ] Написать Dockerfile:
  ```dockerfile
  FROM node:22-alpine
  RUN npm install -g @dokploy/mcp
  ENTRYPOINT ["dokploy-mcp"]
  ```
  Примечание: если бинарь называется иначе — подтвердить `docker run --rm mcp/poh-dokploy:latest --help` и поправить ENTRYPOINT на `npx -y @dokploy/mcp`.
- [ ] Собрать:
  - Run: `docker build -t mcp/poh-dokploy:latest -f mcp/poh-org/images/Dockerfile.dokploy mcp/poh-org/images`
  - Expected: сборка DONE.
- [ ] Smoke (без креденшелов только проверка старта stdio):
  - Run: `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n' | docker run -i --rm -e DOKPLOY_URL=http://localhost -e DOKPLOY_API_KEY=dummy mcp/poh-dokploy:latest 2>&1 | head -3`
  - Expected: JSON `initialize` с `serverInfo`; ошибки auth допустимы позже при вызове тулов, не на initialize.

## Task 5: образ docker-control

**Files:** Create `mcp/poh-org/images/Dockerfile.docker` (обёртка над клоном) либо клонировать ckreiling

- [ ] Клонировать и собрать:
  ```bash
  git clone --depth 1 https://github.com/ckreiling/mcp-server-docker mcp/poh-org/images/mcp-server-docker
  docker build -t mcp/poh-docker:latest mcp/poh-org/images/mcp-server-docker
  ```
  - Expected: `mcp/poh-docker:latest ... DONE`.
- [ ] Smoke (с монтажом сокета, `docker ps` доступен серверу):
  - Run: `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n' | docker run -i --rm -v /var/run/docker.sock:/var/run/docker.sock mcp/poh-docker:latest 2>&1 | head -3`
  - Expected: JSON `initialize` с `serverInfo`.

## Task 6: кастомный каталог `poh-org`

**Files:** Create `mcp/poh-org/poh-org.catalog.yaml`

- [ ] Подтвердить точный синтаксис add (CLI-плагин капризен):
  - Run: `docker mcp catalog create poh-org && docker mcp catalog server add poh-org --help 2>&1 | head -20`
  - Expected: usage `catalog server add` с указанием, принимает ли файл спеки или имя сервера.
- [ ] Описать 5 кастомных серверов в `poh-org.catalog.yaml` по схеме, которую показывает `docker mcp catalog show mts:latest` (поля `name/type/image/description/title/secrets/env/command/volumes`). Ключевые определения:
  - `dokploy`: image `mcp/poh-dokploy:latest`; `env: [{name: DOKPLOY_URL, value: "{{dokploy.url}}"}]`; `secrets: [{name: dokploy.api_key, env: DOKPLOY_API_KEY}]`.
  - `docker`: image `mcp/poh-docker:latest`; `volumes: ["/var/run/docker.sock:/var/run/docker.sock"]`. ⚠️ доступ к Docker-хосту.
  - `repowise`: image `mcp/poh-repowise:latest`; `volumes: ["{{repowise.workdir|volume|into}}"]`; опц. `secrets: [{name: repowise.anthropic_api_key, env: ANTHROPIC_API_KEY}]`.
  - `backlog`: image `mcp/poh-backlog:latest`; `volumes: ["{{backlog.workdir|volume|into}}"]` (монтируется в `/workspace`, совпадает с `BACKLOG_CWD`).
  - `postgres`: image `crystaldba/postgres-mcp:latest`; `secrets: [{name: postgres.database_uri, env: DATABASE_URI}]`.
- [ ] Загрузить серверы в каталог (командой из подтверждённого синтаксиса add).
- [ ] Проверить:
  - Run: `docker mcp catalog show poh-org`
  - Expected: перечислены dokploy, docker, repowise, backlog, postgres с корректными образами/секретами.

## Task 7: профиль `poh_`

**Files:** — (состояние toolkit)

- [ ] Создать профиль:
  - Run: `docker mcp profile create poh_`
  - Expected: профиль создан; виден в `docker mcp profile ls`.
- [ ] Подтвердить синтаксис добавления серверов:
  - Run: `docker mcp profile server add poh_ --help 2>&1 | head -20`
  - Expected: usage `profile server add`.
- [ ] Добавить 10 серверов в профиль: официальные `sentry-remote render github-official filesystem sequentialthinking` + кастомные `dokploy docker repowise backlog postgres`.
- [ ] Задать config-параметры (немысекретные):
  - `filesystem.paths` = список разрешённых путей (рабочая область poh-org, минимально нужное).
  - `dokploy.url` = URL инстанса Dokploy.
  - `repowise.workdir` / `backlog.workdir` = путь целевого репозитория.
  - Команда: `docker mcp profile config poh_ <key> <value>` (подтвердить синтаксис `docker mcp profile config --help`).
- [ ] Проверить:
  - Run: `docker mcp profile show poh_`
  - Expected: 10 серверов, config-параметры выставлены, секреты помечены как требуемые.

## Task 8: секреты и OAuth — ⚠️ ДЕЙСТВИЯ ПОЛЬЗОВАТЕЛЯ

Агент НЕ вводит токены (правила безопасности + non-interactive). Пользователь выполняет сам:

- [ ] Секреты (значения из STDIN, не в истории shell):
  ```bash
  printf %s "<RENDER_API_KEY>"        | docker mcp secret set render.api_key
  printf %s "<DOKPLOY_API_KEY>"       | docker mcp secret set dokploy.api_key
  printf %s "<DATABASE_URI>"          | docker mcp secret set postgres.database_uri
  printf %s "<ANTHROPIC_API_KEY>"     | docker mcp secret set repowise.anthropic_api_key   # опционально
  ```
- [ ] OAuth:
  ```bash
  docker mcp oauth authorize sentry-remote
  docker mcp oauth authorize github          # имя провайдера подтвердить: docker mcp oauth ls
  ```
- [ ] Проверить: `docker mcp secret ls` содержит render/dokploy/postgres (+repowise), `docker mcp oauth ls` показывает authorized для sentry/github.

## Task 9: подключение клиента и активация профиля

**Files:** — (клиентский конфиг Claude Code)

- [ ] Подключить toolkit к клиенту:
  - Run: `docker mcp client connect claude-code`
  - Expected: сообщение об успешном подключении; `docker mcp client ls` показывает claude-code.
- [ ] Активировать профиль `poh_` (mutually exclusive с --servers): через `docker mcp profile` активацию либо gateway `--profile poh_`. Подтвердить механизм активного профиля для клиента (`docker mcp profile --help`, наличие `use`/`activate`, либо gateway-тул `mcp-activate-profile`).
- [ ] Проверить:
  - Run: `docker mcp gateway run --profile poh_ --dry-run 2>&1 | head` (если поддерживается) либо перезапуск клиента.
  - Expected: gateway поднимает 10 серверов профиля.

## Task 10: smoke весь профиль

**Files:** — (проверки через клиента/gateway)

- [ ] По каждому серверу минимальный вызов (через клиента Claude Code с активным `poh_`):
  - sentry — список организаций/issues;
  - render — список сервисов;
  - github — whoami / список репозиториев;
  - dokploy — список проектов;
  - backlog — список задач;
  - postgres — список таблиц;
  - docker — `docker ps` (список контейнеров);
  - repowise — overview/health репозитория;
  - filesystem — чтение файла из смонтированной области;
  - sequentialthinking — тестовый вызов.
- [ ] Expected: каждый сервер отвечает без ошибок инициализации/auth.

## Task 11: закоммитить артефакты + README

**Files:** Create `mcp/poh-org/README.md`; Modify репозиторий poh-infra

- [ ] README: что за профиль, список серверов, как пересобрать образы, как выставить секреты/OAuth, как активировать `poh_`, риски (docker socket, filesystem scope). Без токенов.
- [ ] Скопировать спеку дизайна рядом (`docs/superpowers/specs/`), исключить клон ckreiling из git (`.gitignore` на `images/mcp-server-docker/`).
- [ ] Commit:
  ```bash
  cd /Users/aleksishmanov/projects/poh-org/poh-infra
  git add mcp/poh-org docs/superpowers
  git commit -m "feat(mcp): poh-org toolkit profile — catalog, wrapper images, runbook"
  ```

---

## Self-Review (coverage)

- Sentry/Render/Docker/Render.com/Repowise/Backlog из запроса — Tasks 2-7, 9-10 ✅
- «Другие полезные MCP» (GitHub/Filesystem/Sequential-thinking/Postgres) — Tasks 6-7 ✅
- Docker = управление контейнерами (выбор пользователя) — Task 5 ✅
- «Всё в Docker-профиле» (выбор пользователя) — кастомный каталог + профиль, Tasks 6-7 ✅
- Секреты руками пользователя — Task 8 (граница) ✅
- Безопасность (socket/fs/секреты) — Global Constraints + Tasks 5/6/11 ✅

## Точки живого подтверждения синтаксиса (CLI-плагин глотает `--help` у подгрупп)

1. `catalog server add` — формат входа (файл спеки vs имя). Task 6.
2. `profile server add` — добавление серверов. Task 7.
3. `profile config` — установка config-параметров. Task 7.
4. Активация профиля для клиента (`use`/`activate` vs gateway `--profile`). Task 9.
5. Бинарь Dokploy MCP (`dokploy-mcp` vs `npx @dokploy/mcp`). Task 4.
