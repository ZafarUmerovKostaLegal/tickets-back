# Расход партнёра — изменения API и фронта

## Поведение

| Аспект | Было | Стало |
|--------|------|-------|
| Дата | Только «сегодня» при создании | Для `partner_expense` — любая **прошедшая** дата (≤ сегодня) |
| Согласование | Уже без модерации (`approved` сразу) | Без изменений |
| Партнёр | Не указывался | Опциональное поле **`partnerUserId`** — чей расход фиксируется |
| Черновик | Кнопка «Сохранить черновик» | Для `partner_expense` скрыта — только «Записать расход» |

## API (expenses)

### POST `/api/v1/expenses`

```json
{
  "expenseType": "partner_expense",
  "expenseSubtype": "partner_fuel",
  "expenseDate": "2026-03-15",
  "partnerUserId": 42,
  "...": "..."
}
```

- `partnerUserId` — **необязателен**, только для `partner_expense`.
- Если указан — пользователь должен существовать и иметь org-роль «Партнер».
- `expenseDate` для партнёрского расхода не может быть в будущем.

### Ответ

Добавлены поля:

- `partnerUserId: number | null`
- `partnerUser: { id, displayName, email, ... } | null`

## Фронт (tickets-front)

Уже внедрено:

- `GET /api/v1/users/partners` — список для селекта (`listPartners()`).
- Форма расхода: дата + партнёр для типа «Расход партнёра».
- `ExpensesPage` / `ExpensesFormPanel` / типы в `@entities/expenses`.

## Деплой

1. Пересобрать **expenses** (миграция `partner_user_id` при старте).
2. Пересобрать **tickets-front**.
