# Фронт: инструкция по доработкам (positions, TT, отпуска, чат)

Документ для `tickets-front` после изменений на бэкенде (2026). Описывает, что
проверить/допилить на фронте. Часть пунктов **уже внесена** в репозиторий — см.
раздел «Статус» у каждого блока.

---

## 0. Общий принцип — `permissions` из API

После логина в **`GET /api/v1/users/me`** приходит объект **`permissions`**
(версия `v: 1`). **Не дублируйте** на фронте полные матрицы RBAC по org-роли —
опирайтесь на флаги.

После смены **должности** или org-роли пользователю нужен **re-login** или
`refreshCurrentUser()`, иначе `permissions` устареют.

Тип на фронте сейчас `Record<string, unknown>` — имеет смысл завести явный
`UserUiPermissions` с полями ниже (см. чеклист).

---

## 1. Каталог коллег (чат, контакты, выбор участников)

### Проблема

`GET /api/v1/users` и `GET /api/v1/time-tracking/users` для роли **«Сотрудник»**
**не отдают** полный список коллег (403 или только «я сам»). Из-за этого в чате
не было имён и списка участников.

### Решение на бэкенде

| Эндпоинт | Назначение |
| -------- | ---------- |
| `GET /api/v1/contacts/colleagues` | TT + Auth, объединённый каталог для всех авторизованных |
| `GET /api/v1/users/colleagues` | Только Auth (public projection), для всех авторизованных |
| `GET /api/v1/users/public?ids=1,2,3` | Батч имён/аватаров по id (до 200 за запрос) |

### Что сделать на фронте

1. **Для списка коллег** (чат, контакты, модалки «выбрать участника») использовать
   **`listContactsColleagues()`** → `GET /api/v1/contacts/colleagues`.
   **Не** `getUsers()` и **не** `listTimeTrackingUsers()` как единственный источник.

2. **Для имён авторов** сообщений/комментов, если id нет в локальном каталоге —
   **`getUsersPublic(ids)`** → `GET /api/v1/users/public?ids=...`.

3. **`getUsers()`** оставить только там, где нужен **админский** каталог
   (`permissions.can_view_user_directory === true`): панель администратора,
   инвентарь и т.п.

### Статус

| Место | Статус |
| ----- | ------ |
| `KostaDailyPage` — список участников | ✅ `listContactsColleagues` |
| `useKostaDailyChat` — имена в сообщениях | ✅ `getUsersPublic` для неизвестных id |
| `ContactsPage` | ✅ уже через `listContactsColleagues` |
| `VacationAddEmployeeModal` | ⚠️ ещё `getUsers(false)` — заменить на `listContactsColleagues` |
| `TodoBoardMembersModal`, `TodoBoardsBar`, `TodoPage` | ⚠️ `getUsers` — заменить на colleagues / public |
| `ProjectMembersField`, `ProjectDetailPage` | ⚠️ проверить: для «Сотрудника» может не хватать имён |
| `VacationScheduleGrid` (построение строк) | ⚠️ `getUsers(true).catch([])` — для обычного сотрудника пусто; после `sync` графика можно опираться на `listVacationScheduleEmployees` + colleagues |

### Пример (entities уже есть)

```ts
import { listContactsColleagues } from '@entities/contacts';
import { getUsersPublic } from '@entities/user';

const colleagues = await listContactsColleagues();
const { items } = await getUsersPublic([42, 57], true);
```

---

## 2. Справочник должностей (`position`)

### API

```
GET /api/v1/positions
```

Ответ:

```json
{
  "positions": [
    "Business Development Manager",
    "Contracts and BD Assistant",
    "Accountant",
    "Office Manager"
  ]
}
```

### Что сделать

1. В карточке пользователя (админка) — **выпадающий список** из `/positions`,
   не хардкод.
2. Значение сохранять в поле **`position`** как строку **без изменений** регистра.
3. **`role`** (org RBAC) и **`position`** (должность) — **разные** селекторы.
4. Если у пользователя должность не из списка — показать текущее значение в
   select, чтобы форма не затёрла его.

### Статус

| Место | Статус |
| ----- | ------ |
| `useAdminUsers` + `getPositions` | проверить в вашей ветке |
| Admin `UserCard` / `UserRow` | проверить селектор должности |

---

## 3. Time Tracking — права по должности

Для должностей **Business Development Manager**, **Contracts and BD Assistant**,
**Accountant** (org-роль может оставаться **«Сотрудник»**):

| Можно | Нельзя |
| ----- | ------ |
| Клиенты, проекты, пользователи TT, billable-ставки, записи времени | Вкладка/раздел **«Отчётность»** (`/reports/*`) |
| Счета, дашборд проекта, team workload | Cost rates (только admin/partner) |

### Флаги в `permissions`

| Флаг | Значение для этих должностей |
| ---- | ---------------------------- |
| `time_tracking_can_view_reports` | **`false`** — скрыть отчётность |
| `time_tracking_can_manage_org_users` | `true` |
| `time_tracking_can_view_time_entries_scope` | `true` |
| `time_tracking_can_manage_time_entries_scope` | `true` |
| `hourly_rates_can_manage` | `true` |
| `hourly_rates_admin_only_operations` | `false` |

### Что сделать

1. Скрывать вкладку **Reports** и роуты превью отчётов, если
   `permissions.time_tracking_can_view_reports === false`.
2. Кнопки управления TT (пользователи, ставки, проекты) показывать по
   `time_tracking_can_manage_org_users` / `hourly_rates_can_manage`, а не только
   по org-роли.
3. Не блокировать TT целиком — у пользователя может быть `time_tracking_role:
   manager` при org-роли «Сотрудник».

### Статус

| Место | Статус |
| ----- | ------ |
| `timeTrackingAccess.canViewTimeTrackingReports` | ✅ |
| `getVisibleTimeTrackingTabs` — фильтр `reports` | ✅ |
| Прямые ссылки `/time-tracking/reports/...` | ⚠️ добавить guard по permissions |
| UI ставок / пользователей TT для BDM и т.д. | ⚠️ проверить, что не завязано только на org-роль |

### Смена ставки с даты

Уже на бэке и частично на фронте:

```
POST /api/v1/time-tracking/users/{authUserId}/hourly-rates/change-from
```

Тело (camelCase или snake_case):

```json
{
  "projectId": "uuid",
  "effectiveFrom": "2026-06-01",
  "newAmount": "150.00",
  "currency": "USD"
}
```

На фронте: `changeHourlyRateFrom` в `@entities/time-tracking` (используется в
`UserEditPage`).

---

## 4. График отпусков

### Активация сотрудников (серые строки «Не в графике»)

Строки с **`systemOnly`** / серым текстом — пользователь есть в auth, но **нет
привязки** к `schedule_employees` за год (`auth_user_id`).

**Бэкенд:**

```
POST /api/v1/vacations/schedule/employees/sync?year=2026
```

Привязывает всех auth-пользователей к графику (создаёт строки или линкует
старый Excel по ФИО/email).

**Фронт:**

- При открытии графика менеджером вызывать **`syncVacationScheduleEmployees(year)`**
  (уже в `VacationScheduleGrid`).
- Строки без `auth_user_id` в API по умолчанию **не отдаются** (`only_registered=true`);
  после sync все активные сотрудники должны появиться как редактируемые.

### Редактирование дней

Даже для активных строк нужен **режим редактирования** (кнопка на панели
графика). Без него ячейки read-only.

### Права на график по должности

Те же три должности (BDM, Contracts and BD Assistant, Accountant) получают
**`vacation_can_manage_schedule: true`** при org-роли «Сотрудник».

```ts
// vacationScheduleAccess.ts
permissions.vacation_can_manage_schedule === true
```

### Ручные записи с документами

```
POST /api/v1/vacations/schedule/manual-entries   (multipart)
GET  /api/v1/vacations/schedule/manual-entries?year=&employeeId=
```

Обязательны файлы-основания для категорий из `kind-legend` (командировка,
больничный и т.д.). API и модалка `VacationManualEntryModal` — проверить
интеграцию в вашей ветке.

### Заявки на отсутствие

Без изменений контракта: `leave-requests`, `partners`, approve/decline. После
approve дни попадают в график автоматически.

### Статус

| Место | Статус |
| ----- | ------ |
| `syncVacationScheduleEmployees` + auto-sync при загрузке | ✅ |
| `canEditVacationSchedule` ← `permissions` | ✅ |
| `VacationManualEntryModal` | проверить в ветке |
| Скрытие отчётности TT для тех же должностей | см. §3 |

---

## 5. Чеклист для фронта

### API / entities

- [ ] Типизировать `UserUiPermissions` (ключи из §3–4 + `can_view_user_directory`, …).
- [ ] Везде, где нужен **каталог коллег**, — `listContactsColleagues`, не `getUsers`.
- [ ] Для **имён по id** — `getUsersPublic` / `ensurePublicUsersLoaded`.
- [ ] `GET /api/v1/positions` в админке для поля `position`.

### Time Tracking

- [ ] Скрыть «Отчётность» при `time_tracking_can_view_reports === false`.
- [ ] Guard на deep-link `/time-tracking/.../reports/...`.
- [ ] Операции TT для BDM/Accountant — по `permissions`, не по org-роли.

### Отпуска

- [ ] Sync графика при загрузке (или кнопка «Обновить состав» для админов).
- [ ] Редактирование — явный режим + `vacation_can_manage_schedule`.
- [ ] Ручные записи + загрузка документов.

### Чат

- [ ] Список участников — `listContactsColleagues`.
- [ ] Имена в ленте — каталог + `getUsersPublic` для пропущенных id.
- [ ] Создание DM/группы — выбор из colleagues, не из `getUsers`.

### Todo / прочее

- [ ] `TodoBoardMembersModal` — colleagues вместо `getUsers`.
- [ ] Любые @-упоминания — colleagues или public batch.

---

## 6. Быстрая проверка под «Сотрудником»

1. **Чат** — видны все коллеги, в сообщениях реальные ФИО, не «Пользователь 123».
2. **Контакты** — список коллег загружается без 403.
3. **TT** (BDM / Accountant с `time_tracking_role: manager`) — проекты и ставки
   доступны, вкладки «Отчётность» нет.
4. **Отпуска** (office manager / BDM с `vacation_can_manage_schedule`) — строки
   не серые, режим редактирования работает.
5. **`GET /users/me`** — после назначения должности в permissions ожидаемые флаги.

---

## 7. Деплой

Сначала выкатить бэкенд (**auth**, **gateway**, **contacts**, **vacation**,
**time_tracking**), затем фронт. Иначе `/users/colleagues` и sync отпусков
вернут 404/503.
