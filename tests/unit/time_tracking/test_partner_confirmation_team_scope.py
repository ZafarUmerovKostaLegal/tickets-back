from application.partner_confirmation_team_scope import partner_team_overlaps_report


def test_partner_team_overlaps_report_true_when_intersection():
    assert partner_team_overlaps_report(
        team_member_ids={101, 102},
        report_user_ids={102, 200},
    )


def test_partner_team_overlaps_report_false_when_no_intersection():
    assert not partner_team_overlaps_report(
        team_member_ids={101, 102},
        report_user_ids={200, 201},
    )


def test_partner_team_overlaps_report_false_when_empty_team():
    assert not partner_team_overlaps_report(
        team_member_ids=set(),
        report_user_ids={200},
    )
