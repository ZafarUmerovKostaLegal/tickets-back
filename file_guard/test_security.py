from application.policy import pick_daily_send_limit
from application.security import agent_token_configured, agent_token_matches, safe_file_name
from application.storage import resolve_stored_file
from infrastructure.models import FileGuardPolicyModel
from presentation.deps import journal_login_allowed


def test_token_rejects_short_and_wrong_secret():
    assert agent_token_configured("short") is False
    secret = "a" * 32
    assert agent_token_matches("bearer " + secret, secret) is True
    assert agent_token_matches("Bearer " + secret, secret) is True
    assert agent_token_matches("bearer " + ("b" * 32), secret) is False
    assert agent_token_matches("bearer " + secret + " ", secret) is True
    assert agent_token_matches("", secret) is False
    assert agent_token_matches(secret, "short") is False


def test_file_name_strips_directories_and_header_breaks():
    assert safe_file_name(r"C:\Users\a\Загрузки\cv.pdf") == "cv.pdf"
    assert safe_file_name(r"../../Windows/win.ini") == "win.ini"
    assert safe_file_name("report.pdf\r\nSet-Cookie: a=b") == "report.pdf__Set-Cookie_ a=b"
    assert safe_file_name("report.pdf\r\nContent-Type: text/html") == "html"
    assert safe_file_name("..") == "file"
    assert safe_file_name("") == "file"
    assert len(safe_file_name("a" * 400)) == 180


def test_stored_name_cannot_escape_directory(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    files = tmp_path / "files"
    files.mkdir()
    assert resolve_stored_file(str(files), "../secret.txt") is None
    assert resolve_stored_file(str(files), "..\\secret.txt") is None
    assert resolve_stored_file(str(files), "a" * 31) is None
    assert resolve_stored_file(str(files), "g" * 32) is None
    kept = resolve_stored_file(str(files), "ab" * 16)
    assert kept == (files / ("ab" * 16)).resolve()


def test_policy_prefers_exact_user():
    broad = FileGuardPolicyModel(id="1", host="*", windows_user="*", daily_send_limit=None)
    exact = FileGuardPolicyModel(id="2", host="PC-1", windows_user=r"KOSTA\a", daily_send_limit=3)
    assert pick_daily_send_limit([broad, exact], host="PC-1", windows_user=r"KOSTA\a") == 3
    assert pick_daily_send_limit([broad], host="PC-1", windows_user=r"KOSTA\a") is None


def test_journal_login_is_exact_local_part(monkeypatch):
    from infrastructure.config import get_settings

    settings = get_settings()
    previous = settings.journal_logins
    settings.journal_logins = "aakhmadjonov,zumerov"
    try:
        assert journal_login_allowed({"email": "Aakhmadjonov@kostalegal.com"}) is True
        assert journal_login_allowed({"email": "other@kostalegal.com"}) is False
        assert journal_login_allowed({"email": "aakhmadjonov.extra@kostalegal.com"}) is False
        assert journal_login_allowed({"email": ""}) is False
        settings.journal_logins = ""
        assert journal_login_allowed({"email": "aakhmadjonov@kostalegal.com"}) is False
    finally:
        settings.journal_logins = previous
