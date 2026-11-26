"""Mail rules: «mail from / to X (with this subject) → matter / folder». Pure logic, shared by the API and the auto-filing engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN = re.compile(r"^(?=.{3,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# A rule for these would file every letter of half the internet (or of the firm itself).
GENERIC_DOMAINS = frozenset({
    "kostalegal.com",
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "yahoo.com", "aol.com", "proton.me", "protonmail.com",
    "mail.ru", "inbox.ru", "list.ru", "bk.ru", "yandex.ru", "ya.ru", "yandex.com", "rambler.ru",
})


class RuleLike(Protocol):
    id: str
    scope: str
    owner_user_id: int
    match: str
    enabled: bool


SUBJECT_SPLIT = re.compile(r"[,;\n]+")


def subject_phrases(raw: str | None) -> list[str]:
    """`Invoice, Счёт ; NDA` → ["invoice", "счёт", "nda"] (lower case, single spaces, no duplicates)."""
    out: list[str] = []
    for part in SUBJECT_SPLIT.split(raw or ""):
        phrase = " ".join(part.split()).lower()
        if phrase and phrase not in out:
            out.append(phrase)
    return out


def normalize_subject(raw: str | None) -> str:
    return ", ".join(subject_phrases(raw))[:300]


def subject_matches(phrases: list[str], subject: str) -> bool:
    text = " ".join((subject or "").split()).lower()
    return any(phrase in text for phrase in phrases)


def normalize_match(raw: str) -> str:
    """`Ivan@Client.com` → `ivan@client.com`; `@client.com` / `client.com` → `client.com`."""
    value = (raw or "").strip().lower()
    value = value.removeprefix("mailto:").strip("<>").strip()
    if value.startswith("@"):
        value = value[1:]
    return value


def match_problem(raw: str) -> str | None:
    """Why a match can't be used (None when it is fine)."""
    value = normalize_match(raw)
    if not value:
        return "Укажите адрес или домен"
    if "@" in value:
        if not _EMAIL.match(value):
            return "Неверный адрес почты"
        return None
    if not _DOMAIN.match(value):
        return "Неверный домен"
    if value in GENERIC_DOMAINS:
        return "Это общий почтовый домен — укажите конкретный адрес отправителя"
    return None


def is_domain_match(match: str) -> bool:
    return "@" not in match


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def address_matches(match: str, email: str) -> bool:
    email = normalize_match(email)
    if not email or "@" not in email:
        return False
    if not is_domain_match(match):
        return email == match
    domain = _domain(email)
    return domain == match or domain.endswith(f".{match}")


@dataclass(frozen=True)
class MailFacts:
    """What the engine knows about one message in one mailbox."""

    direction: str  # "in" (Inbox: look at the sender) | "out" (Sent Items: look at the recipients)
    sender: str
    recipients: tuple[str, ...]
    received_at: datetime | None = None
    subject: str = ""


def _addresses(facts: MailFacts) -> list[str]:
    if facts.direction == "out":
        return [normalize_match(addr) for addr in facts.recipients if addr]
    return [normalize_match(facts.sender)] if facts.sender else []


def pick_rule(rules: Iterable[RuleLike], facts: MailFacts, mailbox_owner: int) -> RuleLike | None:
    """The one rule that files this message from this mailbox.

    A rule may name a sender (address or domain), subject phrases, or both; every condition it has must hold.
    Personal rules of the mailbox owner win over shared ones; then a rule with a matching subject wins
    over one without; then a sender rule over a subject-only rule; an exact address over a domain;
    the longest (most specific) domain and subject last.
    """
    addresses = [addr for addr in _addresses(facts) if addr]
    best: tuple[tuple[int, int, int, int, int, int], RuleLike] | None = None
    for rule in rules:
        if not rule.enabled:
            continue
        if rule.scope == "personal" and rule.owner_user_id != mailbox_owner:
            continue
        match = normalize_match(rule.match)
        phrases = subject_phrases(getattr(rule, "subject", ""))
        if not match and not phrases:
            continue
        if match and not any(address_matches(match, addr) for addr in addresses):
            continue
        if phrases and not subject_matches(phrases, facts.subject):
            continue
        rank = (
            1 if rule.scope == "personal" else 0,
            1 if phrases else 0,
            1 if match else 0,
            0 if not match or is_domain_match(match) else 1,
            len(match),
            max((len(phrase) for phrase in phrases), default=0),
        )
        if best is None or rank > best[0]:
            best = (rank, rule)
    return best[1] if best else None


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def message_file_name(subject: str, sender: str, received_at: datetime | None) -> str:
    """`2026-11-20 ivan@client.com — Subject.eml` (safe on Windows, at most ~180 chars)."""
    day = received_at.strftime("%Y-%m-%d") if received_at else ""
    title = " ".join(_UNSAFE.sub(" ", subject or "").split())[:120] or "Без темы"
    who = _UNSAFE.sub(" ", sender or "").strip()[:60]
    base = " — ".join(part for part in (" ".join(part for part in (day, who) if part), title) if part)
    return f"{base.strip(' .')}.eml"
