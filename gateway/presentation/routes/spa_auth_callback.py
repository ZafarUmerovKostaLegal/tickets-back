

import json
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from infrastructure.config import get_settings

router = APIRouter(tags=["spa-auth-bridge"])


@router.get("/auth/callback", include_in_schema=False)
async def spa_auth_callback_bridge(request: Request) -> HTMLResponse:
    settings = get_settings()
    fe = (settings.frontend_url or "").strip().rstrip("/")
    if not fe:
        return HTMLResponse(
            "<!DOCTYPE html><html><body><p>Задайте FRONTEND_URL в окружении gateway (URL, где отдаётся SPA).</p></body></html>",
            status_code=503,
        )

    parsed = urlparse(fe if "://" in fe else f"https://{fe}")
    fe_host = (parsed.hostname or "").lower()
    req_host = (request.url.hostname or "").lower()

    home = f"{fe}/home"
    fe_base_json = json.dumps(fe)
    home_json = json.dumps(home)
    fe_host_json = json.dumps(fe_host)
    req_host_json = json.dumps(req_host)


    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width"/><title>Вход</title></head>
<body>
<script>
(function () {{
  var feBase = {fe_base_json};
  var home = {home_json};
  var feHost = {fe_host_json};
  var reqHost = {req_host_json};
  var h = window.location.hash || '';
  var s = window.location.search || '';

  function parseToken() {{
    if (h) {{
      var p = new URLSearchParams(h.replace(/^#/, ''));
      var t = p.get('access_token');
      if (t) return t;
    }}
    if (s) {{
      var p2 = new URLSearchParams(s.slice(1));
      return p2.get('access_token');
    }}
    return null;
  }}

  if (feHost && reqHost && feHost !== reqHost) {{
    window.location.replace(feBase + '/auth/callback' + s + h);
    return;
  }}

  var token = parseToken();
  if (token) {{
    try {{ localStorage.setItem('access_token', token); }} catch (e) {{}}
    window.location.replace(home);
    return;
  }}

  document.body.innerHTML = '<p>Нет access_token в URL. Проверьте FRONTEND_URL и redirect URI в Azure.</p>';
}})();
</script>
<noscript>Нужен JavaScript для завершения входа.</noscript>
</body></html>"""
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")
