# Инструкция для фронта: бюджет в списке проектов

После обновления бэка endpoint списка проектов теперь отдаёт готовые поля для колонок:

- **Бюджет**
- **Потрачено**
- **Остаток**
- **Прогресс**

Endpoint:

- `GET /api/v1/time-tracking/clients/{clientId}/projects`
- `GET /api/v1/time-tracking/clients/{clientId}/projects/{projectId}`

---

## 1) Какие поля брать из ответа

Новые поля в объекте проекта:

- `budgetDisplayValue`
- `budgetSpentValue`
- `budgetRemainingValue`
- `budgetProgressPercent`

Пример маппинга:

```ts
const budget = project.budgetDisplayValue ?? 0
const spent = project.budgetSpentValue ?? 0
const remaining = project.budgetRemainingValue ?? 0
const progress = project.budgetProgressPercent ?? 0
```

---

## 2) Что показывать в UI

- Колонка **Бюджет** -> `budgetDisplayValue`
- Колонка **Потрачено** -> `budgetSpentValue`
- Колонка **Остаток** -> `budgetRemainingValue`
- Прогресс-бар / % -> `budgetProgressPercent`

Если значение `null`/`undefined`, показывайте `0` или `—` по вашему UX.

---

## 3) Формат по типу бюджета

Бэкенд уже учитывает режим проекта (`budgetType` / фактический режим):

- `hours` -> значения в часах
- `money` -> значения в валюте проекта
- `hours_and_money` -> для списка возвращается денежная часть (удобно для текущих колонок)
- `none` -> нули

---

## 4) Проверка в Network

Проверьте ответ `GET .../clients/{clientId}/projects`:

у каждого проекта должны быть поля:

- `budgetDisplayValue`
- `budgetSpentValue`
- `budgetRemainingValue`
- `budgetProgressPercent`

Если поля есть, но UI пустой (`—`) — проблема только в фронтовом маппинге.
