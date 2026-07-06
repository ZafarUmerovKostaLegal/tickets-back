from infrastructure.project_access_mail import (
    build_project_access_added_bodies,
    build_project_access_added_subject,
    records_language_notice,
)


def test_project_access_added_subject() -> None:
    assert build_project_access_added_subject(project_name="NorNickel") == (
        "Вас включили в проект — NorNickel"
    )


def test_records_language_notice() -> None:
    assert records_language_notice("ENG") == "Записи вносим на английском языке."
    assert records_language_notice("RU") == "Записи вносим на русском языке."
    assert records_language_notice(None) == "Записи вносим на английском языке."


def test_project_access_added_body_matches_template_eng() -> None:
    text, html_body = build_project_access_added_bodies(
        project_name="НорНикель-Добер",
        client_name="НорНикель",
        records_language="ENG",
        signature_name="Гузаль Темирова",
        signature_title="Контрактный менеджер",
    )
    assert "Добрый день." in text
    assert "В нашей системе Вас включили в проект — НорНикель-Добер, клиент — НорНикель." in text
    assert "Записи вносим на английском языке." in text
    assert "Благодарим за внимание." in text
    assert "Гузаль Темирова" in text
    assert "Контрактный менеджер" in text
    assert "НорНикель-Добер" in html_body
    assert "НорНикель" in html_body
    assert "Английский (ENG)" in html_body
    assert "Доступ к проекту" in html_body


def test_project_access_added_body_ru_language() -> None:
    text, html_body = build_project_access_added_bodies(
        project_name="Проект А",
        client_name="Клиент Б",
        records_language="RU",
        signature_name="Гузаль Темирова",
        signature_title="Контрактный менеджер",
    )
    assert "Записи вносим на русском языке." in text
    assert "Русский (RU)" in html_body
