# Локальный прогон контура

Полный цикл на ноутбуке: harness в Docker, вебхуки GitHub — через туннель.
Смысл прогона — убедиться, что контур замкнут, до того как его несут на стенд.

## 1. Поднять харнесс

```bash
cd poh-infra/harness
cp .env.example .env
$EDITOR .env            # обязательное помечено ОБЯЗАТЕЛЬНО
docker compose up -d --build
curl -fsS http://localhost:8080/health
```

Первая сборка тяжёлая (~1.6 ГБ образ воркера: Node.js, GitHub CLI, Claude Code).
PR-половина вынесена в профиль: `docker compose --profile pr up -d` — она требует
второго GitHub App и без него не поднимается.

Локально Issue-Agent ходит в GitHub по `GH_TOKEN`, а не как App: App-у нужен
приватный ключ, которого на ноутбуке быть не должно.

## 2. Туннель

```bash
cloudflared tunnel --url http://localhost:8080
# https://<случайное-имя>.trycloudflare.com
```

Адрес меняется при каждом перезапуске туннеля. Поэтому он живёт в двух местах, и
оба надо обновлять вместе:

```bash
TUNNEL=https://<...>.trycloudflare.com

# в .env харнесса — Temporal UI берёт отсюда CORS
sed -i '' "s|^PUBLIC_URL=.*|PUBLIC_URL=$TUNNEL|" .env
docker compose up -d --force-recreate issue-webhook

# в секретах репозитория — по нему прогон в Actions докладывает о PR
gh secret set ISSUE_AGENT_URL --repo <owner/repo> --body "$TUNNEL/issue"
```

## 3. Вебхук репозитория

Локально это **вебхук репозитория**, а не GitHub App: URL App меняется только
через его настройки и требует приватного ключа, а туннельный адрес живёт часы.

```bash
gh api -X POST /repos/<owner/repo>/hooks --input - <<JSON
{"name":"web","active":true,
 "events":["issues","issue_comment","label","pull_request","sub_issues"],
 "config":{"url":"$TUNNEL/issue/webhook","content_type":"json",
           "secret":"<ISSUE_APP_WEBHOOK_SECRET из .env>","insecure_ssl":"0"}}
JSON
```

Проверка доставки — со стороны GitHub, а не по логам:

```bash
gh api /repos/<owner/repo>/hooks/<id>/deliveries \
  --jq '.[0:5][] | "\(.delivered_at) \(.event).\(.action) → \(.status_code)"'
```

## 4. Секреты репозитория

Их читает прогон OpenHands и ревью — единственные части контура, которые живут не
здесь, а на раннере GitHub.

| Секрет | Значение |
|---|---|
| `LLM_API_KEY` | ключ z.ai |
| `ISSUE_AGENT_URL` | `<TUNNEL>/issue` |
| `AGENT_EVENT_SECRET` | **та же строка**, что в `.env` харнесса |

## 5. Наблюдение за прогоном

```bash
# состояние воркфлоу — что выполняется прямо сейчас
docker exec harness-temporal-1 temporal workflow list --address temporal:7233
docker exec harness-temporal-1 temporal workflow describe --address temporal:7233 \
  --workflow-id "issue-<owner/repo>-<N>"

# лог стадий
docker compose logs -f issue-worker

# UI
open http://localhost:8080/temporal/
```

`Pending Activities` в `describe` — самое полезное место: там видно, на какой
именно стадии стоит задача и сколько попыток потрачено.

## Грабли

- **`/agent-event` отвечает 503.** `AGENT_EVENT_SECRET` пуст. Всё остальное при
  этом работает, PR открываются, и ни одна задача не закрывается — симптом
  неотличим от зависшего агента разработки.
- **Туннель перезапустили, секрет не обновили.** Прогон в Actions отработает и
  молча не доложит: цикл останется ждать в `in-development`.
- **`DRY_RUN=1` при живом прогоне.** Стадии проходят, в Issue не появляется
  ничего. Смотреть надо не в GitHub, а в лог: строки `[DRY_RUN]`.
- **Два обработчика на одном репозитории.** Если на нём же стоит прод-App,
  задачу разберут оба — с разными Temporal и разными комментариями. Для локального
  прогона бери репозиторий, на который прод-App не установлен.
