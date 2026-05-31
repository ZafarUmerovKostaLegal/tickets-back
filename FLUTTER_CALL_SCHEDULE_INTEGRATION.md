# Расписание звонков (Call Schedule) — интеграция во Flutter

Документ описывает, как подключить в мобильном приложении **Flutter** календарь общего почтового ящика Kosta Legal (звонки, встречи, ссылки Zoom / Teams / Meet). Данные приходят из **Microsoft Graph** через backend-сервис `call_schedule`; отдельной БД у модуля нет.

---

## 1. Архитектура

```
Flutter app  →  Gateway  →  call_schedule  →  Microsoft Graph (календарь ящика info@…)
     ↑              ↑
  JWT / cookie   прокси /api/v1/call-schedule/*
```

- Все запросы идут на **gateway**, не напрямую на `call_schedule:1245`.
- Авторизация — та же, что у остальных модулей Kosta Daily: **Bearer access token** (или cookie сессии, если webview).
- Любой **авторизованный** сотрудник с валидным токеном может читать календарь и создавать события (отдельной RBAC-роли на gateway нет).

---

## 2. Базовый URL

| Окружение   | Пример `baseUrl`                          |
| ----------- | ----------------------------------------- |
| Production  | `https://api.kostalegal.com` (ваш домен)  |
| Staging     | из `.env` / конфига Flutter               |
| Local (эмулятор Android) | `http://10.0.2.2:1234` (gateway на хосте) |
| Local (iOS simulator)      | `http://127.0.0.1:1234`                    |
| LAN (телефон / тот же Wi‑Fi) | `http://<IP_ПК>:1234` — **не** `:1245` и **не** `:8765` |

> **Важно:** порт `8765` — это Smart Home API (см. `FLUTTER_SMART_HOME_GATEWAY.md`).  
> Порт `1245` — внутренний `call_schedule` в Docker; с телефона используйте только **gateway `1234`**.

Префикс API:

```text
{baseUrl}/api/v1/call-schedule
```

Пример полного пути событий:

```text
GET https://api.example.com/api/v1/call-schedule/events?start=...&end=...
```

---

## 3. Авторизация

### 3.1 Заголовок (рекомендуется для Flutter)

```http
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: application/json
```

Токен тот же, что выдаёт auth после входа (Microsoft / admin login).

### 3.2 Cookie (если используете общий HTTP-клиент с web)

Gateway может принимать cookie `kl_access_token` (имя из `AUTH_SESSION_COOKIE_NAME`). Для нативного Flutter обычно достаточно Bearer.

### 3.3 Ошибки auth

| HTTP | Значение |
| ---- | -------- |
| 401  | Нет токена / истёк — перелогин |
| 503  | Auth или call_schedule недоступен |

Тело ошибки: `{"detail": "..."}`.

---

## 4. Эндпоинты

### 4.1 Список календарей ящика

```http
GET /api/v1/call-schedule/calendars
```

**Ответ 200:**

```json
{
  "mailbox": "info@kostalegal.com",
  "calendars": [
    {
      "id": "AAMkAGI2...",
      "name": "Календарь",
      "color": "auto",
      "isDefaultCalendar": true,
      "canEdit": true
    }
  ]
}
```

Поля в `calendars[]` — как у Microsoft Graph (`id`, `name`, …). Для основного календаря можно не передавать `calendarId` в других запросах (см. `default`).

---

### 4.2 События за период

```http
GET /api/v1/call-schedule/events?start={iso}&end={iso}&calendarId={id}
```

| Query         | Обязательный | Описание |
| ------------- | ------------ | -------- |
| `start`       | да           | Начало периода, **ISO 8601** (лучше UTC с `Z`) |
| `end`         | да           | Конец периода, строго **позже** `start` |
| `calendarId`  | нет          | `id` из `/calendars` или `default` — основной календарь |

**Пример:**

```http
GET /api/v1/call-schedule/events?start=2026-05-26T00:00:00Z&end=2026-06-02T00:00:00Z&calendarId=default
```

**Ответ 200:**

```json
{
  "mailbox": "info@kostalegal.com",
  "calendarId": "default",
  "start": "2026-05-26T00:00:00+00:00",
  "end": "2026-06-02T00:00:00+00:00",
  "events": [ /* массив событий Graph + обогащение */ ]
}
```

#### Поля события, важные для UI

Backend добавляет к ответу Graph:

| Поле | Тип | Назначение |
| ---- | --- | ---------- |
| `id` | string | Идентификатор события |
| `subject` | string | Заголовок |
| `start` / `end` | object | `{ "dateTime": "...", "timeZone": "UTC" }` |
| `bodyPreview` | string | Краткий текст |
| `isCancelled` | bool | Отменённое — не показывать как активное |
| `showAs` | string | `busy`, `free`, … |
| `location` | object? | Место / текст локации |
| `onlineMeeting` | object? | Teams: `joinUrl` |
| **`meetingJoinUrl`** | string? | **Главная ссылка «Подключиться»** (Zoom приоритетнее Teams, если так настроен сервер) |
| **`meetingLinks`** | array? | Все найденные ссылки: `[{ "url": "https://...", "kind": "zoom" \| "teams" \| "meet" \| "webex" \| "other" }]` |
| `webLink` | string? | Открыть в Outlook Web |

Для кнопки «Войти в звонок» используйте **`meetingJoinUrl`**, fallback — первый элемент `meetingLinks` с `kind != "other"`, затем `onlineMeeting.joinUrl`, затем `webLink`.

---

### 4.3 Создать событие (запись звонка)

```http
POST /api/v1/call-schedule/events
Content-Type: application/json
```

**Тело (camelCase):**

```json
{
  "subject": "Консультация с клиентом",
  "start": "2026-05-27T10:00:00Z",
  "end": "2026-05-27T11:00:00Z",
  "meetingUrl": "https://zoom.us/j/123456789",
  "body": "Дополнительный текст приглашения",
  "calendarId": null,
  "timeZone": "UTC"
}
```

| Поле | Обязательное | Описание |
| ---- | ------------ | -------- |
| `subject` | да | Тема (1–500 символов) |
| `start`, `end` | да | ISO 8601, `end` > `start` |
| `meetingUrl` | условно | **HTTPS**-ссылка Zoom / Meet / Webex и т.д. |
| `body` | нет | Текст после блока со ссылкой в теле приглашения |
| `calendarId` | нет | Календарь; `null` / не передавать = основной |
| `timeZone` | нет | По умолчанию `"UTC"`; передаётся в Graph (`Prefer: outlook.timezone=...`) |

#### Режим Teams на сервере

Если в backend включено `CALL_SCHEDULE_CREATE_AS_TEAMS_MEETING=true` (по умолчанию в docker-compose), Graph **сам создаёт** Teams-встречу, и **`meetingUrl` можно не передавать**.

Если Teams **выключен**, без `meetingUrl` вернётся **400**:

```json
{
  "detail": "Укажите meetingUrl (https://) на Zoom, Google Meet, Webex и т.д. ..."
}
```

`meetingUrl` всегда должен начинаться с **`https://`**.

**Ответ 200** — объект созданного события (как в Graph + `meetingJoinUrl` / `meetingLinks`).

---

## 5. Рекомендуемый клиент (Dart)

### 5.1 Зависимости

```yaml
dependencies:
  dio: ^5.4.0
  url_launcher: ^6.2.0
  intl: ^0.19.0
```

### 5.2 Модели (упрощённо)

```dart
class CallScheduleCalendar {
  final String id;
  final String name;
  final bool isDefault;

  CallScheduleCalendar({
    required this.id,
    required this.name,
    this.isDefault = false,
  });

  factory CallScheduleCalendar.fromJson(Map<String, dynamic> j) =>
      CallScheduleCalendar(
        id: j['id'] as String,
        name: j['name'] as String? ?? '',
        isDefault: j['isDefaultCalendar'] == true,
      );
}

class MeetingLink {
  final String url;
  final String kind; // zoom | teams | meet | webex | other

  MeetingLink({required this.url, required this.kind});

  factory MeetingLink.fromJson(Map<String, dynamic> j) => MeetingLink(
        url: j['url'] as String,
        kind: j['kind'] as String? ?? 'other',
      );
}

class CallScheduleEvent {
  final String id;
  final String subject;
  final DateTime start;
  final DateTime end;
  final bool isCancelled;
  final String? meetingJoinUrl;
  final List<MeetingLink> meetingLinks;

  CallScheduleEvent({
    required this.id,
    required this.subject,
    required this.start,
    required this.end,
    this.isCancelled = false,
    this.meetingJoinUrl,
    this.meetingLinks = const [],
  });

  String? get joinUrl {
    if (meetingJoinUrl != null && meetingJoinUrl!.isNotEmpty) {
      return meetingJoinUrl;
    }
    for (final l in meetingLinks) {
      if (l.kind != 'other') return l.url;
    }
    return meetingLinks.isNotEmpty ? meetingLinks.first.url : null;
  }

  factory CallScheduleEvent.fromJson(Map<String, dynamic> j) {
    DateTime parseGraphDt(Map<String, dynamic>? block) {
      final raw = block?['dateTime'] as String? ?? '';
      var s = raw.trim();
      if (s.endsWith('Z')) s = '${s.substring(0, s.length - 1)}+00:00';
      return DateTime.parse(s).toLocal();
    }

    final links = (j['meetingLinks'] as List<dynamic>?)
            ?.map((e) => MeetingLink.fromJson(e as Map<String, dynamic>))
            .toList() ??
        [];

    return CallScheduleEvent(
      id: j['id'] as String,
      subject: j['subject'] as String? ?? '(без темы)',
      start: parseGraphDt(j['start'] as Map<String, dynamic>?),
      end: parseGraphDt(j['end'] as Map<String, dynamic>?),
      isCancelled: j['isCancelled'] == true,
      meetingJoinUrl: j['meetingJoinUrl'] as String?,
      meetingLinks: links,
    );
  }
}
```

### 5.3 Сервис API

```dart
import 'package:dio/dio.dart';

class CallScheduleApi {
  CallScheduleApi({
    required String gatewayBaseUrl,
    required Future<String?> Function() accessToken,
    Dio? dio,
  })  : _base = gatewayBaseUrl.replaceAll(RegExp(r'/+$'), ''),
        _token = accessToken,
        _dio = dio ?? Dio();

  final String _base;
  final Future<String?> Function() _token;
  final Dio _dio;

  String get _prefix => '$_base/api/v1/call-schedule';

  Future<Options> _authOptions() async {
    final t = await _token();
    if (t == null || t.isEmpty) {
      throw Exception('Not authenticated');
    }
    return Options(
      headers: {
        'Authorization': 'Bearer $t',
        'Accept': 'application/json',
      },
    );
  }

  Future<List<CallScheduleCalendar>> fetchCalendars() async {
    final r = await _dio.get(
      '$_prefix/calendars',
      options: await _authOptions(),
    );
    final list = r.data['calendars'] as List<dynamic>? ?? [];
    return list
        .map((e) => CallScheduleCalendar.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<CallScheduleEvent>> fetchEvents({
    required DateTime rangeStartUtc,
    required DateTime rangeEndUtc,
    String calendarId = 'default',
  }) async {
    String isoUtc(DateTime d) =>
        d.toUtc().toIso8601String().replaceAll(RegExp(r'\.\d+'), '');

    final r = await _dio.get(
      '$_prefix/events',
      queryParameters: {
        'start': '${isoUtc(rangeStartUtc)}Z',
        'end': '${isoUtc(rangeEndUtc)}Z',
        'calendarId': calendarId,
      },
      options: await _authOptions(),
    );
    final events = r.data['events'] as List<dynamic>? ?? [];
    return events
        .map((e) => CallScheduleEvent.fromJson(e as Map<String, dynamic>))
        .where((e) => !e.isCancelled)
        .toList()
      ..sort((a, b) => a.start.compareTo(b.start));
  }

  Future<CallScheduleEvent> createEvent({
    required String subject,
    required DateTime startUtc,
    required DateTime endUtc,
    String? meetingUrl,
    String? body,
    String? calendarId,
    String timeZone = 'UTC',
  }) async {
    final payload = <String, dynamic>{
      'subject': subject,
      'start': startUtc.toUtc().toIso8601String(),
      'end': endUtc.toUtc().toIso8601String(),
      'timeZone': timeZone,
      if (meetingUrl != null && meetingUrl.isNotEmpty) 'meetingUrl': meetingUrl,
      if (body != null && body.isNotEmpty) 'body': body,
      if (calendarId != null) 'calendarId': calendarId,
    };

    final r = await _dio.post(
      '$_prefix/events',
      data: payload,
      options: await _authOptions()
        ..headers?['Content-Type'] = 'application/json',
    );
    return CallScheduleEvent.fromJson(r.data as Map<String, dynamic>);
  }
}
```

### 5.4 Открытие ссылки на встречу

```dart
import 'package:url_launcher/url_launcher.dart';

Future<void> openMeeting(String url) async {
  final uri = Uri.parse(url);
  if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
    throw Exception('Cannot open $url');
  }
}
```

На **Android 11+** добавьте в `AndroidManifest.xml` queries для `https` (или конкретных хостов zoom / teams), иначе `canLaunchUrl` может вернуть false.

---

## 6. Сценарии UI

### 6.1 Экран «Расписание звонков» (неделя / день)

1. При открытии экрана вычислите интервал, например понедельник 00:00 UTC — воскресенье 24:00 UTC (или локальная неделя, конвертированная в UTC для query).
2. `fetchEvents(rangeStartUtc, rangeEndUtc)`.
3. Отрисуйте список / `TableCalendar` / custom timeline: `subject`, локальное время `start`–`end`, бейдж `kind` из `meetingLinks`.
4. Tap по событию → bottom sheet: «Подключиться» (`joinUrl`), «Открыть в Outlook» (`webLink` из raw JSON, если нужно).

### 6.2 Pull-to-refresh

Повторный `fetchEvents` с тем же диапазоном. Кэш на 1–5 минут в памяти допустим; для «живого» календаря лучше refresh при `AppLifecycleState.resumed`.

### 6.3 Создание звонка

1. Форма: тема, дата/время начала и конца, ссылка (если не Teams-only), опционально заметка.
2. Валидация на клиенте: `end > start`, `meetingUrl` начинается с `https://` (если поле обязательно в вашем окружении).
3. `createEvent` → snackbar «Создано» → обновить список.
4. После успеха можно сразу предложить `openMeeting(event.joinUrl!)`.

### 6.4 Выбор календаря (опционально)

Если у ящика несколько календарей — `fetchCalendars()` один раз, сохранить выбранный `calendarId` в `SharedPreferences`, передавать в `fetchEvents`.

---

## 7. Даты и часовые пояса

- Query-параметры `start` / `end` сервер парсит в **UTC** (поддерживается суффикс `Z` и offset `+00:00`).
- В теле `POST` поле `timeZone` влияет на то, как Graph интерпретирует `dateTime` в payload (часто удобно слать **UTC** и `timeZone: "UTC"`).
- В UI показывайте время через `.toLocal()` после парсинга (см. модель выше).

**Пример недели (UTC):**

```dart
final now = DateTime.now().toUtc();
final weekStart = DateTime.utc(now.year, now.month, now.day)
    .subtract(Duration(days: now.weekday - 1)); // понедельник, при weekday Mon=1
final weekEnd = weekStart.add(const Duration(days: 7));
```

Подстройте под `DateTime.monday` / пакет `timezone`, если неделя начинается в воскресенье.

---

## 8. Обработка ошибок

| HTTP | `detail` (примеры) | Действие во Flutter |
| ---- | ------------------ | ------------------- |
| 400  | Неверные даты, нет `meetingUrl` | Показать текст пользователю |
| 401  | Authorization required | На экран входа |
| 502  | Microsoft Graph: 403 … | «Календарь временно недоступен» (проблема Azure/прав) |
| 503  | CALL_SCHEDULE_MAILBOX не задан | DevOps; на prod не должно быть |
| 503  | Call schedule service unreachable | Gateway не видит контейнер |

Парсинг:

```dart
String errorMessage(DioException e) {
  final data = e.response?.data;
  if (data is Map && data['detail'] != null) {
    return data['detail'].toString();
  }
  return e.message ?? 'Ошибка сети';
}
```

---

## 9. Диагностика

| Проверка | URL / действие |
| -------- | -------------- |
| Gateway жив | `GET {baseUrl}/live` |
| Call schedule из gateway | `GET {baseUrl}/health/call-schedule` |
| С токеном | `GET {baseUrl}/api/v1/call-schedule/calendars` |

Если `/calendars` → **502** с текстом про Graph **403**: на стороне Azure нужны application permissions `Calendars.Read` + `Calendars.ReadWrite` и admin consent; ящик `CALL_SCHEDULE_MAILBOX` должен быть доступен приложению.

---

## 10. Чек-лист интеграции Flutter

- [ ] В конфиге приложения задан `gatewayBaseUrl` (не URL микросервиса `:1245`).
- [ ] HTTP-клиент добавляет `Authorization: Bearer …` ко всем запросам `/api/v1/call-schedule/*`.
- [ ] Экран расписания: `GET /events` с корректным диапазоном UTC.
- [ ] Кнопка «Подключиться» использует `meetingJoinUrl` / `meetingLinks`.
- [ ] Форма создания: `POST /events`, обработка 400 без `meetingUrl` (если у вас не Teams-only).
- [ ] Отменённые (`isCancelled`) скрыты или зачёркнуты.
- [ ] `url_launcher` + manifest queries для Android.
- [ ] Обработка 401 → re-login.

---

## 11. Связанные переменные backend (для DevOps)

Не задаются во Flutter, но влияют на поведение API:

| Переменная | Эффект |
| ---------- | ------ |
| `CALL_SCHEDULE_MAILBOX` | Чей календарь читается (например `info@kostalegal.com`) |
| `CALL_SCHEDULE_CREATE_AS_TEAMS_MEETING` | `true` — можно создавать без `meetingUrl` |
| `CALL_SCHEDULE_PREFER_ZOOM_JOIN_OVER_TEAMS` | В списке приоритет Zoom-ссылки в `meetingJoinUrl` |
| `MICROSOFT_*` / `CALL_SCHEDULE_MICROSOFT_*` | Учётные данные Graph |

---

## 12. Краткая шпаргалка путей

| Метод | Путь |
| ----- | ---- |
| GET | `/api/v1/call-schedule/calendars` |
| GET | `/api/v1/call-schedule/events?start=&end=&calendarId=` |
| POST | `/api/v1/call-schedule/events` |

Тело POST — JSON в **camelCase** (`meetingUrl`, `calendarId`, `timeZone`).
