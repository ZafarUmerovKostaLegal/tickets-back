# Подключение фронтенда к бэкенду (Gateway)

Документ описывает, как SPA или другой клиент должен обращаться к API **через сервис Gateway**, а не напрямую к микросервисам.

## Базовый URL

Все запросы с фронта идут на хост, где развёрнут Gateway, с префиксом версии:

`{GATEWAY_BASE}/api/v1/...`

Примеры для локальной разработки (уточните порт в своём `docker-compose` или `main.py`):

- `http://localhost:<порт-gateway>/api/v1/...`

Продакшен: домен и HTTPS задаётся инфраструктурой; переменная `GATEWAY_BASE_URL` в настройках Gateway используется для абсолютных ссылок и интеграций.

Фронтенд **не** должен ходить напрямую на `time_tracking:1241`, `auth:1236` и т.д. — только на Gateway (кроме отладки).

## Аутентификация

### Заголовок `Authorization`

Для вызовов API передавайте JWT:

```http
Authorization: Bearer <access_token>
```

Токен можно хранить во фронте так, как принято в приложении (например `localStorage` после callback — см. ниже).

### Cookie-сессия (опционально)

Если на Gateway включена выдача cookie с токеном (`AUTH_SET_SESSION_COOKIE` и связанные настройки), middleware подставляет `Authorization: Bearer ...` из cookie с именем по умолчанию `kl_access_token`, даже если фронт не прислал заголовок. Имеет смысл при `fetch`/`axios` указывать `credentials: 'include'`, когда работаете с cookie.

### Вход через Azure

Точка входа для браузера (редирект, не XHR):

- `GET {GATEWAY_BASE}/api/v1/auth/azure/login?target=main`  
- для админки: `target=admin`

После IdP пользователь попадает на callback. Gateway отдаёт страницу **`GET /auth/callback`**, которая забирает `access_token` из hash/query и может положить его в `localStorage` под ключом `access_token`, затем редирект на `{FRONTEND_URL}/home`.

Для этого в окружении Gateway должен быть задан **`FRONTEND_URL`** (URL вашего SPA).

Проверка текущего пользователя (через Gateway к auth):

- `GET /api/v1/users/me`  
  с заголовком `Authorization: Bearer ...`.

### Выход

- `POST /api/v1/auth/azure/session/logout` — с передачей того же `Authorization` и при использовании cookie — `Cookie`, если нужно сбросить серверную сессию.

## CORS

В Gateway настроен `CORSMiddleware` с **`allow_credentials: true`**.

- В **`FRONTEND_URL`** и при необходимости **`ADMIN_FRONTEND_URL`** (переменные окружения Gateway) перечисляются разрешённые origin’ы (можно несколько через запятую).
- Дополнительно зашиты типичные локальные origin’ы (`localhost:5173`, `8080`, …).
- При `CORS_ALLOW_PRIVATE_NETWORK` можно разрешить частные IP по regex (см. код gateway).

Фронт при кросс-доменных запросах с cookie должен отправлять `credentials: 'include'`.

## Основные группы маршрутов (поверхность для UI)

Все пути ниже относительно `{GATEWAY_BASE}/api/v1`.

| Область | Префикс | Комментарий |
|--------|---------|-------------|
| Пользователи, профиль | `/users` | `GET /users/me`, прочие CRUD по политике gateway |
| Учёт времени (основной) | `/time-tracking` | Клиенты, проекты, записи времени, отчёты, счета, снимки отчётов |
| Учёт времени (алиас) | `/users` | Часть маршрутов дублирована под `/users/{id}/time-entries`, `hourly-rates`, `project-access`, `time-entry-edit-unlock` — используйте **один** стиль путей в приложении, чтобы не путаться |
| Задачи | `/tickets` | |
| Расходы | `/` (часть путей под тем же `/api/v1`) | Например `/expenses/...`, `/expense-types`, `/projects` — см. `expenses_routes` |
| Уведомления | `/notifications` | |
| Инвентарь | `/inventory` | |
| Заметки / todos | `/todos` | |
| График звонков | `/call-schedule` | |
| Медиа | `/media` | |
| Посещаемость | `/attendance` | |
| Отпуска | `/vacations` | |
| Роли | `/roles` | |

OpenAPI у Gateway в ответе может быть отключён (`docs_url=None`). Контракты уточняйте по коду роутеров в `gateway/presentation/routes/` и по сервисам-источникам.

## Учёт времени: что часто нужно фронту

Базовый префикс: **`/time-tracking`**.

Примеры (не исчерпывающий список):

- Отчёты, мета, выгрузки: `/time-tracking/reports/...`
- Снимки отчётов (редактирование строк):  
  `GET/PATCH /time-tracking/reports/snapshots/...`
- Подтверждение отчётов партнёрами:  
  - `POST /time-tracking/reports/partner-confirmations/submit`  
  - `POST /time-tracking/reports/partner-confirmations/{requestId}/confirm`  
  - `GET /time-tracking/reports/partner-confirmations/pending`  
  - `GET /time-tracking/reports/partner-confirmations/confirmed`
- Разблокировка внесения времени на день (менеджер/админ):  
  `POST /time-tracking/users/{authUserId}/time-entry-edit-unlock`  
  Альтернатива с тем же телом: `POST /users/{authUserId}/time-entry-edit-unlock`

Тела запросов и ответы по возможности используйте в **camelCase** (`workDate`, `snapshotId`, …), как в схемах FastAPI/Pydantic у сервисов.

## Ошибки и заголовки

- Ответы об ошибках обычно в формате JSON с полем `detail` (строка или список).  
- Для трассировки запросов смотрите заголовки ответа Gateway (например request id в middleware), если они включены в вашей сборке.

## Переменные окружения (кратко, со стороны ops)

Имеет смысл согласовать с DevOps:

- URL сервисов: `AUTH_SERVICE_URL`, `TIME_TRACKING_SERVICE_URL`, …  
- `FRONTEND_URL`, `ADMIN_FRONTEND_URL`, `GATEWAY_BASE_URL`  
- Cookie/JWT: `AUTH_SESSION_COOKIE_NAME`, `AUTH_SET_SESSION_COOKIE`, `AUTH_SESSION_COOKIE_SECURE`, …

Фронту для интеграции достаточно знать **публичный URL Gateway** и правила **Bearer / callback / CORS** из разделов выше.
