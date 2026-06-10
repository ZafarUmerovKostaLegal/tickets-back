"""Extract Harvest projects without time entries into a separate CSV.

Time report rows always belong to projects that have hours. "Empty" projects
are those present in a full Harvest Projects export but absent from the time
report (or with Total Hours = 0 in the projects export).

Usage:
  python scripts/extract_harvest_empty_projects.py \\
    --time-report harvest_time_report_from2018-01-08to2026-06-09.csv \\
    --projects-file harvest_project_list.csv \\
    --output harvest_projects_empty_only.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

TT_ROOT = Path(__file__).resolve().parents[1]
if TT_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, TT_ROOT.as_posix())


def _configure_stdio_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _field(row: dict, *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name])
        for key in row:
            if _norm(key).casefold() == _norm(name).casefold():
                return str(row[key] or "")
    return ""


def _to_hours(s: str | None) -> float:
    raw = (s or "").strip().replace(",", "")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _parse_currency(raw: str) -> str:
    s = _norm(raw)
    if not s:
        return "USD"
    if " - " in s:
        part = s.split(" - ")[-1].strip()
        if part:
            return part[:10]
    return s[:10]


@dataclass(frozen=True)
class ProjectRow:
    client_name: str
    project_name: str
    project_code: str
    currency: str
    project_status: str
    total_hours: float | None


def _load_time_report_projects(path: Path) -> tuple[set[tuple[str, str]], dict[str, ProjectRow]]:
    pairs: set[tuple[str, str]] = set()
    meta: dict[str, ProjectRow] = {}
    client_currency: dict[str, Counter[str]] = defaultdict(Counter)

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            client = _norm(_field(row, "Client"))
            project = _norm(_field(row, "Project"))
            if not client or not project:
                continue
            key = (client, project)
            pairs.add(key)
            cur = _parse_currency(_field(row, "Currency"))
            code = _norm(_field(row, "Project Code"))
            client_currency[client][cur] += 1
            pk = f"{client}\0{project}"
            if pk not in meta:
                meta[pk] = ProjectRow(
                    client_name=client,
                    project_name=project,
                    project_code=code,
                    currency=cur,
                    project_status="",
                    total_hours=None,
                )

    for pk, entry in meta.items():
        client = entry.client_name
        if client in client_currency and client_currency[client]:
            entry_cur = client_currency[client].most_common(1)[0][0]
            meta[pk] = ProjectRow(
                client_name=entry.client_name,
                project_name=entry.project_name,
                project_code=entry.project_code,
                currency=entry_cur,
                project_status=entry.project_status,
                total_hours=entry.total_hours,
            )

    return pairs, meta


def _load_catalog_projects(path: Path) -> list[ProjectRow]:
    out: list[ProjectRow] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            client = _norm(_field(row, "Client"))
            project = _norm(_field(row, "Project"))
            if not client or not project:
                continue
            total_raw = _field(row, "Total Hours", "Hours", "Total hours")
            total_hours = _to_hours(total_raw) if total_raw else None
            currency_raw = _field(row, "Currency", "Budget By", "Budget by")
            status = _norm(_field(row, "Project Status", "Status", "Project State"))
            out.append(
                ProjectRow(
                    client_name=client,
                    project_name=project,
                    project_code=_norm(_field(row, "Project Code")),
                    currency=_parse_currency(currency_raw),
                    project_status=status,
                    total_hours=total_hours,
                )
            )
    return out


def _write_projects_csv(path: Path, rows: list[ProjectRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=("Client", "Project", "Project Code", "Currency", "Project Status", "Total Hours"),
        )
        writer.writeheader()
        for r in sorted(rows, key=lambda x: (_norm(x.client_name).casefold(), _norm(x.project_name).casefold())):
            writer.writerow(
                {
                    "Client": r.client_name,
                    "Project": r.project_name,
                    "Project Code": r.project_code,
                    "Currency": r.currency,
                    "Project Status": r.project_status,
                    "Total Hours": "" if r.total_hours is None else r.total_hours,
                }
            )


def main() -> int:
    _configure_stdio_utf8()
    ap = argparse.ArgumentParser(description="Extract Harvest projects without time entries.")
    ap.add_argument(
        "--time-report",
        type=Path,
        default=TT_ROOT / "harvest_time_report_from2018-01-08to2026-06-09.csv",
        help="Harvest time report CSV (projects with hours).",
    )
    ap.add_argument(
        "--projects-file",
        type=Path,
        default=None,
        help="Full Harvest Projects export CSV (includes projects without hours).",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=TT_ROOT / "harvest_projects_empty_only.csv",
        help="Output CSV for empty projects.",
    )
    ap.add_argument(
        "--with-hours-output",
        type=Path,
        default=TT_ROOT / "harvest_projects_with_hours.csv",
        help="Also write projects found in the time report.",
    )
    args = ap.parse_args()

    if not args.time_report.is_file():
        print(f"Time report not found: {args.time_report}", file=sys.stderr)
        return 1

    time_pairs, time_meta = _load_time_report_projects(args.time_report)
    with_hours = list(time_meta.values())
    _write_projects_csv(args.with_hours_output, with_hours)
    print(f"Projects with hours in time report: {len(with_hours)} -> {args.with_hours_output}")

    catalog_path = args.projects_file
    if catalog_path is None:
        for candidate in (
            TT_ROOT / "harvest_project_list.csv",
            Path.home() / "Downloads" / "harvest_project_list.csv",
        ):
            if candidate.is_file():
                catalog_path = candidate
                break

    if catalog_path is None or not catalog_path.is_file():
        print(
            "No --projects-file provided and no harvest_project_list.csv found.\n"
            "Time report alone contains only projects WITH hours (empty count = 0).\n"
            "Export full Harvest Projects list (~904) and pass --projects-file.",
            file=sys.stderr,
        )
        _write_projects_csv(args.output, [])
        print(f"Wrote empty template: {args.output}")
        return 0

    catalog = _load_catalog_projects(catalog_path)
    print(f"Catalog projects: {len(catalog)} from {catalog_path}")

    empty: list[ProjectRow] = []
    seen: set[tuple[str, str]] = set()
    for entry in catalog:
        key = (entry.client_name, entry.project_name)
        if key in seen:
            continue
        seen.add(key)
        in_time_report = key in time_pairs
        zero_hours = entry.total_hours is not None and entry.total_hours <= 0
        if not in_time_report or zero_hours:
            empty.append(entry)

    _write_projects_csv(args.output, empty)
    print(f"Empty projects (no entries / not in time report): {len(empty)} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
