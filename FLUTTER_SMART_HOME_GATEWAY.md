# Smart Home — доступ через Gateway (Flutter)

Раньше приложение ходило **напрямую** на `http://192.168.x.x:8765`. Теперь запросы нужно отправлять на **gateway** с префиксом `/api/v1/smart-home/`.

---

## 1. Новый базовый URL

| Было (не использовать) | Стало |
| ---------------------- | ----- |
| `http://192.168.230.121:8765` | `http://192.168.230.121:1234` (порт **gateway**) |

Префикс API:

```text
{gatewayBaseUrl}/api/v1/smart-home
```

**Пример:** если раньше был `GET http://192.168.230.121:8765/scenes`, то теперь:

```text
GET http://192.168.230.121:1234/api/v1/smart-home/scenes
Authorization: Bearer <access_token>
```

Путь после `/smart-home/` **совпадает** с путём на локальном API `:8765`.

---

## 2. Авторизация

На gateway обязателен тот же JWT, что и для Kosta Daily:

```http
Authorization: Bearer <access_token>
```

Без токена — **401**. Локальный сервер на `:8765` может не проверять JWT; gateway проверяет через auth `/users/me`.

---

## 3. Изменения в Flutter (`SmartHomeApiClient`)

В конфиге замените base URL:

```dart
// Было:
// static const baseUrl = 'http://192.168.230.121:8765';

// Стало (gateway на том же хосте, порт 1234):
static const gatewayBaseUrl = 'http://192.168.230.121:1234';
static const apiPrefix = '/api/v1/smart-home';

String url(String path) {
  final p = path.startsWith('/') ? path : '/$path';
  return '$gatewayBaseUrl$apiPrefix$p';
}
```

Метод `getList` / `fetchScenes` должен вызывать, например:

```dart
final response = await _dio.get(
  url('/scenes'), // или тот path, что был раньше без хоста
  options: Options(headers: await _authHeaders()),
);
```

**Android-эмулятор:** `http://10.0.2.2:1234/api/v1/smart-home/...`  
**Физический телефон:** IP ПК в Wi‑Fi + порт gateway `1234`.

---

## 4. Проверка с ПК

```powershell
# Gateway жив
curl http://192.168.230.121:1234/live

# Smart Home upstream с gateway (нужен токен)
curl -H "Authorization: Bearer YOUR_TOKEN" http://192.168.230.121:1234/health/smart-home

# Список сцен (подставьте свой path)
curl -H "Authorization: Bearer YOUR_TOKEN" http://192.168.230.121:1234/api/v1/smart-home/scenes
```

---

## 5. DevOps (.env gateway)

```env
GATEWAY_PORT=1234
SMART_HOME_SERVICE_URL=http://host.docker.internal:8765
```

Если Smart Home API на **другой машине** в LAN:

```env
SMART_HOME_SERVICE_URL=http://192.168.230.50:8765
```

После смены env перезапустите контейнер gateway:

```bash
docker compose up -d gateway
```

На Linux для `host.docker.internal` в compose добавлен `extra_hosts: host-gateway`.

---

## 6. Типичные ошибки

| Симптом | Причина |
| ------- | ------- |
| Connection refused на `:8765` из Flutter | В приложении всё ещё старый URL — переключите на `:1234/api/v1/smart-home` |
| 503 Smart Home unreachable from gateway | API на 8765 не запущен или `SMART_HOME_SERVICE_URL` неверный |
| 401 | Нет/просрочен Bearer token |
| 502/503 call schedule | См. `GET /health/call-schedule` — отдельный сервис `call_schedule` |

---

## 7. Расписание звонков (call schedule)

Это **другой** модуль, не Smart Home:

```text
{gateway}/api/v1/call-schedule/events
```

См. `FLUTTER_CALL_SCHEDULE_INTEGRATION.md`.
