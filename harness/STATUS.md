# Состояние стенда

> Снято 2026-08-18 после сквозного прогона контура на живом репозитории.

## Что поднято прямо сейчас

Харнесс работает **локально**, на ноутбуке, вебхуки заведены через туннель.

| Что | Где |
|---|---|
| Приём вебхуков | `https://runtime-candles-slides-pro.trycloudflare.com/issue/webhook` |
| Приём докладов агентов | `https://runtime-candles-slides-pro.trycloudflare.com/issue/agent-event` |
| Temporal UI | http://localhost:8080/temporal/ (напрямую — http://localhost:8233) |
| Демо-репозиторий | https://github.com/po-helper-org/poh-demo-checkout |

Адрес туннеля живёт часы и меняется при перезапуске. За этим следит сторож
(`scratchpad/tunnel-watchdog.sh`): поднимает туннель заново и переписывает
адрес в трёх местах — `PUBLIC_URL` харнесса, секрет `ISSUE_AGENT_URL`
репозитория, `config.url` вебхука. Разошлись — прогон в Actions отработает и
молча не доложит.

Сторож проверяет **не «жив ли процесс», а «отвечает ли публичный адрес»**:
cloudflared переживает разрыв связи с edge и остаётся в памяти, продолжая
логировать неудачные переподключения. Процесс жив, туннель мёртв, GitHub
получает 530 — это случилось на прогоне, и в истории доставок видно
`issues.unlabeled → 530` рядом с успешными.

**Адрес в этом документе мог устареть.** Текущий — в
`scratchpad/tunnel-url.txt` и в настройках вебхука репозитория.

Поднять заново: `docker compose up -d` в этом каталоге. Порядок и грабли —
[`LOCAL.md`](LOCAL.md).

**Если стенд не отвечает — сначала проверь сам Docker.** `restart: unless-stopped`
поднимает упавший контейнер, но не помогает, когда останавливается демон целиком:
за ночь Docker Desktop выключился один раз, и вместе с ним исчез весь стенд.
Признак — `curl localhost:8080/health` возвращает `000`, а туннель `502`.

```bash
open -a Docker && until docker info >/dev/null 2>&1; do sleep 5; done
docker compose up -d
curl -fsS http://localhost:8080/health
```

Данные переживают перезапуск: история Temporal лежит в томе `pgdata`, и
припаркованные задачи продолжают с того места, где стояли.

## Что прогнано вживую

Задача [#1](https://github.com/po-helper-org/poh-demo-checkout/issues/1) прошла
путь целиком, без ручного вмешательства между шагами:

| Шаг | Результат |
|---|---|
| Триаж | `advisor:feature-request`, `priority:P3`, `phase:classified`, содержательный ответ комментарием |
| `/analyze` | `IssueAnalysis` в Temporal, цепочка FNR `task → concept → debate → sysreq → validate` |
| Артефакты | ветка `research/issue-1`, `sa_documentation/FNR/FNR_1/` |
| Передача | `ready-for-dev` + чеклист готовности |
| Develop | автостарт, диспатч `openhands-resolver.yml` |
| Разработка | 3 файла, тестов стало 15 вместо 8 |
| SubIssue | [#4](https://github.com/po-helper-org/poh-demo-checkout/issues/4) заведён самим агентом, `origin:agent`, отранжирован `priority:P1` |
| PR | [#6](https://github.com/po-helper-org/poh-demo-checkout/pull/6) с `Closes #1` |
| Ревью | `PR Reviewer Guide 🔍` опубликовано |
| Фаза | `phase:pr-review` — доклад дошёл и сдвинул состояние сам |

## Что осталось не сделано

**Стенд Dokploy живёт на версии до PR #61.** Его `openapi.json` отдаёт только
`/webhook`; эндпоинта `/agent-event` там нет, и автодеплой после мержа в
`main` не сработал. Пока это так, на стенде замкнут только Research: доклады
о PR и ревью приходить некуда, и задача остаётся в `in-development` навсегда.

После деплоя нужны две переменные в Environment — код их не подставит:

```
AGENT_EVENT_SECRET=<строка из scratchpad/dokploy-env.txt>
DEVELOP_AUTOSTART=1
```

Без первой эндпоинт отвечает 503. Это самая дорогая из возможных ошибок
конфигурации: всё остальное работает, PR открываются, и ни одна задача не
закрывается — симптом неотличим от зависшего агента разработки.

**PR-Agent как self-hosted сервис нигде не развёрнут.** Ревью в демо гоняется
CLI-образом pr-agent прямо в прогоне Actions
([`pr-review.yml`](https://github.com/po-helper-org/poh-demo-checkout/blob/main/.github/workflows/pr-review.yml)):
двумя входами — вызовом из разработки и по команде `/review` в PR. Доклад в
цикл идёт тот же. Половина харнесса под PR-Agent готова и включается профилем
(`docker compose --profile pr up -d`), но требует второго GitHub App.
