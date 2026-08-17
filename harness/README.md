# Harness контура производства

Весь цикл одним конфигом: **Issue → аналитика → разработка → PR → ревью**.

Три сервиса живут в отдельных репозиториях со своими релизными циклами, и каждый
умеет подниматься сам. Рвался контур не на них, а на связке: два GitHub App,
общий секрет докладов, адрес соседа, namespace Temporal — всё это до сих пор
согласовывалось руками, в четырёх местах, без единого источника правды. Здесь
связка записана один раз.

```
GitHub ──webhook──▶ caddy ──/issue/*──▶ issue-webhook ──▶ Temporal ──▶ issue-worker
                      │                                                     │
                      │                                          workflow_dispatch
                      │                                                     ▼
                      └──/pr/*──▶ pr-ingress ─▶ pr-worker         OpenHands (Actions)
                                                    │                       │
                                                    └──── /agent-event ◀─────┘
```

Стрелка снизу — то, ради чего харнесс и собран. Issue-Agent держит задачу живым
Temporal-workflow и после передачи в разработку ждёт события: `pr-open` от
прогона OpenHands, `pr-review` от PR-Agent. Пока эта стрелка не замкнута, задача
доходит до `in-development` и остаётся там навсегда.

---

## Подъём

```bash
cp .env.example .env
$EDITOR .env          # обязательное помечено ОБЯЗАТЕЛЬНО
docker compose up -d --build
curl -fsS http://localhost:8080/health
```

Образы собираются **прямо из git-репозиториев** — копия исходников рядом не
нужна, и версия сервиса не может незаметно разойтись с тем, что в `main`. Ветка
каждого сервиса задаётся переменной (`ISSUE_AGENT_REF`, `PR_AGENT_REF`): так
поднимают харнесс на доработке, не трогая compose.

Первая сборка тяжёлая: в образ воркера ставятся Node.js, GitHub CLI и Claude
Code, а образ pr-agent тянется из апстрима. Заложи **~4 ГБ памяти** билдеру.

Забытая обязательная переменная роняет `up` **сразу**, на подстановке, а не
через десять минут сборки. Это намеренно: половина контура, поднятая с забытым
секретом, выглядит работающей.

---

## Что прописать снаружи

### 1. Два GitHub App

Приложений именно два, и это не дублирование: у Issue-Agent свои права и свои
события, у PR-Agent свои. Одно на оба означало бы, что утечка ключа открывает
сразу и бэклог, и код.

| App | Permissions | Events | Webhook URL |
|---|---|---|---|
| Issue-Agent | Issues r/w, Contents r/w, Pull requests r | Issues, Issue comments, Label, Sub-issues | `<PUBLIC_URL>/issue/webhook` |
| PR-Agent | Pull requests r/w, Issues r/w, Contents r | Pull request, Issue comments, Push | `<PUBLIC_URL>/pr/webhook` |

Установи оба на репозитории из `WATCHED_REPOS`.

### 2. Секреты в каждом репозитории под контуром

Их читает прогон OpenHands в Actions — он единственная часть контура, которая
живёт не здесь, а на раннере GitHub.

| Секрет | Значение |
|---|---|
| `LLM_API_KEY` | тот же ключ z.ai, что `ZAI_API_KEY` |
| `ISSUE_AGENT_URL` | `<PUBLIC_URL>/issue` |
| `AGENT_EVENT_SECRET` | **та же строка**, что в `.env` харнесса |

```bash
repo=owner/repo
gh secret set LLM_API_KEY        --repo "$repo" --body "$ZAI_API_KEY"
gh secret set ISSUE_AGENT_URL    --repo "$repo" --body "$PUBLIC_URL/issue"
gh secret set AGENT_EVENT_SECRET --repo "$repo" --body "$AGENT_EVENT_SECRET"
```

Плюс сам workflow разработки — `.github/workflows/openhands-resolver.yml`,
`AGENTS.md` и `.openhands/task-rules.md`. Образец — в
[`mbox-checkout-service`](https://github.com/momento-box-org/mbox-checkout-service).

---

## Проверка, что контур замкнут

Порядок не случаен: каждый шаг проверяет ровно одно звено, и первый же
несработавший показывает, где рвётся.

```bash
# 1. Харнесс жив
curl -fsS "$PUBLIC_URL/health"

# 2. Приём вебхуков открыт (401 = живой эндпоинт с проверкой подписи; 404 = не тот путь)
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$PUBLIC_URL/issue/webhook"
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$PUBLIC_URL/pr/webhook"

# 3. Приём докладов открыт (401 = секрет задан; 503 = AGENT_EVENT_SECRET пуст)
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$PUBLIC_URL/issue/agent-event"

# 4. Temporal видит воркер
open "$PUBLIC_URL/temporal/"
```

**`503` на шаге 3 — самая дорогая из возможных ошибок конфигурации.** Всё
остальное работает, задачи доходят до разработки, PR открываются — и ни одна
задача не закрывается, потому что доклад о PR отвергается на входе. Симптом
неотличим от «агент разработки завис».

---

## Сквозной прогон

С `DRY_RUN=1` контур проходит все стадии, но в GitHub не пишет ничего — этим
проверяют проводку, не тратя ни токенов на комментарии, ни чужого внимания.

```
Issue заведён
  └─ триаж: advisor:* + priority:* + phase:classified
       └─ /analyze либо метка run:analyze
            └─ FNR: task → concept → debate → sysreq → validate
                 └─ ветка research/issue-N + phase:system-requirements
                      └─ ready-for-dev + чеклист готовности            ← H1
                           └─ build-me (или DEVELOP_AUTOSTART=1)
                                └─ workflow_dispatch → OpenHands в Actions
                                     ├─ edge-кейсы → SubIssue (origin:agent)
                                     └─ PR с Closes #N
                                          └─ pr-open → phase:pr-open   ← доклад
                                               └─ ревью PR-Agent
                                                    └─ pr-review       ← доклад
```

Каждая стрелка видна в Temporal UI отдельной стадией, а метка `phase:*` на Issue
показывает то же состояние тому, кто в Temporal не ходит.

---

## Границы

- **Харнесс — отладочный стенд, а не прод.** Temporal UI открыт без пароля,
  Postgres в томе рядом, TLS терминирует внешний прокси. На постоянном стенде
  закрывай UI и клади секреты в хранилище платформы, а не в `.env`.
- **Один `AGENT_EVENT_SECRET` на весь контур.** Он же в секретах репозиториев.
  Ротация — обе стороны одновременно, иначе доклады начнут отвергаться, а
  выглядеть это будет как зависший агент разработки.
- **`WATCHED_REPOS` один на обоих агентов.** Разъехавшийся охват означает PR без
  ревью либо ревью без задачи — и то, и другое замечается не сразу.
- **OpenHands в контуре не живёт.** Он на раннере GitHub Actions: своё
  окружение, свой sandboxing, свой счёт минут. Харнесс его только запускает и
  слушает доклад.
