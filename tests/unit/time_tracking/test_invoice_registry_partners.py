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


def test_registry_partner_prefers_project_partners_whose_team_logged_hours():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    assert registry_sheet_partner(
        [11, 12],
        4,
        ["project-1"],
        partner_by_worker={11: 8, 12: 8},
        partners_by_project={"project-1": [7, 8, 3]},
        initials_by_user={8: "VBG", 7: "MAD", 3: "AAA", 4: "KL"},
    ) == "VBG"


def test_registry_partner_uses_project_partners_over_unrelated_team_partner():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    # The worker's team partner (a test account) has nothing to do with the project.
    assert registry_sheet_partner(
        [11],
        4,
        ["project-1"],
        partner_by_worker={11: 99},
        partners_by_project={"project-1": [7]},
        initials_by_user={99: "TEST", 7: "MAD", 4: "AAA"},
    ) == "MAD"


def test_registry_partner_never_returns_test_accounts():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    initials = {99: "TEST", 98: "test", 7: "MAD"}
    assert registry_sheet_partner(
        [11], 0, ["project-1"],
        partner_by_worker={11: 99},
        partners_by_project={"project-1": [98, 7]},
        initials_by_user=initials,
    ) == "MAD"
    # Only a test account anywhere -> empty cell instead of TEST.
    assert registry_sheet_partner(
        [11], 99, [],
        partner_by_worker={11: 99, 99: 99},
        partners_by_project={},
        initials_by_user=initials,
    ) == ""


def test_registry_partner_falls_back_to_team_partner_without_project_partners():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import registry_sheet_partner

    assert registry_sheet_partner(
        [11],
        4,
        ["project-1"],
        partner_by_worker={11: 8},
        partners_by_project={"project-1": []},
        initials_by_user={8: "VBG", 4: "AAA"},
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
