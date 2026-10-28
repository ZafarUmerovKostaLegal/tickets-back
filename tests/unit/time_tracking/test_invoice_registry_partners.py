from __future__ import annotations

from support.service_path import ensure_service_in_path


def test_registry_partner_uses_project_when_hours_have_no_team():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    initials = {7: "MAD", 8: "VBG", 3: "AAA"}
    assert registry_sheet_partner(
        [11],
        0,
        ["project-invoice", "project-report"],
        partner_by_worker={},
        partners_by_project={"project-invoice": [7], "project-report": [8, 3]},
        initials_by_user=initials,
    ) == "MAD, VBG, AAA"


def test_registry_partner_keeps_team_codes_from_logged_hours():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    assert registry_sheet_partner(
        [11],
        4,
        ["project-1"],
        partner_by_worker={11: 8},
        partners_by_project={"project-1": [7]},
        initials_by_user={8: "VBG", 7: "MAD", 4: "AAA"},
    ) == "VBG"


def test_registry_partner_uses_report_project_when_invoice_has_none():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    assert registry_sheet_partner(
        [],
        4,
        ["project-report"],
        partner_by_worker={9: 9},
        partners_by_project={"project-report": [9]},
        initials_by_user={4: "KL", 9: "SHMYU"},
    ) == "SHMYU"
