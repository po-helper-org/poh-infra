# Состояние стенда

> Снято 2026-08-19 после отладки контура по сценарию демо.

## Что поднято прямо сейчас

Харнесс работает **на сервере**, в Dokploy. Туннель с ноутбука больше не нужен —
адрес постоянный, и вебхук репозитория настроен на него.

| Что | Где |
|---|---|
| Приём вебхуков | `http://harness.stand.example/issue/webhook` |
| Приём докладов агентов | `.../issue/agent-event` |
| Temporal UI | `.../temporal/` (basic-auth), напрямую — `ssh -L` на `127.0.0.1:8233` |
| Демо-репозиторий | https://github.com/po-helper-org/poh-demo-checkout |

**Только `http`.** По `https` на этом хосте сертификата нет, и вебхук
настроен на `http`. GitHub за редиректами вебхука не идёт: 308 на
`/issue/webhook` выглядел бы как принятая, но никуда не дошедшая доставка.

Доступ на сервер — `ssh poh-stand`. Каталог сервиса:
`/etc/dokploy/compose/compose-project/code/harness`.

## Как выложить правку Issue-Agent

Автодеплой на этот compose не настроен: образы собираются из git-контекста, и
пересобрать их надо руками.

```bash
SHA=$(git rev-parse HEAD)          # в poh-issue-agents, после push
ssh poh-stand "cd /etc/dokploy/compose/compose-project/code/harness \
  && sed -i 's|^ISSUE_AGENT_CONTEXT=.*|ISSUE_AGENT_CONTEXT=https://github.com/po-helper-org/poh-issue-agents.git#$SHA|' .env \
  && docker compose build issue-webhook issue-worker \
  && docker compose up -d issue-webhook issue-worker"
```

**Пин на ПОЛНЫЙ SHA обязателен.** BuildKit кэширует клон по URL: после нового
коммита в ту же ветку `#main` молча соберёт прежний код. Короткий SHA не годится
вовсе — `repository does not contain ref`.

## Чем опасна выкладка на живой стенд

Прогоны Temporal живут неделями, и рестарт воркера бьёт по ним тремя способами.
Каждый снаружи выглядит как «агент завис».

1. **Изменил решение воркфлоу — заведи `workflow.patched(...)`.** Ветку, которую
   прогон уже выбрал, он держит в истории. Новый код на реплее выбирает другую,
   и Temporal валит прогон: `Nondeterminism error: Activity machine does not
   handle this event`. Прогон перестаёт отвечать на сигналы вовсе. Проверка
   после выкладки — `docker logs <worker> | grep -i nondetermin`. Правка тела
   активности, ретраев и меток безопасна: их в истории нет.
2. **Долгая активность умирает вместе с воркером.** Heartbeat пропадает, и через
   `heartbeat_timeout` сервер признаёт её мёртвой. Политика ретраев применяется
   та, что записана при планировании, — свежая до идущей активности не доедет.
   Выкладывать между прогонами, а не «сейчас быстро».
3. **Контейнер агента разработки переживает своего запускателя.** `--rm`
   срабатывает только на нормальном выходе. После выкладки посмотреть
   `docker ps | grep openhands` и снять остаток; с версии от 2026-08-19 воркер
   снимает его сам по имени задачи перед новой попыткой.

## Ресурсы

На сервере ~8 ГБ памяти, из них 2.4 ГБ занимает посторонний
`temporal-postgresql`. Несколько одновременных `claude -p` плюс контейнер агента
разработки её выбирают, и стенд начинает ползти — вплоть до того, что
`docker compose build` не укладывается в десять минут. Признак: `free -m`
показывает меньше 500 МБ в `available`.

## Что прогнано вживую

Задача [#13](https://github.com/po-helper-org/poh-demo-checkout/issues/13)
прошла путь от заявки до запуска разработки без касания человека:

| Шаг | Результат |
|---|---|
| Триаж | `advisor:feature-request`, `priority:P2`, содержательный ответ комментарием |
| Аналитика | автостарт, цепочка FNR `task → concept → debate → sysreq → validate` |
| Артефакты | ветка `research/issue-13`, `sa_documentation/FNR/FNR_1/` |
| Декомпозиция | MVP (#14→#15→#16 по зависимостям), GROW (#17), SUPPORT пуст |
| Передача | `ready-for-dev` + чеклист готовности |
| Разработка | автостарт, одноразовый контейнер агента на самом стенде |

## Что осталось не сделано

**PR-Agent как self-hosted сервис нигде не развёрнут.** Ревью гоняется
CLI-образом pr-agent прямо в прогоне Actions демо-репозитория
([`pr-review.yml`](https://github.com/po-helper-org/poh-demo-checkout/blob/main/.github/workflows/pr-review.yml)):
три входа — вызов из разработки, событие `pull_request` и команда `/review` в
PR. Доклад в цикл идёт тем же `/agent-event`. Половина харнесса под PR-Agent
готова и включается профилем (`docker compose --profile pr up -d`), но требует
второго GitHub App.
