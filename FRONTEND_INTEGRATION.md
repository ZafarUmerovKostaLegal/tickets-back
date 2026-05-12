# Подключение фронтенда к бэкенду (tickets-back)

Документ для команды фронтенда: как ходить в API при локальной или docker-сборке монорепозитория **tickets-back**.

## Точка входа: API Gateway

Все запросы от браузера направляйте на **gateway**, а не на отдельные микросервисы напрямую.

| Окружение | Базовый URL API (пример) |
|-----------|---------------------------|
| Docker Compose по умолчанию | `http://localhost:1234` |
| Другой порт | Значение переменной `GATEWAY_PORT` в `.env` (см. `docker-compose.yml`) |

Переменная для фронта (пример имени): `VITE_API_BASE_URL` / `NEXT_PUBLIC_API_URL` = **`http://localhost:1234`** (без завершающего `/`).

Проверка живости шлюза: `GET {base}/live`  
Проверка доступности todos через шлюз: `GET {base}/health/todos`

## Авторизация

Микросервисы (в том числе **todos**) ожидают заголовок **`Authorization: Bearer <access_token>`**.

Шлюз дополнительно:

- принимает тот же **Bearer** от клиента;
- либо подставляет токен из **cookie сессии** (по умолчанию имя `kl_access_token`), если заголовка `Authorization` нет.

Итого для SPA обычно достаточно одного из вариантов:

1. После логина сохранять токен и на каждый запрос ставить `Authorization: Bearer …`.
2. Либо полагаться на **cookie** `kl_access_token`, если бэкенд выставляет её при логине и фронт ходит на тот же origin (или настроены куки/credentials под ваш домен).

Эндпоинты авторизации проходят через шлюз под префиксом **`/api/v1/auth/...`** (используйте текущие маршруты вашего фронта и OpenAPI, если он включён в стенде).

**Важно:** при запросах с `credentials: 'include'` убедитесь, что CORS на gateway соответствует вашему origin (в Compose по умолчанию учитывается `FRONTEND_URL`, например `http://localhost:5173`).

## Префиксы API (через gateway)

Все пути ниже **относительно базы** `{base}` (например `http://localhost:1234`).

| Модуль | Префикс |
|--------|---------|
| Auth | `/api/v1/auth/...` |
| Задачи / тикеты | `/api/v1/tickets/...` (и связанные маршруты шлюза) |
| **Todo / Kanban** | **`/api/v1/todos/...`** |
| Time tracking | `/api/v1/time-tracking/...` |
| Расходы | `/api/v1/expenses/...` |
| Медиа (файлы с диска gateway) | `/api/v1/media/...` |

Прокси на микросервисы настроен в `gateway/presentation/routes/*`; для todos используется catch-all `GET/POST/... /api/v1/todos/{path}`.

## Модуль Todo: что вызывать

### Через шлюз (рекомендуется)

- База: `{base}/api/v1/todos`

### Легаси одна доска на пользователя

- `GET /api/v1/todos/board` — полная доска (колонки, карточки, лейблы, фон).
- Мутации: `/api/v1/todos/board/columns`, `/api/v1/todos/board/cards/...` и т.д. (как в текущем фронте).

### Несколько досок, участники, приглашения

**Одна доска = изолированный канбан.** У каждой доски свой `board_id`, свой **`background_url`**, свои колонки, карточки и лейблы. Данные чужих досок по API не смешиваются.

При **создании** доски (`POST /boards` или первая автосозданная через `GET /board` / `ensure_board`) на бэкенде **автоматически** добавляются **три пустые колонки** (и только они), без карточек:

1. «Сегодня»  
2. «На этой неделе»  
3. «Позже»  

Фон задаётся отдельно через `PATCH` доски (`backgroundUrl` / `background_url`). Значение по умолчанию для **названия** новой доски в `POST /boards`, если не передать `title`, — **«Новая доска»**; первая автосозданная личная доска при отсутствии досок — **«Моя доска»**.

- `GET /api/v1/todos/boards` — список досок (`items`, роль `my_role`, `is_current`).
- `GET /api/v1/todos/boards/current` — полное тело доски для «текущей» личной доски (аналогично выбору primary).
- `POST /api/v1/todos/boards` — создание (тело: опционально `title` (по умолчанию «Новая доска»), `visibility`, опционально `memberUserIds`, `instantAddMembers`).
- `GET|PATCH|DELETE /api/v1/todos/boards/{boardId}` — просмотр / изменение метаданных и фона / архивация.
- Канбан под конкретной доской: пути вида **`/api/v1/todos/boards/{boardId}/columns/...`**, **`.../cards/...`** (зеркально легаси-структуры под `/board/...`).

Приглашения:

- `GET /api/v1/todos/invites` — входящие.
- `POST /api/v1/todos/invites/{id}/accept` | `decline` | `revoke`.
- `GET|POST /api/v1/todos/boards/{boardId}/invites` — исходящие по доске.

Ответ полной доски **`BoardOut`** дополнительно содержит поля **`title`**, **`visibility`**, **`color`** (кроме прежних `id`, `user_id`, `background_url`, колонок и лейблов).

### Вложения карточек и URL медиа

В JSON карточек приходит относительный путь вида **`/api/v1/media/{storage_key}`**. Подставляйте к нему тот же **`{base}`**, что и для API, и передавайте авторизацию (или cookie), т.к. выдача файла идёт через **gateway** (`gateway/presentation/routes/media.py`).

## Локальная разработка без Docker

Если поднимаете сервисы по отдельности, задайте фронту URL того хоста, где реально слушает **gateway** (или dev-proxy), и тот же список префиксов `/api/v1/...`. Прямые URL вида `http://todos:1240` из браузера не используются — это внутренняя сеть Docker.

## Переменные окружения (справочно)

Из `docker-compose.yml` для полной стыковки стека:

- `GATEWAY_PORT`, `GATEWAY_BASE_URL`
- `FRONTEND_URL` — origin SPA для CORS/редиректов
- `JWT_SECRET` — общий для gateway и auth
- `TODOS_SERVICE_URL` — **только на gateway** (внутри Docker обычно `http://todos:1240`)

## Где смотреть код контрактов

- Прокси todos: `gateway/presentation/routes/todos_routes.py`
- Роуты сервиса todos: `todos/presentation/routes/board_routes.py`, `todos/presentation/routes/boards_multi_routes.py`
- Схемы ответа доски: `todos/presentation/board_payload.py`
- User id в todos: `todos/presentation/dependencies.py` (валидация через `auth` `/users/me`)

При смене версии API синхронизируйте типы на фронте с этими модулями или сгенерированной OpenAPI, если она включена на вашем стенде.

## Если `POST /boards` возвращает 409 «Could not create board»

Чаще всего в БД осталось ограничение **одна строка todo_boards на один `user_id`**. Перезапустите контейнер/процесс **todos** — при старте выполняется патч `apply_todo_boards_multi_user_patch` (снимает UNIQUE с `user_id`).

Если ошибка сохраняется, в ответе 409 теперь есть поля **`hint`** и **`postgres`** (текст из PostgreSQL) — по ним видна точная причина. Вручную в PostgreSQL можно выполнить:

```sql
-- список уникальных ограничений на todo_boards
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.todo_boards'::regclass AND contype = 'u';
```

После снятия лишнего `UNIQUE (user_id)` создание второй и следующих досок должно проходить.
