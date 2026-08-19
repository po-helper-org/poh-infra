# Harness — документация

Harness — один Docker Compose стек, который поднимает весь производственный
контур: Temporal, Issue-Agent, PR-Agent и общий вход через Caddy. Исходный
конфиг лежит в [`harness/`](../../harness), здесь — путеводитель по нему для
тех, кто разворачивает или администрирует стенд, а не читает `docker-compose.yml`
построчно.

## С чего начать

| Хочу... | Куда идти |
|---|---|
| Поднять харнесс на своей машине | [`harness/LOCAL.md`](../../harness/LOCAL.md) |
| Развернуть постоянный стенд на Dokploy | [`harness/DOKPLOY.md`](../../harness/DOKPLOY.md) |
| Понять, какие адреса появятся снаружи и как ими управлять | [Публичные точки входа](endpoints.md) |
| Разобраться, что писать в `.env` и почему | [Справочник конфигурации](configuration.md) |
| Понять архитектуру и сквозной цикл Issue → PR | [`harness/README.md`](../../harness/README.md) |

## Что это вообще такое

Три сервиса — Issue-Agent, PR-Agent и Temporal — живут в отдельных
репозиториях со своими релизными циклами и умеют подниматься каждый сам по
себе. Harness не заменяет их, а связывает: единый вход по HTTP, общий секрет
для обратных докладов, согласованные адреса друг друга и namespace Temporal.
Именно на этой связке — а не на самих сервисах — контур обычно и рвался,
когда её приходилось каждый раз собирать руками.

```
GitHub ──webhook──▶ caddy ──/issue/*──▶ issue-webhook ──▶ Temporal ──▶ issue-worker
                      │                                                     │
                      │                                          workflow_dispatch
                      │                                                     ▼
                      └──/pr/*──▶ pr-ingress ─▶ pr-worker         OpenHands (Actions)
                                                    │                       │
                                                    └──── /agent-event ◀─────┘
```

Issue заводится в GitHub → триаж → аналитика (FNR) → метка `ready-for-dev` →
агент разработки открывает PR → PR-Agent ревьюит → доклады о каждом шаге
возвращаются в Temporal-workflow задачи через `/agent-event`. Подробности
цикла — в [`harness/README.md`](../../harness/README.md#сквозной-прогон).

## Кто отвечает за что

| Файл | Назначение |
|---|---|
| [`harness/docker-compose.yml`](../../harness/docker-compose.yml) | Единственный источник правды: сервисы, сети, тома, метки Traefik |
| [`harness/Caddyfile`](../../harness/Caddyfile) | Маршрутизация одного публичного домена на четыре сервиса |
| [`harness/.env.example`](../../harness/.env.example) | Шаблон конфигурации с комментариями к каждой переменной |
| [`harness/README.md`](../../harness/README.md) | Архитектура, подъём, что прописать в GitHub |
| [`harness/LOCAL.md`](../../harness/LOCAL.md) | Прогон на ноутбуке через туннель |
| [`harness/DOKPLOY.md`](../../harness/DOKPLOY.md) | Постоянный стенд на Dokploy, шаг за шагом |
| [`harness/STATUS.md`](../../harness/STATUS.md) | Снимок текущего состояния конкретного развёрнутого стенда |

Эта папка (`docs/harness/`) не дублирует их, а даёт сквозной обзор двух
вопросов, которые не укладываются в один файл выше: какие URL появляются и
что означает каждая переменная окружения.
