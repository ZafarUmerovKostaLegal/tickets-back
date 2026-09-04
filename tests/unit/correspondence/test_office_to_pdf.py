from infrastructure.office_to_pdf import is_office_document


def test_is_office_document_by_name_and_mime():
    assert is_office_document("ИСХ_письмо.docx") is True
    assert is_office_document("scan.pdf") is False
    assert is_office_document(
        "file.bin",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) is True
    assert is_office_document("photo.png", "image/png") is False
