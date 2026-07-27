# Design: профиль Docker MCP Toolkit «poh-org»

Дата: 2026-07-24
Статус: утверждён (готов к плану)

## Цель

Собрать единый профиль Docker MCP Toolkit для организации `poh-org`, дающий
AI-агенту набор MCP-серверов одной точкой активации через gateway. Профиль
активируется в клиенте Claude Code и покрывает мониторинг (Sentry), деплой
(Render, Dokploy), контроль контейнеров (Docker), понимание кода (Repowise),
задачи (Backlog), а также базовый инструментарий автономной работы (GitHub,
Filesystem, Sequential-thinking, Postgres).

## Архитектура

- Кастомный OCI-каталог `poh-org:latest` (зеркало существующего `mts:latest`)
  плюс профиль `poh_` (зеркало существующего профиля `mts_`).
- Профиль включает серверы из двух каталогов:
  - официального `mcp/docker-mcp-catalog:latest` — для готовых серверов;
  - кастомного `poh-org:latest` — для серверов, которых нет в официальном
    каталоге (собираются локально либо тянутся из community-образов).
- Все серверы работают под gateway `MCP_DOCKER`. Активация профиля в клиенте —
  один вход, весь набор.
- Секреты хранятся только в OS Keychain через `docker mcp secret set`. В YAML
  каталога кладутся ссылки на секреты (`secrets: [{name, env}]`), не значения.
  Токены вводит пользователь; агент их не получает и не пишет в файлы.

## Состав серверов (10)

### A. Из официального каталога — включить в профиль

| # | Сервер | Тип | Секрет / конфиг | Аутентификация |
|---|--------|-----|-----------------|----------------|
| 1 | `sentry-remote` | remote (SSE, `https://mcp.sentry.dev/sse`) | OAuth provider `sentry-remote` | `docker mcp oauth authorize sentry-remote` |
| 2 | `render` | image `mcp/render` | `render.api_key` → `RENDER_API_KEY` | секрет |
| 3 | `github-official` | image/remote | GitHub PAT или OAuth (решаем в плане) | секрет/OAuth |
| 4 | `filesystem` | image `mcp/filesystem` | монтаж рабочей области, список разрешённых путей в args | — |
| 5 | `sequentialthinking` | image | — | — |

### B. Кастомный каталог `poh-org` — образ + конфиг

| # | Сервер | Образ | Секрет / конфиг | Монтаж |
|---|--------|-------|-----------------|--------|
| 6 | `dokploy` | официальный `Dokploy/mcp`, если публикует образ; иначе node-wrapper поверх npm-пакета | `dokploy.api_key` → `DOKPLOY_API_KEY`; env `DOKPLOY_URL` | — |
| 7 | `docker` (control) | community-образ управления контейнерами | env `DOCKER_HOST` | `/var/run/docker.sock` ⚠️ |
| 8 | `repowise` | wrapper `python:3.11-slim` + `pip install repowise`, cmd `repowise mcp` | опц. `repowise.anthropic_api_key` → `ANTHROPIC_API_KEY` | целевой репозиторий как workdir |
| 9 | `backlog` | node-wrapper поверх npm `backlog.md`, cmd `backlog mcp start` | — | папка проекта Backlog (per-repo) |
| 10 | `postgres` | `crystaldba/postgres-mcp` | `postgres.database_uri` → `DATABASE_URI` | — |

## Сборка wrapper-образов

Серверы без готового официального образа собираются локально тонкими
Dockerfile'ами, тег `mcp/poh-<name>:latest`, ссылка из каталога `poh-org`:

- `mcp/poh-repowise` — база `python:3.11-slim`, `pip install repowise`,
  ENTRYPOINT `repowise mcp`. Сервер отдаёт данные из директории репозитория,
  поэтому рабочая папка монтируется как workdir.
- `mcp/poh-backlog` — база `node:22-alpine`, установка npm-пакета `backlog.md`,
  ENTRYPOINT `backlog mcp start`. Монтируется папка проекта с данными Backlog.
- `mcp/poh-dokploy` — только если официальный проект не публикует образ; тогда
  `node:22-alpine` поверх официального npm-пакета Dokploy MCP.

`docker` (control) и `postgres` берутся из community/готовых образов без сборки.

## Секреты (заводит пользователь)

```
docker mcp secret set render.api_key
docker mcp secret set dokploy.api_key
docker mcp secret set postgres.database_uri
docker mcp secret set repowise.anthropic_api_key   # опционально
# github: PAT через secret ИЛИ OAuth — решаем в плане
# sentry: без статического секрета, docker mcp oauth authorize sentry-remote
```

Env-значения без секретности (`DOKPLOY_URL`, `DOCKER_HOST`, разрешённые пути
filesystem) прописываются в определении сервера в каталоге.

## Что не трогаем

- Серверы, уже поднятые через gateway `MCP_DOCKER`: Jira, Confluence, Grafana,
  Obsidian, Fetch — остаются как есть.
- Прямые серверы Claude Code в отдельных репозиториях (Temporal, Context7) —
  не ломаем; при желании переносятся в профиль позже отдельной итерацией.

## Безопасность

- **Docker socket (сервер 7)**: доступ к `/var/run/docker.sock` даёт агенту
  полный контроль над контейнерами хоста. Включается осознанно. Область по
  возможности ограничить (отдельный Docker context / права).
- **Filesystem rw + сокет**: мощная связка. Список путей в args держать
  минимальным; по умолчанию монтировать рабочую область, а не весь диск.
- **Секреты**: только Keychain через `docker mcp secret`. Никогда не писать
  токены в YAML каталога, Dockerfile или git.
- **Postgres**: `DATABASE_URI` с правами не выше необходимых (предпочтительно
  read-only роль, если агенту не нужна запись).

## Решения, фиксируемые на этапе плана

Не заглушки — способ разрешения известен, финальный выбор в плане после проверки:

1. Точный community-образ `docker` control: сравнить кандидатов
   (`ckreiling/mcp-server-docker` и аналоги), проверить доступ через сокет.
2. Dokploy: официальный образ vs node-wrapper поверх npm-пакета — проверить,
   публикует ли `Dokploy/mcp` образ и точное имя пакета/транспорт (stdio/http).
3. `github-official`: PAT-секрет vs OAuth — выбрать по тому, что каталог
   определяет для этого сервера.
4. `backlog.md`: точное имя npm-пакета CLI и нужна ли инициализация проекта
   (`backlog init`) до `backlog mcp start`.

## Проверка (smoke)

- `docker mcp profile ls` показывает `poh_`.
- `docker mcp catalog ls` показывает `poh-org:latest`.
- Активация профиля в клиенте Claude Code поднимает 10 серверов, tools видны.
- По серверу:
  - sentry — список issues;
  - render — список сервисов;
  - github — whoami / список репозиториев;
  - dokploy — список проектов;
  - backlog — список задач;
  - postgres — `\dt` / список таблиц;
  - docker — `docker ps`;
  - repowise — overview/health по репозиторию;
  - filesystem — чтение файла из смонтированной области;
  - sequentialthinking — тестовый вызов.

## Критерий готовности

Профиль `poh_` активируется в Claude Code, все 10 серверов отвечают на smoke,
секреты в Keychain, кастомный каталог и Dockerfile'ы wrapper-образов
воспроизводимы из репозитория `poh-org` (каталог + Dockerfile'ы закоммичены в
инфраструктурный репозиторий, напр. `poh-infra`).
