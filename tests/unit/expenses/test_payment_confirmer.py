from types import SimpleNamespace

from presentation.deps import is_reimbursement_payment_confirmer


def test_testeracc_has_same_confirmer_rights_as_aakhmadjonov(monkeypatch):
    monkeypatch.setattr(
        "presentation.deps.get_settings",
        lambda: SimpleNamespace(expense_payment_confirmer_email="aakhmadjonov@kostalegal.com"),
    )
    assert is_reimbursement_payment_confirmer({"email": "aakhmadjonov@kostalegal.com"}) is True
    assert is_reimbursement_payment_confirmer({"email": "testeracc@kostalegal.com"}) is True
    assert is_reimbursement_payment_confirmer({"email": "testeracc@example.com"}) is True
    assert is_reimbursement_payment_confirmer({"email": None, "display_name": "testeracc"}) is True
    assert is_reimbursement_payment_confirmer({"email": "oidrisova@kostalegal.com"}) is False
