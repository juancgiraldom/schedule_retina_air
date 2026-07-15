#!/usr/bin/env python3
"""
Descarga slices horarios RETINA hacia atras durante 1 anio.

Empieza desde ayer (-24 h) respecto a la hora actual UTC.

Ejemplo:
  python download_retina_year_back.py --dataset MADRID2 --species NO2
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from retina_daily_netcdf import build_time_day_netcdfs


BASE_URL = "https://files.isardsat.co.uk/aq-retina"
REQUEST_TIMEOUT = 120
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 1.5


@dataclass
class DownloadStats:
    ok: int = 0
    exists: int = 0
    missing: int = 0
    errors: int = 0


def utc_hour_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_request_log(
    request_log: List[Dict[str, Any]],
    *,
    url: str,
    kind: str,
    result: str,
    requested_at_utc: str,
    attempt: int = 1,
    status_code: Optional[int] = None,
    bytes_count: Optional[int] = None,
    duration_ms: Optional[int] = None,
    slice_time: Optional[str] = None,
    error: Optional[str] = None,
    log_path: Optional[Path] = None,
    run_meta: Optional[Dict[str, str]] = None,
) -> None:
    entry: Dict[str, Any] = {
        "requested_at_utc": requested_at_utc,
        "url": url,
        "kind": kind,
        "result": result,
        "attempt": attempt,
    }
    if slice_time is not None:
        entry["slice_time"] = slice_time
    if status_code is not None:
        entry["status_code"] = status_code
    if bytes_count is not None:
        entry["bytes"] = bytes_count
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if error is not None:
        entry["error"] = error
    if run_meta:
        entry = {**run_meta, **entry}
    request_log.append(entry)
    if log_path is not None:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fetch_json_with_retry(
    url: str,
    request_log: List[Dict[str, Any]],
    kind: str = "index",
    slice_time: Optional[str] = None,
    log_path: Optional[Path] = None,
    run_meta: Optional[Dict[str, str]] = None,
) -> Dict:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        requested_at = utc_now_iso()
        started = time.monotonic()
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            duration_ms = int((time.monotonic() - started) * 1000)
            response.raise_for_status()
            append_request_log(
                request_log,
                url=url,
                kind=kind,
                result="ok",
                requested_at_utc=requested_at,
                attempt=attempt,
                status_code=response.status_code,
                duration_ms=duration_ms,
                slice_time=slice_time,
                log_path=log_path,
                run_meta=run_meta,
            )
            return response.json()
        except Exception as exc:  # pylint: disable=broad-except
            duration_ms = int((time.monotonic() - started) * 1000)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            print(f"Error al obtener JSON: {url} ({exc})")
            append_request_log(
                request_log,
                url=url,
                kind=kind,
                result="error",
                requested_at_utc=requested_at,
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                slice_time=slice_time,
                error=str(exc),
                log_path=log_path,
                run_meta=run_meta,
            )
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    raise RuntimeError(f"No se pudo obtener JSON: {url} ({last_exc})") from last_exc


def download_binary_with_retry(
    url: str,
    request_log: List[Dict[str, Any]],
    slice_time: Optional[str] = None,
    log_path: Optional[Path] = None,
    run_meta: Optional[Dict[str, str]] = None,
) -> Tuple[int, bytes]:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        requested_at = utc_now_iso()
        started = time.monotonic()
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            duration_ms = int((time.monotonic() - started) * 1000)
            if response.status_code == 404:
                append_request_log(
                    request_log,
                    url=url,
                    kind="slice",
                    result="missing",
                    requested_at_utc=requested_at,
                    attempt=attempt,
                    status_code=404,
                    duration_ms=duration_ms,
                    slice_time=slice_time,
                    log_path=log_path,
                    run_meta=run_meta,
                )
                return 404, b""
            response.raise_for_status()
            append_request_log(
                request_log,
                url=url,
                kind="slice",
                result="ok",
                requested_at_utc=requested_at,
                attempt=attempt,
                status_code=response.status_code,
                bytes_count=len(response.content),
                duration_ms=duration_ms,
                slice_time=slice_time,
                log_path=log_path,
                run_meta=run_meta,
            )
            return response.status_code, response.content
        except Exception as exc:  # pylint: disable=broad-except
            duration_ms = int((time.monotonic() - started) * 1000)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            print(f"Error al descargar binario: {url} ({exc})")
            append_request_log(
                request_log,
                url=url,
                kind="slice",
                result="error",
                requested_at_utc=requested_at,
                attempt=attempt,
                status_code=status_code,
                duration_ms=duration_ms,
                slice_time=slice_time,
                error=str(exc),
                log_path=log_path,
                run_meta=run_meta,
            )
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    raise RuntimeError(f"No se pudo descargar binario: {url} ({last_exc})") from last_exc


def build_hour_list(start_utc: datetime, days_back: int) -> List[datetime]:
    end_utc = start_utc - timedelta(days=days_back)
    total_hours = int((start_utc - end_utc).total_seconds() // 3600)
    return [start_utc - timedelta(hours=h) for h in range(total_hours + 1)]


def save_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(
    dataset: str,
    species: str,
    out_root: Path,
    days_back: int,
    refresh_index_every: int,
    stop_after_missing: int = 2,
    start_hours_back: int = 24,
    build_daily_netcdf: bool = True,
    require_hours_per_day: int = 24,
) -> Dict[str, Any]:
    dataset_species = f"{dataset}-{species}"
    out_dir = out_root / dataset_species
    slices_dir = out_dir / "time-slices"
    slices_dir.mkdir(parents=True, exist_ok=True)

    now_utc = utc_hour_floor(datetime.now(timezone.utc))
    start_utc = now_utc - timedelta(hours=max(0, start_hours_back))
    hours = build_hour_list(start_utc, days_back=days_back)

    stats = DownloadStats()
    log: List[Dict] = []
    request_log: List[Dict[str, Any]] = []
    requests_log_path = out_dir / "requests_log.jsonl"
    run_meta = {
        "run_started_at_utc": utc_now_iso(),
        "dataset_species": dataset_species,
    }
    channel = None
    meta_index = None
    consecutive_missing = 0
    stopped_early = False
    stop_reason = ""
    hours_processed = 0

    for i, hour_dt in enumerate(hours, start=1):
        hours_processed = i
        if i == 1 or i % refresh_index_every == 0:
            index_url = f"{BASE_URL}/{dataset_species}/index.json"
            index_data = fetch_json_with_retry(
                index_url,
                request_log=request_log,
                kind="index",
                log_path=requests_log_path,
                run_meta=run_meta,
            )
            channel = index_data.get("current")
            meta_index = index_data
            if not channel:
                raise RuntimeError("El index.json no contiene la clave 'current'.")
            save_json(out_dir / "index.json", index_data)

        stamp = hour_dt.strftime("%Y-%m-%d--%H-%M-%S")
        local_file = slices_dir / f"{stamp}.ttf"

        if local_file.exists():
            stats.exists += 1
            consecutive_missing = 0
            log.append(
                {
                    "slice_time": hour_dt.isoformat(),
                    "status": "exists",
                    "file": str(local_file),
                }
            )
            continue

        remote_url = (
            f"{BASE_URL}/{dataset_species}/{dataset_species}-{channel}/time-slices/{stamp}.ttf"
        )

        try:
            status, content = download_binary_with_retry(
                remote_url,
                request_log=request_log,
                slice_time=hour_dt.isoformat(),
                log_path=requests_log_path,
                run_meta=run_meta,
            )
            if status == 404:
                stats.missing += 1
                consecutive_missing += 1
                print(f"No se pudo descargar (404): {remote_url}")
                log.append(
                    {
                        "slice_time": hour_dt.isoformat(),
                        "status": "missing",
                        "url": remote_url,
                    }
                )
                if stop_after_missing > 0 and consecutive_missing >= stop_after_missing:
                    stopped_early = True
                    stop_reason = (
                        f"{consecutive_missing} peticiones consecutivas sin resultado (404)"
                    )
                    break
                continue

            consecutive_missing = 0
            local_file.write_bytes(content)
            stats.ok += 1
            print(f"Descargado OK: {remote_url} ({len(content)} bytes)")
            log.append(
                {
                    "slice_time": hour_dt.isoformat(),
                    "status": "ok",
                    "bytes": len(content),
                    "file": str(local_file),
                    "url": remote_url,
                }
            )
        except Exception as exc:  # pylint: disable=broad-except
            stats.errors += 1
            print(f"No se pudo descargar: {remote_url} ({exc})")
            log.append(
                {
                    "slice_time": hour_dt.isoformat(),
                    "status": "error",
                    "error": str(exc),
                    "url": remote_url,
                }
            )

    manifest = {
        "base_url": BASE_URL,
        "dataset": dataset,
        "species": species,
        "dataset_species": dataset_species,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "now_utc": now_utc.isoformat(),
        "start_hours_back": start_hours_back,
        "start_utc": hours[0].isoformat(),
        "end_utc": hours[-1].isoformat(),
        "days_back": days_back,
        "stop_after_missing": stop_after_missing,
        "hours_planned": len(hours),
        "hours_processed": hours_processed,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
        "stats": {
            "downloaded_ok": stats.ok,
            "already_exists": stats.exists,
            "missing_404": stats.missing,
            "errors": stats.errors,
        },
        "index_snapshot": meta_index if meta_index is not None else {},
        "requests_log": str(requests_log_path),
        "requests_log_entries_this_run": len(request_log),
        "records": log,
    }

    save_json(out_dir / "manifest.json", manifest)

    print(f"Descarga completada en: {out_dir}")
    print(
        f"Log de peticiones (acumulativo): {requests_log_path} "
        f"({len(request_log)} entradas en esta ejecucion)"
    )
    print(
        "Resumen -> "
        f"ok: {stats.ok}, exists: {stats.exists}, missing: {stats.missing}, errors: {stats.errors}"
    )
    if stopped_early:
        print(f"Parada anticipada: {stop_reason}")

    netcdf_summary: Optional[Dict[str, Any]] = None
    if build_daily_netcdf:
        print("Construyendo NetCDF diarios...")
        netcdf_summary = build_time_day_netcdfs(
            dataset=dataset,
            species=species,
            out_root=out_root,
            require_hours=require_hours_per_day,
        )
        manifest["netcdf_summary"] = netcdf_summary
        save_json(out_dir / "manifest.json", manifest)

    result = {
        "out_dir": str(out_dir),
        "manifest_path": str(out_dir / "manifest.json"),
        "requests_log_path": str(requests_log_path),
        "stats": manifest["stats"],
        "netcdf_summary": netcdf_summary,
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Descarga time-slices RETINA desde ayer hacia atras."
    )
    parser.add_argument("--dataset", required=True, help="Ciudad/dataset. Ej: MADRID2")
    parser.add_argument("--species", required=True, help="Especie. Ej: NO2")
    parser.add_argument(
        "--out",
        default="data",
        help="Carpeta de salida base (por defecto: data).",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=365,
        help="Dias hacia atras desde el inicio (por defecto: 365).",
    )
    parser.add_argument(
        "--start-hours-back",
        type=int,
        default=24,
        help="Horas hacia atras desde ahora para empezar (por defecto: 24, ayer).",
    )
    parser.add_argument(
        "--refresh-index-every",
        type=int,
        default=24,
        help="Refrescar index.json cada N horas procesadas (por defecto: 24).",
    )
    parser.add_argument(
        "--stop-after-missing",
        type=int,
        default=2,
        help="Parar tras N peticiones consecutivas sin resultado (404). 0 desactiva la parada.",
    )
    parser.add_argument(
        "--no-build-daily-netcdf",
        action="store_true",
        help="No construir NetCDF diarios al finalizar.",
    )
    parser.add_argument(
        "--require-hours-per-day",
        type=int,
        default=24,
        help="Horas requeridas por dia para generar NetCDF (por defecto: 24).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        dataset=args.dataset.strip(),
        species=args.species.strip(),
        out_root=Path(args.out),
        days_back=args.days_back,
        refresh_index_every=max(1, args.refresh_index_every),
        stop_after_missing=max(0, args.stop_after_missing),
        start_hours_back=max(0, args.start_hours_back),
        build_daily_netcdf=not args.no_build_daily_netcdf,
        require_hours_per_day=max(1, args.require_hours_per_day),
    )
