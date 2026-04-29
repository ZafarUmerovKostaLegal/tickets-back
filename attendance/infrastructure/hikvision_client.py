from datetime import date
from typing import Any, Optional

import httpx


def _norm(s: Any, default: str = "-") -> str:
    if s is None or (isinstance(s, str) and not str(s).strip()):
        return default
    return str(s).strip()


def _norm_int(n: Any) -> Optional[int]:
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def _parse_record_from_info(info: dict) -> dict:

    person_id = _norm(
        info.get("employeeNoString") or info.get("employeeNo") or info.get("cardNo"),
        "-",
    )
    name = _norm(info.get("name"))
    department = _norm(info.get("department"))
    time_val = _norm(info.get("time") or info.get("dateTime"), "")
    door_no = _norm_int(info.get("doorNo"))
    door_name = _norm(info.get("doorName"))
    checkpoint = door_name if door_name != "-" else (f"Door{door_no}" if door_no is not None else "Door")
    attendance_status = _norm(info.get("attendanceStatus"))
    label = _norm(info.get("label"), "")
    return {
        "person_id": person_id,
        "name": name,
        "department": department,
        "time": time_val or None,
        "checkpoint": checkpoint,
        "attendance_status": attendance_status,
        "door_no": door_no,
        "label": label,
    }


def _parse_json_response(body: dict) -> tuple[list[dict], int, int, str]:

    acs = body.get("AcsEvent") or {}
    total = int(acs.get("totalMatches", 0) or 0)
    num = int(acs.get("numOfMatches", 0) or 0)
    status_str = _norm(acs.get("responseStatusStrg", ""), "")
    info_list = acs.get("InfoList") or []
    if isinstance(info_list, dict):
        info_list = info_list.get("InfoListItem") or info_list.get("info") or []
        if not isinstance(info_list, list):
            info_list = [info_list] if info_list else []
    records: list[dict] = []
    for item in info_list:
        if isinstance(item, dict):
            records.append(_parse_record_from_info(item))
    return records, total, num, status_str


def _parse_user_from_info(info: dict) -> dict:
    employee_no = _norm(info.get("employeeNoString") or info.get("employeeNo") or info.get("userId") or info.get("id"))
    name = _norm(info.get("name"))
    user_type = _norm(info.get("userType"), "")
    gender = _norm(info.get("gender"), "")
    department = _norm(info.get("department"), "")
    return {
        "employee_no": employee_no,
        "name": name,
        "department": department,
        "user_type": user_type or None,
        "gender": gender or None,
    }


def _parse_users_json_response(body: dict) -> tuple[list[dict], int, int, str]:

    root = body.get("UserInfoSearch") or body.get("UserInfoSearchResult") or body.get("UserInfo") or {}
    total = int(root.get("totalMatches", 0) or 0)
    num = int(root.get("numOfMatches", 0) or 0)
    status_str = _norm(root.get("responseStatusStrg", ""), "")

    info_list: Any = root.get("UserInfo") or root.get("UserInfoList") or root.get("InfoList") or []
    if isinstance(info_list, dict):

        info_list = (
            info_list.get("UserInfo")
            or info_list.get("UserInfoItem")
            or info_list.get("InfoListItem")
            or info_list.get("info")
            or []
        )
    if not isinstance(info_list, list):
        info_list = [info_list] if info_list else []

    users: list[dict] = []
    for item in info_list:
        if isinstance(item, dict):
            users.append(_parse_user_from_info(item))
    return users, total, num, status_str


def get_attendance_from_device(
    host: str,
    port: int = 80,
    user: str = "admin",
    password: str = "",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    max_records: int = 2000,
    timeout: float = 60.0,
    use_basic_auth: bool = False,
) -> dict:
    if not host or not user:
        return {
            "records": [],
            "error": "Устройство не настроено (host, user)",
            "camera_ip": host or "-",
        }

    today = date.today()
    date_from = date_from or today
    date_to = date_to or date_from
    max_records = max(1, min(10000, max_records))

    start_time = f"{date_from.isoformat()}T00:00:00+00:00"
    end_time = f"{date_to.isoformat()}T23:59:59+00:00"

    base_url = f"http://{host}:{port}"
    path = "/ISAPI/AccessControl/AcsEvent"
    auth: httpx.Auth = (
        httpx.BasicAuth(user, password or "")
        if use_basic_auth
        else httpx.DigestAuth(user, password or "")
    )
    all_records: list[dict] = []
    use_json = True
    position = 0

    with httpx.Client(timeout=timeout, auth=auth) as client:
        while len(all_records) < max_records:
            page_size = min(10, max_records - len(all_records))
            body_json = {
                "AcsEventCond": {
                    "searchID": "1",
                    "searchResultPosition": position,
                    "maxResults": page_size,
                    "major": 5,
                    "minor": 0,
                    "startTime": start_time,
                    "endTime": end_time,
                }
            }
            try:
                url = f"{base_url}{path}?format=json" if use_json else f"{base_url}{path}?format=xml"
                if use_json:
                    resp = client.post(
                        url,
                        json=body_json,
                        headers={"Accept": "application/json"},
                    )
                else:
                    resp = client.post(url, json=body_json, headers={"Accept": "application/json"})
            except Exception as e:
                err_msg = str(e).strip().lower()
                if "timed out" in err_msg or "timeout" in err_msg:
                    return {
                        "records": all_records,
                        "error": (
                            "Таймаут подключения к устройству. Проверьте: "
                            "устройство доступно с сервера, IP/порт/логин/пароль в .env."
                        ),
                        "camera_ip": host,
                    }
                return {"records": all_records, "error": f"Ошибка подключения: {e}", "camera_ip": host}

            if resp.status_code != 200:
                if use_json and resp.status_code == 400 and "badJsonFormat" in (resp.text or ""):
                    use_json = False
                    continue
                return {
                    "records": all_records,
                    "error": f"HTTP {resp.status_code}: {(resp.text or '')[:200]}",
                    "camera_ip": host,
                }

            try:
                data = resp.json()
                records, total, num, status_str = _parse_json_response(data)
            except Exception as e:
                return {"records": all_records, "error": f"Ошибка разбора ответа: {e}", "camera_ip": host}

            all_records.extend(records)
            if status_str != "MORE" or num == 0:
                break
            position += num
            if position >= total:
                break

    return {"records": all_records, "error": None, "camera_ip": host}


def get_attendance_from_devices(
    hosts: list[str],
    port: int,
    user: str,
    password: str,
    date_from: Optional[date],
    date_to: Optional[date],
    max_records_per_device: int,
    timeout: float,
) -> list[dict]:

    results: list[dict] = []
    for host in hosts:
        host = host.strip()
        if not host:
            continue
        result = get_attendance_from_device(
            host=host,
            port=port,
            user=user,
            password=password,
            date_from=date_from,
            date_to=date_to,
            max_records=max_records_per_device,
            timeout=timeout,
        )
        results.append(result)
    return results


def get_users_from_device(
    host: str,
    port: int = 80,
    user: str = "admin",
    password: str = "",
    max_users: int = 2000,
    timeout: float = 60.0,
    use_basic_auth: bool = False,
    name: Optional[str] = None,
    employee_no: Optional[str] = None,
) -> dict:

    if not host or not user:
        return {"users": [], "error": "Устройство не настроено (host, user)", "camera_ip": host or "-"}

    max_users = max(1, min(20000, max_users))
    base_url = f"http://{host}:{port}"
    path = "/ISAPI/AccessControl/UserInfo/Search"
    auth: httpx.Auth = (
        httpx.BasicAuth(user, password or "")
        if use_basic_auth
        else httpx.DigestAuth(user, password or "")
    )

    all_users: list[dict] = []
    position = 0

    with httpx.Client(timeout=timeout, auth=auth) as client:
        while len(all_users) < max_users:
            page_size = min(50, max_users - len(all_users))

            cond: dict[str, Any] = {
                "searchID": "1",
                "searchResultPosition": position,
                "maxResults": page_size,
            }

            if name:
                cond["name"] = name
            if employee_no:
                cond["employeeNo"] = employee_no

            body_json = {"UserInfoSearchCond": cond}
            try:
                url = f"{base_url}{path}?format=json"
                resp = client.post(url, json=body_json, headers={"Accept": "application/json"})
            except Exception as e:
                err_msg = str(e).strip().lower()
                if "timed out" in err_msg or "timeout" in err_msg:
                    return {
                        "users": all_users,
                        "error": (
                            "Таймаут подключения к устройству. Проверьте: "
                            "устройство доступно с сервера, IP/порт/логин/пароль в .env."
                        ),
                        "camera_ip": host,
                    }
                return {"users": all_users, "error": f"Ошибка подключения: {e}", "camera_ip": host}

            if resp.status_code != 200:
                return {
                    "users": all_users,
                    "error": f"HTTP {resp.status_code}: {(resp.text or '')[:200]}",
                    "camera_ip": host,
                }

            try:
                data = resp.json()
                users, total, num, status_str = _parse_users_json_response(data)
            except Exception as e:
                return {"users": all_users, "error": f"Ошибка разбора ответа: {e}", "camera_ip": host}

            all_users.extend(users)
            if status_str != "MORE" or num == 0:
                break
            position += num
            if position >= total:
                break


    if name:
        all_users = [u for u in all_users if name.lower() in (u.get("name") or "").lower()]
    if employee_no:
        all_users = [u for u in all_users if (u.get("employee_no") or "").strip() == employee_no.strip()]

    return {"users": all_users, "error": None, "camera_ip": host}


def get_users_from_devices(
    hosts: list[str],
    port: int,
    user: str,
    password: str,
    max_users_per_device: int,
    timeout: float,
    name: Optional[str] = None,
    employee_no: Optional[str] = None,
) -> list[dict]:
    results: list[dict] = []
    for host in hosts:
        host = host.strip()
        if not host:
            continue
        result = get_users_from_device(
            host=host,
            port=port,
            user=user,
            password=password,
            max_users=max_users_per_device,
            timeout=timeout,
            name=name,
            employee_no=employee_no,
        )
        results.append(result)
    return results
