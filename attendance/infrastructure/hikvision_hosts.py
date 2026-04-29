from __future__ import annotations


def configured_hikvision_hosts(settings) -> list[str]:
    if settings.hikvision_device_ips:
        return [h.strip() for h in settings.hikvision_device_ips.split(",") if h.strip()]
    if settings.hikvision_device_ip:
        return [settings.hikvision_device_ip.strip()]
    return []


def resolve_hikvision_hosts(settings, camera_ip: str | None) -> list[str]:
    allowed_hosts = configured_hikvision_hosts(settings)
    if not allowed_hosts:
        return []
    if not camera_ip:
        return allowed_hosts

    allowed_set = {host.strip().lower() for host in allowed_hosts}
    requested_hosts = [h.strip() for h in camera_ip.split(",") if h.strip()]
    if not requested_hosts:
        return allowed_hosts

    normalized_requested = [host for host in requested_hosts if host.lower() in allowed_set]
    if len(normalized_requested) != len(requested_hosts):
        raise ValueError("camera_ip must match configured Hikvision hosts")
    return normalized_requested
