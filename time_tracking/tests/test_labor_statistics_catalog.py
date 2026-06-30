from application.labor_statistics_catalog import infer_work_type


def test_infer_work_type_litigation() -> None:
    wid, name = infer_work_type("Court Hearing Preparation")
    assert wid == "litigation"
    assert "Судеб" in name


def test_infer_work_type_other() -> None:
    wid, _ = infer_work_type("")
    assert wid == "other"
