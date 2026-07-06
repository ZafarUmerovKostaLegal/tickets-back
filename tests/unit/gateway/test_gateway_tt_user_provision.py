from presentation.time_tracking_user_provision import build_tt_upsert_payload_from_auth_record


def test_build_tt_upsert_payload_defaults_tt_role_from_public_profile():
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
    assert payload["position"] == "Associate"


def test_build_tt_upsert_payload_prefers_auth_tt_role():
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
