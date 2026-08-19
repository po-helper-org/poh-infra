# Развёртывание и первичная индексация

Порядок продуман так, чтобы каждый шаг проверялся до следующего: сервис
поднимается снизу вверх, и неудача видна там, где произошла, а не в виде
молчащего агента через сутки.

## Перед началом

**Первичная индексация идёт вне контура производства.** Это решение, а не
рекомендация: она клонирует четырнадцать репозиториев с историей и строит по
ним графы — самая тяжёлая нагрузка, которую этот сервис создаёт. На сервере
контура свободной памяти под неё нет. Выкладка туда — отдельная задача после
решения по памяти.

Нужно заранее:

| Что | Зачем |
|---|---|
| Токен GitHub с чтением репозиториев организации | клонирование приватных |
| Ключ провайдера модели (необязательно) | проза страниц вики |
| Ключ провайдера эмбеддингов (необязательно) | семантический поиск |
| Домен с A-записью на хост | веб-админка |
| ~20 ГБ диска | клоны с историей и индексы |

## Шаг 1. Сети и конфигурация

```bash
docker network create poh-repowise-net
docker network create dokploy-network   # локально, если Traefik не поднят

cd repowise
cp .env.example .env && $EDITOR .env
```

Обязательны к заполнению:

```bash
REPOWISE_AGENT_TOKEN=$(openssl rand -hex 24)   # тот же уйдёт агентам контура
REPOWISE_API_KEY=$(openssl rand -hex 24)       # guard самого repowise serve
REPOWISE_GIT_TOKEN=...                          # чтение репозиториев
REPOWISE_PUBLIC_HOST=repowise.example.com
```

**Про `REPOWISE_PUBLIC_HOST`.** Имя `REPOWISE_HOST` занято самим пакетом под
адрес привязки сокета: `repowise mcp --host` читает его по умолчанию. Домен,
положенный в него, заставит эндпоинт слушать на несуществующем адресе, и
сервис молча не поднимется.

## Шаг 2. Учётка веб-админки

Отдельным файлом, а **не** в `.env`: панель Dokploy переписывает `.env` на
каждом деплое, и учётка, заведённая мимо панели, пропала бы молча — вместе со
всем входом.

```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'ваш-пароль'
```

Полученный хэш записать с **удвоенными долларами**:

```bash
# локально: ./caddy.env    на стенде: /etc/poh-repowise/web-auth.env (0600)
REPOWISE_WEB_USER=ops
REPOWISE_WEB_PASSWORD_HASH=$$2a$$14$$xxxxxxxxxxxxxxxxxxxxxx
```

`docker compose` интерполирует значения любого env-файла. Без удвоения хэш
доедет до контейнера обрезанным и будет отвергать верный пароль при внешне
исправном контейнере — отказ, который стоит часа отладки.

**Проверить хэш до записи:**

```bash
docker compose exec caddy sh -c 'echo -n "$REPOWISE_WEB_PASSWORD_HASH" | wc -c'
```

Должно быть 60. Меньше — доллары не удвоены.

**Пароль не должен совпадать** с паролем Temporal UI контура: за этим входом
клоны всех репозиториев организации и ключ модели.

## Шаг 3. Сборка

```bash
docker compose build
```

## Шаг 4. Первичная индексация

Разовый прогон, отдельным контейнером:

```bash
docker compose run --rm indexer python /app/indexer.py bootstrap
```

Что происходит и в каком порядке:

1. клонирование каждого репозитория из списков состава (`--filter=blob:none` —
   история для blame нужна, содержимое прошлых ревизий нет);
2. удаление `.env`, `*.pem`, `secrets/` из рабочих копий;
3. структурный индекс первого репозитория **без модели** (`init --no-prose`);
4. регистрация остальных в workspace;
5. отдельным шагом — проза моделью, если задан `REPOWISE_PROVIDER`.

Шаг 5 отделён намеренно: он единственный, который может не состояться
(провайдер, лимиты, эмбеддер), и его неудача не должна отменять шаги 1–4.
После неудачи на руках работоспособный структурный индекс, а не половина.

Время: на четырнадцати репозиториях структурная часть — минуты, проза моделью —
часы. Генерация шести страниц по репозиторию из 14 файлов заняла 251 с.

## Шаг 5. Запуск

```bash
docker compose up -d
docker compose ps
```

## Проверочный список

```bash
# 1. Индекс собран и видит все репозитории
docker compose exec repowise-contour-mcp repowise workspace list

# 2. Прокси жив (без токена — так и задумано)
curl -fsS http://localhost:7400/health

# 3. Без токена не пускает
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  'http://localhost:7400/mcp?workspace=contour&session=t'      # 401

# 4. Без сессии не пускает
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: Bearer $REPOWISE_AGENT_TOKEN" \
  'http://localhost:7400/mcp?workspace=contour'                # 400

# 5. MCP-эндпоинт снаружи недоступен — граница держится
curl -s -m 3 -o /dev/null -w '%{http_code}\n' http://localhost:7338/mcp  # 000

# 6. Админка требует пароль
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/          # 401

# 7. Возраст индекса виден
curl -s -H "Authorization: Bearer $REPOWISE_AGENT_TOKEN" \
  http://localhost:7400/index-age

# 8. Секретов в индексе нет
docker compose exec repowise-contour-mcp \
  repowise search "ZAI_API_KEY" --limit 5
```

Пункты 3–6 — не формальность: каждый из них проверяет границу, тихий отказ
которой означает открытый доступ к коду организации.

## Шаг 6. Подключение агентов контура

В `.env` харнесса (`harness/.env`):

```bash
REPOWISE_PROXY_URL=http://repowise-proxy:7400
REPOWISE_AGENT_TOKEN=<тот же, что в сервисе>
REPOWISE_CONTOUR_REPOS=po-helper-org/poh-issue-agents,po-helper-org/poh-infra,...
REPOWISE_NETWORK=poh-repowise-net
```

И подключить сервисы харнесса к сети `poh-repowise-net` — иначе воркер не
достучится до прокси, и стадия будет штатно деградировать, не сообщая, что
причина в сети.

Проверка со стороны контура:

```bash
docker compose exec issue-worker \
  python -c "from shared import repowise; print('доступен:', repowise.available())"
```

## Откат

```bash
docker compose down                # сервис погашен, агенты деградируют штатно
docker compose down -v             # плюс удаление индекса и журналов
```

Со стороны контура достаточно очистить `REPOWISE_PROXY_URL`: стадия перейдёт в
деградацию, конвейер продолжит работать как до внедрения.
