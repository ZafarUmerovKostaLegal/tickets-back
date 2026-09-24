"""Standalone public letter card. Served on the API host, not inside the portal."""

from __future__ import annotations

import html
import json
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


def _csp(nonce: str, *, meta: bool) -> str:
    policy = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "frame-src 'self'; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )
    if not meta:
        policy += "; frame-ancestors 'none'"
    return policy


def public_card_headers(nonce: str) -> dict[str, str]:
    csp = _csp(nonce, meta=False)
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
    csp = _csp(nonce, meta=True)
    number = html.escape((registry_number or "").strip() or "Без номера")
    when = html.escape(format_public_date(issued_on))
    who = html.escape((counterparty or "").strip() or "—")
    theme = html.escape((subject or "").strip() or "—")
    kind = html.escape(doc_type_label(doc_type))
    name = html.escape((file_name or "Документ").strip() or "Документ")
    path = json.dumps(file_path)
    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="referrer" content="no-referrer"/>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23E6282C'/%3E%3C/svg%3E"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&amp;display=swap"/>
<meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}"/>
<title>Kosta Legal · {number}</title>
<style nonce="{nonce}">
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: #f8fafc; color: #0f172a; font-family: "Montserrat", system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .layout {{ display: grid; grid-template-columns: 320px minmax(0, 1fr); height: 100%; }}
  .side {{
    background: #fff; border-right: 1px solid #e2e8f0; padding: 1.25rem 1.2rem 2rem;
    overflow: auto; display: flex; flex-direction: column; gap: 0.15rem;
  }}
  .brand {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.35rem; }}
  .brand svg {{ width: 42px; height: 42px; flex-shrink: 0; }}
  .brand strong {{ display: block; font-size: 0.95rem; letter-spacing: 0.04em; }}
  .brand span {{ display: block; margin-top: 0.1rem; color: #64748b; font-size: 0.75rem; }}
  .num {{ margin: 0.2rem 0 0; font-size: 1.25rem; font-weight: 720; letter-spacing: -0.03em; }}
  .date {{ margin: 0.2rem 0 0.6rem; color: #64748b; font-size: 0.9rem; }}
  .label {{ margin: 0.95rem 0 0.2rem; color: #64748b; font-size: 0.75rem; font-weight: 650; letter-spacing: 0.04em; text-transform: uppercase; }}
  .value {{ margin: 0; font-size: 0.98rem; font-weight: 620; line-height: 1.4; }}
  .file {{
    display: flex; gap: 0.55rem; align-items: flex-start; width: 100%; margin-top: 0.35rem;
    padding: 0.7rem 0.75rem; border: 1px solid #e2e8f0; border-radius: 12px;
    background: #f8fafc; color: inherit; font: inherit; text-align: left; cursor: pointer;
  }}
  .file:hover {{ border-color: #c7d2fe; background: #eef2ff; }}
  .file strong {{ font-weight: 640; line-height: 1.35; word-break: break-word; }}
  .stage {{
    min-width: 0; height: 100%; overflow: auto; background: #eef2f7;
    padding: 1.5rem 1.25rem 2.5rem;
  }}
  .paper {{
    width: min(820px, 100%); margin: 0 auto; background: #fff;
    border-radius: 4px; box-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
    overflow: hidden;
  }}
  .paper img {{ display: block; width: 100%; height: auto; background: #fff; }}
  .menu {{
    display: none; position: fixed; z-index: 4;
    top: max(12px, env(safe-area-inset-top));
    left: max(12px, env(safe-area-inset-left));
    width: 42px; height: 42px; border: 1px solid #e2e8f0; border-radius: 12px;
    background: #fff; color: #0f172a; font-size: 1.15rem;
    box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
    transition: transform 0.2s ease, background 0.2s ease;
  }}
  .menu:active {{ transform: scale(0.96); }}
  .backdrop {{
    display: none; position: fixed; inset: 0; z-index: 2; border: 0; padding: 0;
    background: rgba(15, 23, 42, 0.35); opacity: 0; pointer-events: none;
    transition: opacity 0.28s ease;
  }}
  .paper {{ animation: rise 0.45s cubic-bezier(0.22, 1, 0.36, 1) both; }}
  @keyframes rise {{
    from {{ opacity: 0; transform: translateY(10px); }}
    to {{ opacity: 1; transform: none; }}
  }}
  @media (max-width: 800px) {{
    .layout {{ grid-template-columns: 1fr; }}
    .menu, .backdrop {{ display: block; }}
    .menu {{ display: grid; place-items: center; }}
    .side {{
      position: fixed; z-index: 3; top: 0; bottom: 0; left: 0;
      width: min(86vw, 340px);
      padding: 1.15rem 1.1rem calc(1.5rem + env(safe-area-inset-bottom));
      transform: translateX(-105%);
      transition: transform 0.32s cubic-bezier(0.22, 1, 0.36, 1);
      box-shadow: none;
    }}
    .side.is-open {{
      transform: none;
      box-shadow: 12px 0 40px rgba(15, 23, 42, 0.16);
    }}
    .side .brand {{
      min-height: 42px;
      margin-top: max(0px, env(safe-area-inset-top));
      padding-left: 3.35rem;
    }}
    .backdrop.is-open {{ opacity: 1; pointer-events: auto; }}
    .stage {{
      padding: calc(4.2rem + env(safe-area-inset-top)) 0.75rem calc(1.25rem + env(safe-area-inset-bottom));
    }}
    .paper {{ width: 100%; border-radius: 2px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .paper, .side, .backdrop, .menu {{ animation: none; transition: none; }}
  }}
</style>
</head>
<body>
<button class="menu" type="button" id="open" aria-label="Сведения" aria-expanded="false">☰</button>
<button class="backdrop" type="button" id="backdrop" aria-label="Закрыть"></button>
<div class="layout">
  <aside class="side" id="sheet">
    <div class="brand">
      <svg viewBox="0 0 143 209" aria-hidden="true">
        <path d="M125.2 144.6C125.2 114.8 101 90.6 71.2 90.6C41.4 90.6 17.2 114.8 17.2 144.6C17.2 174.4 41.4 198.6 71.2 198.6C101 198.6 125.2 174.5 125.2 144.6ZM134.7 144.6C134.7 179.7 106.3 208.1 71.2 208.1C36.1 208.1 7.7 179.7 7.7 144.6C7.7 109.5 36.1 81.1 71.2 81.1C106.2 81.2 134.7 109.6 134.7 144.6Z" fill="#E6282C"/>
        <path d="M107.7 144.6C107.7 124.4 91.4 108.1 71.2 108.1C51 108.1 34.7 124.4 34.7 144.6C34.7 164.8 51 181.1 71.2 181.1C91.3 181.1 107.7 164.8 107.7 144.6ZM117.1 144.6C117.1 170 96.5 190.6 71.1 190.6C45.7 190.6 25.1 170 25.1 144.6C25.1 119.2 45.7 98.6 71.1 98.6C96.6 98.7 117.1 119.2 117.1 144.6Z" fill="#E6282C"/>
        <path d="M0 71.1L71.2 0L142.3 71.1H128.9L71.2 13.4L13.4 71.1H0Z" fill="#E6282C"/>
        <path d="M25.6 71.1L71.2 25.6L116.7 71.1H103.3L71.2 39L39 71.1H25.6Z" fill="#E6282C"/>
      </svg>
      <div>
        <strong>KOSTA LEGAL</strong>
        <span>Проверка документа</span>
      </div>
    </div>
    <p class="num">{number}</p>
    <p class="date">{when}</p>
    <p class="label">Кому</p>
    <p class="value">{who}</p>
    <p class="label">Тема</p>
    <p class="value">{theme}</p>
    <p class="label">Вид документа</p>
    <p class="value">{kind}</p>
    <p class="label">Подписанный документ</p>
    <button class="file" type="button" id="open-file"><strong>{name}</strong></button>
  </aside>
  <main class="stage">
    <div class="paper"><img id="doc" alt="Письмо" /></div>
  </main>
</div>
<script nonce="{nonce}">
(function () {{
  var path = {path};
  var frame = document.getElementById("doc");
  var sheet = document.getElementById("sheet");
  var menu = document.getElementById("open");
  var backdrop = document.getElementById("backdrop");
  function show() {{
    var params = new URLSearchParams(window.location.search);
    if (!params.get("token")) return;
    params.set("inline", "1");
    params.set("page", "1");
    frame.src = path + "?" + params.toString();
  }}
  function setOpen(open) {{
    sheet.classList.toggle("is-open", open);
    backdrop.classList.toggle("is-open", open);
    menu.setAttribute("aria-expanded", open ? "true" : "false");
  }}
  document.getElementById("open-file").addEventListener("click", function () {{
    show();
    setOpen(false);
  }});
  menu.addEventListener("click", function () {{
    setOpen(!sheet.classList.contains("is-open"));
  }});
  backdrop.addEventListener("click", function () {{ setOpen(false); }});
  show();
}})();
</script>
</body>
</html>"""
    return page, nonce
