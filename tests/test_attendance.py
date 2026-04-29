

import pytest

from service_path import ensure_service_in_path


@pytest.mark.skip(reason="Требует PostgreSQL и изолированный path")
async def test_attendance_health(attendance_client):

    r = await attendance_client.get("/health")
    assert r.status_code in (200, 503)


@pytest.mark.skip(reason="Требует изолированный path")
async def test_attendance_hikvision_no_config(attendance_client):

    r = await attendance_client.get("/hikvision/attendance")
    assert r.status_code in (200, 400)


def test_hikvision_host_override_is_limited_to_configured_hosts():

    ensure_service_in_path("attendance")

    from infrastructure.hikvision_hosts import resolve_hikvision_hosts

    settings = type(
        "SettingsStub",
        (),
        {
            "hikvision_device_ips": "10.0.0.10,10.0.0.11",
            "hikvision_device_ip": "",
        },
    )()

    assert resolve_hikvision_hosts(settings, None) == ["10.0.0.10", "10.0.0.11"]
    assert resolve_hikvision_hosts(settings, "10.0.0.11") == ["10.0.0.11"]

    with pytest.raises(ValueError, match="camera_ip must match configured Hikvision hosts"):
        resolve_hikvision_hosts(settings, "10.0.0.99")
