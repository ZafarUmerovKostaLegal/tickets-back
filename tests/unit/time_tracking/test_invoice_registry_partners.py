from __future__ import annotations

from support.service_path import ensure_service_in_path


def _partner(project_ids, partners_by_project, initials, names=None):
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    return registry_sheet_partner(
        project_ids,
        partners_by_project=partners_by_project,
        initials_by_user=initials,
        names_by_user=names,
    )


def test_single_project_partner():
    assert _partner(["p1"], {"p1": [7]}, {7: "VBG"}) == "VBG"


def test_all_project_partners_are_listed_once():
    assert _partner(["p1"], {"p1": [7, 8, 7]}, {7: "VBG", 8: "SHMYU"}) == "VBG, SHMYU"


def test_no_project_partners_gives_empty_cell():
    assert _partner(["p1"], {"p1": []}, {}) == ""
    assert _partner([], {}, {}) == ""


def test_test_accounts_are_never_shown():
    assert _partner(["p1"], {"p1": [5, 7]}, {5: "TEST", 7: "NFH"}) == "NFH"
    assert _partner(["p1"], {"p1": [5]}, {5: "TestAcc"}) == ""


def test_initials_from_name_when_profile_has_none():
    assert _partner(["p1"], {"p1": [7]}, {}, {7: ("Ivan Petrov", "ip@x.com")}) == "IP"


def test_invoice_project_wins_over_line_projects():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import invoice_partner_project_ids

    assert invoice_partner_project_ids("p1", ["p2", "p3"]) == ["p1"]
    assert invoice_partner_project_ids(None, ["p2", "p3", "p2"]) == ["p2", "p3"]
    assert invoice_partner_project_ids("  ", []) == []
