

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("call_schedule")

from infrastructure.meeting_links import (
    classify_meeting_url,
    event_body_text,
    event_meeting_urls_from_body_object,
    extract_https_urls,
)
from infrastructure.graph_mailbox import _enrich_event_with_join_url


def test_classify_meeting_url() -> None:
    assert classify_meeting_url("https://kostalegal.zoom.us/j/123?pwd=x") == "zoom"
    assert (
        classify_meeting_url("https://teams.microsoft.com/l/meetup-join/19%3a...")
        == "teams"
    )
    assert classify_meeting_url("https://meet.google.com/abc-defg-hij") == "meet"


def test_extract_from_html_body() -> None:
    ev = {
        "body": {
            "contentType": "html",
            "content": '<a href="https://zoom.us/j/999">join</a>',
        }
    }
    assert event_body_text(ev) == " join "
    assert event_meeting_urls_from_body_object(ev) == ["https://zoom.us/j/999"]


def test_enrich_meeting_join_and_links() -> None:
    ev = {
        "id": "1",
        "webLink": "https://outlook.office.com/calendar/item/AAMkAG1",
        "body": {
            "contentType": "text",
            "content": "Встреча\nhttps://kostalegal.zoom.us/j/1",
        },
    }
    out = _enrich_event_with_join_url(ev)
    assert out.get("meetingJoinUrl", "").find("zoom.us") >= 0
    assert "meetingLinks" in out
    assert any("zoom" in m["url"] for m in out["meetingLinks"])


def test_enrich_prefer_zoom_join_when_both_with_teams() -> None:

    ev = {
        "onlineMeeting": {
            "joinUrl": "https://teams.microsoft.com/l/meetup-join/xx",
        },
        "body": {
            "content": "https://zoom.us/j/1",
            "contentType": "text",
        },
    }
    out = _enrich_event_with_join_url(ev)
    assert "zoom" in (out.get("meetingJoinUrl") or "")
    assert len(out["meetingLinks"]) == 2
    assert any("teams" in m["url"] for m in out["meetingLinks"])


def test_enrich_location_zoom() -> None:
    ev = {
        "location": {
            "displayName": "Созвон",
            "locationUri": "https://us02web.zoom.us/j/123456?pwd=abc",
        }
    }
    out = _enrich_event_with_join_url(ev)
    assert "zoom" in (out.get("meetingJoinUrl") or "")
    assert any(m["kind"] == "zoom" for m in out.get("meetingLinks", []))


def test_http_zoom_normalized_to_https() -> None:
    u = extract_https_urls("join http://zoom.us/j/9")
    assert u == ["https://zoom.us/j/9"]


def test_enrich_fallback_join_url() -> None:
    ev = {
        "body": {
            "content": "только примечание",
            "contentType": "text",
        }
    }
    out = _enrich_event_with_join_url(
        ev, fallback_join_url="https://meet.google.com/xxx-yyy-zzz"
    )
    assert out["meetingJoinUrl"] == "https://meet.google.com/xxx-yyy-zzz"
