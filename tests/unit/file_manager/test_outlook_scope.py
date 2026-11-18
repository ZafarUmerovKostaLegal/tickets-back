from support.service_path import ensure_service_in_path


def test_outlook_whitelist():
    ensure_service_in_path("file_manager")
    from presentation.outlook_scope import outlook_path_allowed

    assert outlook_path_allowed("GET", "/api/v1/files/clients")
    assert outlook_path_allowed("GET", "/api/v1/files/matters/abc/browse")
    assert outlook_path_allowed("POST", "/api/v1/files/matters/abc/folders")
    assert outlook_path_allowed("POST", "/api/v1/files/matters/abc/documents")
    assert not outlook_path_allowed("GET", "/api/v1/files/trash")
    assert not outlook_path_allowed("GET", "/api/v1/files/matrix")
    assert not outlook_path_allowed("DELETE", "/api/v1/files/matters/abc/folders")


def test_outlook_pane_routes_allowed():
    ensure_service_in_path("file_manager")
    from presentation.outlook_scope import outlook_path_allowed

    assert outlook_path_allowed("POST", "/api/v1/files/mail/lookup")
    assert outlook_path_allowed("GET", "/api/v1/files/mail/suggestions")
    assert outlook_path_allowed("GET", "/api/v1/files/search")
    assert outlook_path_allowed("GET", "/api/v1/files/documents/d1/content")
    assert not outlook_path_allowed("DELETE", "/api/v1/files/documents/d1")
    assert not outlook_path_allowed("GET", "/api/v1/files/audit")
