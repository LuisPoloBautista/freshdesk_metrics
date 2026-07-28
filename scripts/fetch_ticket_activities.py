#!/usr/bin/env python3
"""
Download Freshdesk ticket activity exports into the dashboard data folder.

Freshdesk's export endpoint returns a short-lived S3 URL. This script requests
that URL for each date, downloads it immediately, and saves the resulting
activities_*.json file where the Streamlit dashboard can read it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_DOMAIN = "igniteonline.freshdesk.com"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = DEFAULT_OUTPUT_DIR / ".env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def describe_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return (
                "HTTP 401 Unauthorized. Revisa que FRESHDESK_API_KEY este configurada "
                "en esta misma terminal y que pertenezca a la cuenta Freshdesk correcta."
            )
        if exc.code == 403:
            return (
                "HTTP 403 Forbidden. La API key existe, pero no tiene permiso para "
                "este export o el dominio no corresponde."
            )
        return f"HTTP {exc.code}: {exc.reason}"
    return str(exc)


def parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"fecha invalida: {value!r}. Usa formato YYYY-MM-DD."
        ) from exc


def iter_dates(start: date, end: date) -> Iterable[date]:
    if end < start:
        raise ValueError("--end-date no puede ser menor que --start-date")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def auth_header(api_key: str) -> str:
    if api_key.lower().startswith(("basic ", "bearer ")):
        return api_key
    if ":" in api_key:
        token = base64.b64encode(api_key.encode("utf-8")).decode("ascii")
        return f"Basic {token}"
    token = base64.b64encode(f"{api_key}:X".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def read_json_url(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> dict:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset)
    return json.loads(body)


def download_bytes(url: str, timeout: int = 120) -> bytes:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def output_name_from_export_url(export_url: str, fallback_day: date) -> str:
    parsed = urlparse(export_url)
    name = Path(parsed.path).name
    if name.startswith("activities_") and name.endswith(".json"):
        return name
    return f"activities_export_{fallback_day.day}_{fallback_day.month}_{fallback_day.year}.json"


def existing_export_for_day(output_dir: Path, day: date) -> Path | None:
    matches = sorted(output_dir.glob(f"activities_*_{day.day}_{day.month}_{day.year}.json"))
    return matches[0] if matches else None


def validate_activities_json(payload: bytes, source: str) -> dict:
    try:
        data = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError:
        data = json.loads(payload.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} no devolvio JSON valido: {exc}") from exc

    if not isinstance(data, dict) or "activities_data" not in data:
        raise ValueError(f"{source} no contiene la llave esperada 'activities_data'")
    if not isinstance(data["activities_data"], list):
        raise ValueError(f"{source} contiene 'activities_data', pero no es una lista")
    return data


def fetch_export_url(domain: str, day: date, api_key: str, timeout: int) -> str:
    endpoint = (
        f"https://{domain}/api/v2/export/ticket_activities"
        f"?created_at={quote(day.isoformat())}"
    )
    data = read_json_url(endpoint, {"Authorization": auth_header(api_key)}, timeout=timeout)
    exports = data.get("export")
    if not isinstance(exports, list) or not exports:
        raise ValueError(f"Freshdesk no devolvio export para {day.isoformat()}: {data}")

    export_url = exports[0].get("url") if isinstance(exports[0], dict) else None
    if not export_url:
        raise ValueError(f"Freshdesk devolvio export sin URL para {day.isoformat()}: {data}")
    return export_url


def save_export(
    *,
    day: date,
    domain: str,
    api_key: str,
    output_dir: Path,
    overwrite: bool,
    timeout: int,
    pause_seconds: float,
) -> tuple[Path, int, bool]:
    existing = existing_export_for_day(output_dir, day)
    if existing and not overwrite:
        return existing, -1, False

    export_url = fetch_export_url(domain, day, api_key, timeout)
    output_path = output_dir / output_name_from_export_url(export_url, day)

    if output_path.exists() and not overwrite:
        return output_path, -1, False

    payload = download_bytes(export_url, timeout=timeout)
    data = validate_activities_json(payload, export_url)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    if pause_seconds:
        time.sleep(pause_seconds)

    return output_path, len(data["activities_data"]), True


def build_parser() -> argparse.ArgumentParser:
    load_env_file(DEFAULT_ENV_FILE)

    parser = argparse.ArgumentParser(
        description="Descarga exports diarios de ticket_activities desde Freshdesk."
    )
    parser.add_argument(
        "--date",
        type=parse_iso_date,
        help="Fecha unica a descargar, por ejemplo 2026-07-08.",
    )
    parser.add_argument(
        "--start-date",
        type=parse_iso_date,
        help="Primera fecha del rango, por ejemplo 2026-07-01.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_iso_date,
        help="Ultima fecha del rango. Si se omite, usa --start-date.",
    )
    parser.add_argument(
        "--domain",
        default=os.getenv("FRESHDESK_DOMAIN", DEFAULT_DOMAIN),
        help=f"Dominio Freshdesk. Default: {DEFAULT_DOMAIN}",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("FRESHDESK_API_KEY"),
        help="API key de Freshdesk. Mejor usa la variable FRESHDESK_API_KEY.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Carpeta donde guardar los activities_*.json. Default: raiz del proyecto.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescribe archivos existentes.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout HTTP en segundos.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.5,
        help="Pausa entre descargas cuando usas un rango.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.api_key:
        parser.error("falta FRESHDESK_API_KEY o --api-key")

    if args.date and (args.start_date or args.end_date):
        parser.error("usa --date o --start-date/--end-date, no ambos")

    if args.date:
        start = end = args.date
    elif args.start_date:
        start = args.start_date
        end = args.end_date or args.start_date
    else:
        parser.error("indica --date o --start-date")

    output_dir = args.output_dir.resolve()
    failures = 0

    for day in iter_dates(start, end):
        try:
            path, count, downloaded = save_export(
                day=day,
                domain=args.domain,
                api_key=args.api_key,
                output_dir=output_dir,
                overwrite=args.overwrite,
                timeout=args.timeout,
                pause_seconds=args.pause_seconds,
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"[ERROR] {day.isoformat()}: {describe_error(exc)}", file=sys.stderr)
            continue

        rel_path = path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path
        if downloaded:
            print(f"[OK] {day.isoformat()} -> {rel_path} ({count} actividades)")
        else:
            print(f"[SKIP] {day.isoformat()} -> {rel_path} ya existe")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
