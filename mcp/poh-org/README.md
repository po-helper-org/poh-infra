# POH Org — Docker MCP Toolkit profile

Единый профиль `poh_` в Docker MCP Toolkit: один gateway отдаёт AI-агенту
весь набор MCP-серверов организации. Каталог `poh-org:latest` собирает 10
серверов (5 официальных + 5 кастомных на локально собранных образах).

## Серверы (10)

| Сервер | Источник | Секрет / конфиг | Статус сборки |
|--------|----------|-----------------|---------------|
| sentry-remote | офиц. каталог (remote SSE) | OAuth `sentry-remote` | — |
| render | офиц. каталог `mcp/render` | secret `render.api_key` | — |
| github-official | офиц. `ghcr.io/github/github-mcp-server` | secret `github.personal_access_token` **или** OAuth `github` | — |
| filesystem | офиц. `mcp/filesystem` | config `filesystem.paths` | — |
| sequentialthinking | офиц. каталог | — | — |
| dokploy | wrapper `mcp/poh-dokploy` (`@dokploy/mcp`) | config `dokploy.url` + secret `dokploy.api_key` | build |
| docker | wrapper `mcp/poh-docker` (ckreiling) | volume `/var/run/docker.sock` ⚠️ | build |
| repowise | wrapper `mcp/poh-repowise` (pip `repowise`) | config `repowise.mount`; опц. secret `repowise.anthropic_api_key` | build |
| backlog | wrapper `mcp/poh-backlog` (`backlog.md`) | config `backlog.mount` | build |
| postgres | `crystaldba/postgres-mcp` | secret `postgres.database_uri` | — |

Проверено на dry-run: 8/10 стартуют и отдают tools сразу; sentry ждёт OAuth,
dokploy ждёт `dokploy.url` + api_key.

## Сборка / пересборка

```bash
./setup.sh
```

Скрипт собирает образы, создаёт каталог `poh-org:latest`, профиль `poh_`,
добавляет серверы и выставляет host-специфичные дефолты монтажей. Секреты,
OAuth и `dokploy.url` он НЕ трогает — это шаги пользователя ниже.

## Шаги пользователя (креды — только ты, агент их не вводит)

Секреты кладутся в OS Keychain через STDIN (не попадают в историю shell):

```bash
printf %s 'RENDER_API_KEY_VALUE'   | docker mcp secret set render.api_key
printf %s 'DOKPLOY_API_KEY_VALUE'  | docker mcp secret set dokploy.api_key
printf %s 'postgresql://user:pass@host:5432/db' | docker mcp secret set postgres.database_uri
printf %s 'GITHUB_PAT_VALUE'       | docker mcp secret set github.personal_access_token   # либо OAuth ниже
printf %s 'ANTHROPIC_KEY'          | docker mcp secret set repowise.anthropic_api_key      # опционально (авто-доки)
```

OAuth:

```bash
docker mcp oauth authorize sentry-remote
docker mcp oauth authorize github      # альтернатива PAT-секрету
```

Deployment-конфиг Dokploy:

```bash
docker mcp profile config poh_ --set dokploy.url=https://your-dokploy-host
```

Проверка:

```bash
docker mcp secret ls
docker mcp oauth ls
docker mcp gateway run --profile poh_ --dry-run 2>&1 | grep -E "tools\)|Can't start"
```

## Активация профиля для клиента

Профиль выбирается флагом gateway `--profile poh_`. Активировать для Claude Code:

```bash
docker mcp client connect claude-code      # если toolkit ещё не подключён к клиенту
# затем клиент запускает gateway с активным профилем poh_
```

⚠️ Профиль `mts_` (Jira/Confluence MTS) отдельный. Активация `poh_` НЕ удаляет
`mts_` — переключаешься между ними по необходимости. Держать оба одновременно
для одного клиента нельзя (`--profile` взаимоисключающий).

## Безопасность

- **docker** монтирует `/var/run/docker.sock` — агент управляет контейнерами
  хоста. Включено осознанно; ограничивай область при необходимости.
- **filesystem** / **repowise** / **backlog** монтируют рабочую область. Дефолт —
  корень `poh-org`; сужай `filesystem.paths` / `*.mount` до нужного.
- **postgres**: `DATABASE_URI` с минимальными правами (предпочтительно read-only).
- Секреты — только в Keychain через `docker mcp secret`. Никогда не в git.

## Замечания

- `repowise` пишет индекс-кэш `.repowise/` в смонтированную папку; первый запуск
  разово шумит в stdout, дальше тихо. Per-repo — repoint `repowise.mount`.
- `backlog` читает `backlog/` из смонтированной папки (`BACKLOG_CWD=/workspace`);
  для конкретного репо repoint `backlog.mount=<repo>:/workspace`.
- Образ `mcp/poh-docker` собирается из upstream `ckreiling/mcp-server-docker`
  (клон в `images/mcp-server-docker/`, в git не вендорится).
