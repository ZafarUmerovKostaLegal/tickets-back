from support.service_path import ensure_service_in_path


def test_build_tt_upsert_payload_defaults_tt_role_from_public_profile():
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import build_tt_upsert_payload_from_auth_record

    payload = build_tt_upsert_payload_from_auth_record(
        {
            "id": 12,
            "email": "user@example.com",
            "displayName": "User",
            "position": "Associate",
        },
        default_tt_role="user",
    )
    assert payload is not None
    assert payload["auth_user_id"] == 12
    assert payload["role"] == "user"
    assert payload["position"] is None


def test_build_tt_upsert_payload_prefers_auth_tt_role():
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import build_tt_upsert_payload_from_auth_record

    payload = build_tt_upsert_payload_from_auth_record(
        {
            "id": 3,
            "email": "m@example.com",
            "timeTrackingRole": "manager",
            "position": "Partner",
        }
    )
    assert payload is not None
    assert payload["role"] == "manager"
