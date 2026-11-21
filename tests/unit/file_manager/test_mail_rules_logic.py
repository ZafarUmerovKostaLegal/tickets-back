from dataclasses import dataclass
from datetime import datetime, timezone

from support.service_path import ensure_service_in_path


def _mod():
    ensure_service_in_path("file_manager")
    import application.mail_rules as mail_rules

    return mail_rules


@dataclass
class Rule:
    id: str
    scope: str
    owner_user_id: int
    match: str
    enabled: bool = True


def test_matches_are_normalized_and_validated():
    m = _mod()
    assert m.normalize_match(" MailTo:<Ivan@Client.COM> ") == "ivan@client.com"
    assert m.normalize_match("@Client.com") == "client.com"
    assert m.match_problem("ivan@client.com") is None
    assert m.match_problem("client.com") is None
    assert m.match_problem("@gmail.com") is not None
    assert m.match_problem("kostalegal.com") is not None
    assert m.match_problem("ivan@") is not None
    assert m.match_problem("") is not None
    assert m.match_problem("someone@gmail.com") is None


def test_personal_beats_shared_and_address_beats_domain():
    m = _mod()
    rules = [
        Rule("shared-domain", "shared", 1, "client.com"),
        Rule("shared-exact", "shared", 1, "ivan@client.com"),
        Rule("mine-domain", "personal", 7, "client.com"),
        Rule("someone-elses", "personal", 9, "ivan@client.com"),
    ]
    incoming = m.MailFacts(direction="in", sender="Ivan@Client.com", recipients=("me@kostalegal.com",))
    assert m.pick_rule(rules, incoming, mailbox_owner=7).id == "mine-domain"
    assert m.pick_rule(rules, incoming, mailbox_owner=5).id == "shared-exact"
    assert m.pick_rule(rules, m.MailFacts("in", "anna@client.com", ()), mailbox_owner=5).id == "shared-domain"


def test_subdomains_sent_items_and_disabled_rules():
    m = _mod()
    rules = [Rule("acme", "shared", 1, "acme.com"), Rule("off", "shared", 1, "beta.com", enabled=False)]
    assert m.pick_rule(rules, m.MailFacts("in", "x@mail.acme.com", ()), 3).id == "acme"
    assert m.pick_rule(rules, m.MailFacts("in", "x@notacme.com", ()), 3) is None
    outgoing = m.MailFacts("out", "me@kostalegal.com", ("colleague@kostalegal.com", "ceo@acme.com"))
    assert m.pick_rule(rules, outgoing, 3).id == "acme"
    assert m.pick_rule(rules, m.MailFacts("in", "x@beta.com", ()), 3) is None


def test_file_name_is_safe():
    m = _mod()
    name = m.message_file_name('RE: Contract "v2" / final?', "ivan@client.com", datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert name == "2026-11-20 ivan@client.com — RE Contract v2 final.eml"
    assert m.message_file_name("", "", None) == "Без темы.eml"
