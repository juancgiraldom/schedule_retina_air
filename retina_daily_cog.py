#!/usr/bin/env python3
"""Convierte slices horarios RETINA (.ttf) en COG de una sola banda."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from retina_daily_netcdf import (
    NODATA_VALUE,
    SLICE_PATTERN,
    load_index_meta,
    read_slice_array,
)

COG_DRIVER_OPTIONS = [
    "COMPRESS=DEFLATE",
    "BLOCKSIZE=512",
    "OVERVIEWS=IGNORE_EXISTING",
]


def geotransform_from_meta(meta: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
    lon_len = int(meta["lonLen"])
    lat_len = int(meta["latLen"])
    lon_min = float(meta["lonMin"])
    lon_max = float(meta["lonMax"])
    lat_min = float(meta["latMin"])
    lat_max = float(meta["latMax"])
    xres = (lon_max - lon_min) / (lon_len - 1)
    yres = (lat_max - lat_min) / (lat_len - 1)
    west = lon_min - xres / 2
    north = lat_max + yres / 2
    east = west + xres * lon_len
    south = north - yres * lat_len
    return west, north, east, south, xres


def write_slice_cog(
    slice_path: Path,
    meta: Dict[str, Any],
    out_path: Path,
) -> None:
    lat_len = int(meta["latLen"])
    lon_len = int(meta["lonLen"])
    array = read_slice_array(slice_path, lat_len, lon_len)[::-1, :]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    west, north, east, south, _xres = geotransform_from_meta(meta)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bsq_path = tmp / "slice.bsq"
        hdr_path = tmp / "slice.bsq.hdr"
        bsq_path.write_bytes(array.tobytes())
        hdr_path.write_text(
            f"""ENVI
samples = {lon_len}
lines = {lat_len}
bands = 1
header offset = 0
file type = ENVI Standard
data type = 1
interleave = bsq
byte order = 0
""",
            encoding="utf-8",
        )

        cmd = [
            "gdal_translate",
            "-of",
            "COG",
            "-a_srs",
            "EPSG:4326",
            "-a_ullr",
            str(west),
            str(north),
            str(east),
            str(south),
            "-a_nodata",
            str(NODATA_VALUE),
            "-co",
            COG_DRIVER_OPTIONS[0],
            "-co",
            COG_DRIVER_OPTIONS[1],
            "-co",
            COG_DRIVER_OPTIONS[2],
            str(bsq_path),
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)


def _day_hour_from_slice(slice_path: Path) -> Tuple[str, int] | None:
    match = SLICE_PATTERN.match(slice_path.name)
    if not match:
        return None
    return match.group("date"), int(match.group("hour"))


def build_hourly_cogs(
    dataset: str,
    species: str,
    out_root: Path,
    *,
    require_hours: int = 24,
    delete_intermediate_ttf: bool = True,
) -> Dict[str, Any]:
    dataset_species = f"{dataset}-{species}"
    dataset_dir = out_root / dataset_species
    slices_dir = dataset_dir / "time-slices"
    index_path = dataset_dir / "index.json"
    output_dir = dataset_dir / "time-cogs"
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = load_index_meta(index_path)

    written_files: List[str] = []
    skipped_existing: List[str] = []
    deleted_ttf_files: List[str] = []
    failed_slices: List[Dict[str, Any]] = []
    days_with_hours: Dict[str, set[int]] = {}

    for slice_path in sorted(slices_dir.glob("*.ttf")):
        parsed = _day_hour_from_slice(slice_path)
        if parsed is None:
            continue
        day, hour = parsed
        days_with_hours.setdefault(day, set()).add(hour)

        cog_path = output_dir / f"{slice_path.stem}.tif"
        if cog_path.exists():
            skipped_existing.append(str(cog_path))
            print(f"COG ya existe, se omite: {cog_path}")
            if delete_intermediate_ttf and slice_path.exists():
                slice_path.unlink()
                deleted_ttf_files.append(str(slice_path))
                print(f"Slice intermedio eliminado: {slice_path}")
            continue

        try:
            write_slice_cog(slice_path, meta, cog_path)
            written_files.append(str(cog_path))
            print(f"COG creado: {cog_path}")
            if delete_intermediate_ttf and slice_path.exists():
                slice_path.unlink()
                deleted_ttf_files.append(str(slice_path))
                print(f"Slice intermedio eliminado: {slice_path}")
        except Exception as exc:  # pylint: disable=broad-except
            failed_slices.append(
                {
                    "slice": str(slice_path),
                    "cog_target": str(cog_path),
                    "error": str(exc),
                }
            )
            print(f"Error creando COG desde {slice_path}: {exc}")

    complete_days: List[str] = []
    incomplete_days: List[Dict[str, Any]] = []
    for day in sorted(days_with_hours):
        present_hours = sorted(days_with_hours[day])
        missing_hours = sorted(set(range(require_hours)) - set(present_hours))
        if missing_hours:
            incomplete_days.append(
                {
                    "day": day,
                    "present_hours": present_hours,
                    "missing_hours": missing_hours,
                    "count": len(present_hours),
                }
            )
        else:
            complete_days.append(day)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset,
        "species": species,
        "dataset_species": dataset_species,
        "slices_dir": str(slices_dir),
        "index_path": str(index_path),
        "output_dir": str(output_dir),
        "require_hours": require_hours,
        "delete_intermediate_ttf": delete_intermediate_ttf,
        "slices_found": sum(len(hours) for hours in days_with_hours.values()),
        "days_found": len(days_with_hours),
        "days_complete": len(complete_days),
        "days_incomplete": len(incomplete_days),
        "cogs_written": len(written_files),
        "cogs_skipped_existing": len(skipped_existing),
        "ttf_deleted": len(deleted_ttf_files),
        "complete_days": complete_days,
        "incomplete_days": incomplete_days,
        "written_files": written_files,
        "skipped_existing": skipped_existing,
        "deleted_ttf_files": deleted_ttf_files,
        "failed_slices": failed_slices,
    }

    summary_path = output_dir / "build_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Resumen COG -> creados: {len(written_files)}, "
        f"omitidos (ya existian): {len(skipped_existing)}, "
        f"ttf eliminados: {len(deleted_ttf_files)}, "
        f"fallos: {len(failed_slices)}"
    )
    print(f"Resumen guardado en: {summary_path}")
    return summary
