"""Standalone public letter card. Served on the API host, not inside the portal."""

from __future__ import annotations

import html
import secrets
from datetime import datetime

_DOC_TYPE_LABELS = {
    "letter": "Письмо",
    "request": "Запрос",
    "claim": "Претензия",
    "demand": "Требование",
    "notification": "Уведомление",
    "application": "Заявление",
    "complaint": "Жалоба",
    "lawsuit": "Исковое заявление",
    "court": "Судебный документ",
    "enforcement": "Исполнительный документ",
    "contract": "Договор",
    "addendum": "Дополнительное соглашение",
    "act": "Акт",
    "financial": "Финансовый документ",
    "proposal": "Коммерческое предложение",
    "other": "Иное",
    "note": "Записка",
}

_MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def doc_type_label(value: str | None) -> str:
    key = (value or "").strip().lower()
    return _DOC_TYPE_LABELS.get(key, "Документ")


def format_public_date(value: datetime | None) -> str:
    if value is None:
        return "—"
    month = _MONTHS[value.month] if 1 <= value.month <= 12 else ""
    return f"{value.day} {month} {value.year}".strip()


def public_card_headers(nonce: str) -> dict[str, str]:
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; "
        "img-src 'self' data:; "
        "frame-src 'self'; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    )
    return {
        "Content-Security-Policy": csp,
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "no-store",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }


def render_public_card(
    *,
    registry_number: str | None,
    issued_on: datetime | None,
    counterparty: str,
    subject: str,
    doc_type: str,
    file_name: str,
    file_path: str,
) -> tuple[str, str]:
    """Return (html, nonce). file_path is same-origin, token is appended in the page script."""
    nonce = secrets.token_urlsafe(16)
    csp = public_card_headers(nonce)["Content-Security-Policy"]
    number = html.escape((registry_number or "").strip() or "Без номера")
    when = html.escape(format_public_date(issued_on))
    who = html.escape((counterparty or "").strip() or "—")
    theme = html.escape((subject or "").strip() or "—")
    kind = html.escape(doc_type_label(doc_type))
    name = html.escape((file_name or "Документ").strip() or "Документ")
    path = html.escape(file_path, quote=True)
    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="referrer" content="no-referrer"/>
<meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}"/>
<title>{number}</title>
<style nonce="{nonce}">
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: #d5dbe3; color: #1c2836; font-family: "Segoe UI", system-ui, sans-serif; }}
  .layout {{ display: grid; grid-template-columns: minmax(280px, 380px) minmax(0, 1fr); height: 100%; }}
  .sheet {{
    background: #3e5168; color: #f4f7fb; padding: 1.35rem 1.25rem 2rem;
    overflow: auto; box-shadow: 8px 0 24px rgba(20, 32, 48, 0.18);
  }}
  .mark {{
    width: 46px; height: 46px; margin: 0 auto 1.25rem; border-radius: 50%;
    display: grid; place-items: center; background: #f3e6b8; color: #8a6a12; font-weight: 750;
  }}
  .num {{ margin: 0; font-size: 1.35rem; font-weight: 720; letter-spacing: -0.02em; }}
  .date {{ margin: 0.35rem 0 1.15rem; color: #d5deea; font-size: 0.95rem; }}
  .label {{ margin: 1rem 0 0.35rem; color: #d5deea; font-size: 0.92rem; }}
  .value {{ margin: 0; font-size: 1.02rem; font-weight: 650; line-height: 1.35; }}
  .file {{
    display: flex; gap: 0.65rem; align-items: flex-start; width: 100%; margin-top: 0.35rem;
    padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer;
  }}
  .file strong {{ font-weight: 650; line-height: 1.35; }}
  .stage {{ min-width: 0; height: 100%; background: #e7edf3; }}
  .stage iframe {{ display: block; width: 100%; height: 100%; border: 0; background: #fff; }}
  .menu {{
    display: none; position: fixed; z-index: 3; top: 12px; left: 12px;
    width: 42px; height: 42px; border: 0; border-radius: 12px;
    background: #3e5168; color: #fff; font-size: 1.25rem;
  }}
  @media (max-width: 800px) {{
    .layout {{ grid-template-columns: 1fr; }}
    .menu {{ display: grid; place-items: center; }}
    .sheet {{
      position: fixed; z-index: 2; inset: 0 auto 0 0; width: min(88vw, 380px);
      transform: translateX(-105%); transition: transform 0.2s ease;
    }}
    .sheet.is-open {{ transform: none; }}
  }}
</style>
</head>
<body>
<button class="menu" type="button" id="open" aria-label="Сведения">☰</button>
<div class="layout">
  <aside class="sheet" id="sheet">
    <div class="mark" aria-hidden="true">KL</div>
    <p class="num">{number}</p>
    <p class="date">{when}</p>
    <p class="label">Кому</p>
    <p class="value">{who}</p>
    <p class="label">Тема</p>
    <p class="value">{theme}</p>
    <p class="label">Вид документа</p>
    <p class="value">{kind}</p>
    <p class="label">Подписанный документ</p>
    <button class="file" type="button" id="open-file">
      <span aria-hidden="true">📄</span>
      <strong>{name}</strong>
    </button>
  </aside>
  <main class="stage"><iframe id="doc" title="Документ" referrerpolicy="no-referrer"></iframe></main>
</div>
<script nonce="{nonce}">
(function () {{
  var path = "{path}";
  var frame = document.getElementById("doc");
  var sheet = document.getElementById("sheet");
  function show() {{
    var params = new URLSearchParams(window.location.search);
    if (!params.get("token")) return;
    params.set("inline", "1");
    frame.src = path + "?" + params.toString();
  }}
  document.getElementById("open-file").addEventListener("click", function () {{
    show();
    sheet.classList.remove("is-open");
  }});
  document.getElementById("open").addEventListener("click", function () {{
    sheet.classList.toggle("is-open");
  }});
  show();
}})();
</script>
</body>
</html>"""
    return page, nonce
