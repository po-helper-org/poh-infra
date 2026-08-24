# Слой саморефлексии — развёртывание

Опциональный слой, который даёт агентам харнесса правила и принципы,
накопленные конкретной организацией, и принимает от них запись о каждой
итерации. Исходники — [`po-helper-org/harness-memory-base`](https://github.com/po-helper-org/harness-memory-base).

Здесь только развёртывание рядом с контуром. Что это такое и зачем —
[`docs/harness/self-reflection.md`](../docs/harness/self-reflection.md).

## Запуск

```bash
docker network create harness-memory-net     # один раз
cp .env.example .env && $EDITOR .env         # заполнить MEMORY_BASE_TOKEN
docker compose up -d --build
```

Проверка:

```bash
docker compose exec memory-api python -c \
  "import httpx; print(httpx.get('http://127.0.0.1:8090/health').json())"
```

Ожидается `{"status": "ok", "rules": 25, ...}`.

## Включение в контуре

В `harness/.env` — две переменные, и агенты начинают спрашивать слой:

```bash
MEMORY_BASE_URL=http://memory-api:8090
MEMORY_BASE_TOKEN=<тот же токен, что в memory/.env>
```

Затем `docker compose up -d issue-webhook issue-worker` в `harness/`.

Пустой `MEMORY_BASE_URL` выключает слой целиком — это умолчание, и контур без
него работает ровно как раньше.

## Проход рефлексии

Постоянного процесса не требует. Запускается расписанием:

```cron
# Раз в сутки, в тихое окно. Разведено по времени с работой агентов: ключ
# модели один на весь контур, и одновременные потребители выбивают
# ограничение частоты.
17 4 * * *  cd /opt/harness-memory && /usr/bin/docker compose --profile reflect run --rm memory-reflector >> /var/log/harness-memory-reflect.log 2>&1
```

## Что смотреть

```bash
curl -H "Authorization: Bearer $MEMORY_BASE_TOKEN" http://memory-api:8090/stats
```

| Величина | О чём говорит, когда портится |
|---|---|
| `verdict_coverage` | падает — проход рефлексии не отрабатывает |
| `episodes_without_verdict` | растёт без остановки — созревание не наступает, скорее всего не задан `MEMORY_GITHUB_TOKEN` |
| `lessons_by_status` | всё в `proposed` и ничего в `active` — человек не переносит кандидатов, петля разомкнута |

## Расход

| Контейнер | Лимит | Фактически |
|---|---|---|
| `memory-api` | 256 МБ | ~50 МБ |
| `memory-reflector` | 512 МБ | только на время прохода |

Лимиты обязательны: на стенде swap выключен, свободно порядка 300 МБ и рядом
живут чужие боевые сервисы.
