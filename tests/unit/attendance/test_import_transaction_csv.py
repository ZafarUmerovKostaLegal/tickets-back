from application.import_transaction_csv import build_csv_name_map, parse_transaction_csv_rows


def test_build_csv_name_map_prefers_short_names_and_overrides():
    users = [
        {"employee_no": "00000002", "name": "Shoira"},
        {"employee_no": "00000036", "name": "Shoira Mirrakhimova"},
        {"employee_no": "00000025", "name": "Iroda Tokhirova"},
        {"employee_no": "00000057", "name": "Iroda  Bekmurodova"},
        {"employee_no": "00000019", "name": "Diyorbek"},
        {"employee_no": "00000015", "name": "AlinaA"},
        {"employee_no": "00000034", "name": "Amal Yuldashev"},
    ]
    mapped = build_csv_name_map(users)
    assert mapped["Shoira"][0] == "00000036"
    assert mapped["Iroda"][0] == "00000025"
    assert mapped["Diyorbek"][0] == "00000019"
    assert mapped["AlinaA"][0] == "00000015"
    assert mapped["Amal"][0] == "00000034"


def test_parse_transaction_csv_rows_maps_and_dedupes():
    users = [
        {"employee_no": "00000019", "name": "Diyorbek"},
        {"employee_no": "00000036", "name": "Shoira Mirrakhimova"},
    ]
    name_map = build_csv_name_map(users)
    text = """Report Title
First Name,Date,Week,Time,
Diyorbek,2026-03-02,Monday,09:01,
Diyorbek,2026-03-02,Monday,09:01,
Shoira,2026-03-02,Monday,08:15,
Unknown,2026-03-02,Monday,10:00,
"""
    rows, result = parse_transaction_csv_rows(text, name_map, camera_ip="192.168.230.48")
    assert result.rows_read == 4
    assert result.rows_mapped == 3
    assert result.rows_unmapped == 1
    assert result.unmapped_names["Unknown"] == 1
    assert len(rows) == 2  # duplicate Diyorbek same second collapsed
    assert {r["person_id"] for r in rows} == {"00000019", "00000036"}
