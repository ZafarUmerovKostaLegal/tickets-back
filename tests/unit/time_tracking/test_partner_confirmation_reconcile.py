from application.partner_report_confirmation_service import partners_confirmation_is_complete


def test_complete_when_all_required_partners_signed():
    assert partners_confirmation_is_complete([10, 20], {10, 20}) is True


def test_incomplete_when_required_partner_missing():
    assert partners_confirmation_is_complete([10, 20], {10}) is False


def test_complete_when_partners_removed_but_signatures_remain():
    assert partners_confirmation_is_complete([], {10}) is True


def test_incomplete_when_no_signatures():
    assert partners_confirmation_is_complete([10], set()) is False
    assert partners_confirmation_is_complete([], set()) is False
