# Фронт: график отпусков (заявки + ручные записи с основаниями)

Инструкция для фронтенда по переработанному модулю отпусков. Всё ходит через
gateway (`/api/v1/vacations/...`) с Bearer-токеном.

Две части:
1. **Заявки сотрудников** — сотрудник подаёт заявление, партнёр подтверждает/отклоняет (уже было, ниже — напоминание).
2. **Ручные записи в график** — менеджер вносит данные вручную и **обязан приложить документ-основание** на период (новое).

---

## 0. Категории графика (легенда)

```
GET /api/v1/vacations/schedule/kind-legend
```

Возвращает 5 категорий с цветами для отрисовки таблицы:

| kind_code | kind             | label_ru               |
| --------- | ---------------- | ---------------------- |
| 1         | annual_vacation  | Ежегодный отпуск       |
| 2         | sick_leave       | Больничный             |
| 3         | day_off          | Day Off (нерабочий)    |
| 4         | business_trip    | Командировка           |
| 5         | remote_work      | Дистанционный режим    |

Используйте `color_hex` / `color_text_hex` из ответа для плашек. **Не хардкодьте**
категории — берите из этого эндпоинта.

---

## 1. Заявки сотрудников (employee → partner)

### Справочник заявляемых категорий

```
GET /api/v1/vacations/leave-kinds
```

Возвращает категории, которые сотрудник может запросить (отпуск, day off,
дистанционка). Командировка/больничный вносятся через ручные записи (см. часть 2).

### Список партнёров для согласования

```
GET /api/v1/vacations/partners
```

### Создать заявку

```
POST /api/v1/vacations/leave-requests
Content-Type: application/json

{
  "kind": "annual_vacation",      // из leave-kinds
  "dateFrom": "2026-07-01",
  "dateTo": "2026-07-14",
  "partnerUserId": 42,             // из partners
  "reason": "Ежегодный отпуск"
}
```

После создания формируется PDF-заявление, партнёру уходит письмо.

### Списки заявок

```
GET /api/v1/vacations/leave-requests?scope=mine&status=any
GET /api/v1/vacations/leave-requests?scope=to_decide&status=pending   // для партнёра
```

- `scope`: `mine` | `to_decide` | `all`
- `status`: `pending` | `approved` | `declined` | `cancelled` | `any`

### Решение партнёра

```
POST /api/v1/vacations/leave-requests/{id}/approve   { "decisionReason": "..." }
POST /api/v1/vacations/leave-requests/{id}/decline   { "decisionReason": "..." }
```

После `approve` дни автоматически появляются в графике (`absence_days`).

### Прочее

- `DELETE /api/v1/vacations/leave-requests/{id}` — сотрудник отменяет свою pending-заявку.
- `GET /api/v1/vacations/leave-requests/{id}/pdf` — скачать PDF заявления.

Что показать в UI: форма подачи заявки; список «мои заявки» со статусами; для
партнёра — очередь «на согласование» с кнопками Подтвердить/Отклонить.

---

## 2. Ручные записи в график с обязательным основанием (новое)

Если кто-то (админ/партнёр/офис-менеджер) хочет внести данные в таблицу вручную
(например, командировку с 5 по 10), он **обязан приложить документ-основание**
(приказ/заявление/доп. документы) на этот период. Документ обязателен для **всех**
категорий.

### Создать ручную запись (multipart/form-data)

```
POST /api/v1/vacations/schedule/manual-entries
Content-Type: multipart/form-data
```

Поля формы:

| Поле         | Тип        | Обяз. | Описание                                            |
| ------------ | ---------- | ----- | --------------------------------------------------- |
| `employeeId` | number     | да    | ID сотрудника в графике (`schedule_employees.id`)   |
| `dateFrom`   | YYYY-MM-DD | да    | Начало периода                                      |
| `dateTo`     | YYYY-MM-DD | да    | Конец периода (в пределах одного года графика)      |
| `kind`       | string     | *     | Ключ категории (`business_trip`, `sick_leave`, ...) |
| `kindCode`   | number     | *     | Альтернатива `kind` (1..5)                          |
| `reason`     | string     | нет   | Комментарий/причина                                 |
| `files`      | file[]     | да**  | Документы-основания (один или несколько)            |

`*` — укажите либо `kind`, либо `kindCode`.
`**` — минимум 1 файл обязателен для всех категорий, иначе вернётся `400`.

Ограничения по файлам: до **25 МБ** на файл, до **20** файлов; типы:
`pdf, jpg, jpeg, png, webp, heic, doc, docx, xls, xlsx, txt`.

Пример (JS):

```js
const fd = new FormData();
fd.append("employeeId", String(employeeId));
fd.append("kind", "business_trip");
fd.append("dateFrom", "2026-08-05");
fd.append("dateTo", "2026-08-10");
fd.append("reason", "Командировка в Ташкент");
for (const file of selectedFiles) fd.append("files", file);

await fetch("/api/v1/vacations/schedule/manual-entries", {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` }, // НЕ ставьте Content-Type вручную
  body: fd,
});
```

> Важно: при отправке `FormData` не задавайте заголовок `Content-Type` руками —
> браузер сам проставит `multipart/form-data; boundary=...`.

### Ответ (201)

```json
{
  "id": 12,
  "employeeId": 7,
  "kindCode": 4,
  "kind": "business_trip",
  "labelRu": "Командировка",
  "dateFrom": "2026-08-05",
  "dateTo": "2026-08-10",
  "reason": "Командировка в Ташкент",
  "createdByUserId": 3,
  "createdByName": "Иван Иванов",
  "createdAt": "2026-06-10T05:52:00Z",
  "documents": [
    {
      "id": 21,
      "originalFilename": "prikaz.pdf",
      "contentType": "application/pdf",
      "sizeBytes": 184512,
      "downloadUrl": "/api/v1/vacations/schedule/manual-entries/12/documents/21/download",
      "createdAt": "2026-06-10T05:52:00Z"
    }
  ]
}
```

После создания дни периода появляются в графике (с привязкой к записи). Если на
дату уже была отметка — она перезаписывается категорией ручной записи.

### Список / детали ручных записей

```
GET /api/v1/vacations/schedule/manual-entries?year=2026&employeeId=7
GET /api/v1/vacations/schedule/manual-entries/{id}
```

### Управление основаниями

```
POST   /api/v1/vacations/schedule/manual-entries/{id}/documents          // multipart, поле files[]
GET    /api/v1/vacations/schedule/manual-entries/{id}/documents/{docId}/download
DELETE /api/v1/vacations/schedule/manual-entries/{id}/documents/{docId}   // нельзя удалить последний, если основание обязательно
```

### Удалить ручную запись

```
DELETE /api/v1/vacations/schedule/manual-entries/{id}
```

Удаляет запись, её документы и связанные дни графика.

---

## 3. Отрисовка графика (таблица)

```
GET /api/v1/vacations/schedule/employees?year=2026
GET /api/v1/vacations/schedule/absence-days?year=2026&dateFrom=...&dateTo=...
GET /api/v1/vacations/schedule/employees/{employeeId}?year=2026   // сотрудник + его дни
```

Каждый день (`absence-days`) содержит `kind_code`/`kind` — красьте ячейку по
легенде. Чтобы показать основание для ячейки, найдите ручную запись сотрудника,
покрывающую эту дату (через список manual-entries), и дайте ссылки на её
документы (`downloadUrl`).

---

## 4. Доступ (роли)

- **Просмотр графика и ручных записей** (`GET`): админ, партнёр, IT, офис-менеджер
  (роли из VACATION_VIEW).
- **Создание/изменение/удаление ручных записей** (`POST/PATCH/DELETE`): только
  Главный администратор, Администратор, Партнёр, Офис-менеджер.
- **Подача/отмена своей заявки, выбор партнёра, справочники** — любой
  авторизованный сотрудник.
- **Решение по заявке** — только выбранный в заявке партнёр.

Кнопки ручного редактирования и загрузки оснований показывайте только ролям с
правом управления графиком.

---

## 5. Чеклист для фронта

- [ ] Легенда категорий из `GET /schedule/kind-legend` (5 категорий, цвета).
- [ ] Форма подачи заявки + списки заявок (mine / to_decide) + кнопки решения партнёра.
- [ ] Форма ручной записи с **обязательной загрузкой файлов-оснований** (multipart),
      выбор категории/периода/сотрудника.
- [ ] Валидация на фронте: период в пределах одного года, хотя бы один файл,
      типы/размер файлов (дублирует серверную, для UX).
- [ ] Показ и скачивание документов-оснований у записей графика.
- [ ] Скрывать управляющие действия для ролей без права управления графиком.
- [ ] Обработка ошибок `400/403/404/409` — показывать `detail` из ответа.
