from infrastructure.project_access_mail import (
    build_project_access_added_bodies,
    build_project_access_added_subject,
)


def test_project_access_added_subject() -> None:
    assert build_project_access_added_subject(project_name="NorNickel") == (
        "Вас включили в проект — NorNickel"
    )


def test_project_access_added_body_matches_template() -> None:
    text, html_body = build_project_access_added_bodies(
        project_name="НорНикель-Добер",
        client_name="НорНикель",
        signature_name="Гузаль Темирова",
        signature_title="Контрактный менеджер",
    )
    assert "Добрый день." in text
    assert "В нашей системе Вас включили в проект — НорНикель-Добер, клиент — НорНикель." in text
    assert "Благодарим за внимание." in text
    assert "Гузаль Темирова" in text
    assert "Контрактный менеджер" in text
    assert "НорНикель-Добер" in html_body
    assert "НорНикель" in html_body
