#!/usr/bin/env python3
"""Agrupa slices horarios RETINA en NetCDF diarios."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import xarray as xr

SLICE_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})--(?P<hour>\d{2})-\d{2}-\d{2}\.ttf$"
)
NODATA_VALUE = 255


def load_index_meta(index_path: Path) -> Dict[str, Any]:
    if not index_path.exists():
        raise FileNotFoundError(f"No existe index.json: {index_path}")
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    meta = index_data.get("meta")
    if not meta:
        raise RuntimeError(f"index.json sin clave 'meta': {index_path}")
    required = ("lonLen", "latLen", "lons", "lats", "species")
    missing = [key for key in required if key not in meta]
    if missing:
        raise RuntimeError(f"index.json incompleto, faltan claves: {missing}")
    return meta


def group_slices_by_day(slices_dir: Path) -> Dict[str, Dict[int, Path]]:
    by_day: Dict[str, Dict[int, Path]] = defaultdict(dict)
    if not slices_dir.exists():
        return by_day

    for slice_path in sorted(slices_dir.glob("*.ttf")):
        match = SLICE_PATTERN.match(slice_path.name)
        if not match:
            continue
        day = match.group("date")
        hour = int(match.group("hour"))
        by_day[day][hour] = slice_path
    return by_day


def read_slice_array(slice_path: Path, lat_len: int, lon_len: int) -> np.ndarray:
    raw = slice_path.read_bytes()
    expected_size = lat_len * lon_len
    if len(raw) != expected_size:
        raise ValueError(
            f"Tamanio invalido en {slice_path.name}: {len(raw)} bytes, "
            f"esperados {expected_size}"
        )
    return np.frombuffer(raw, dtype=np.uint8).reshape(lat_len, lon_len)


def build_daily_dataset(
    day: str,
    hour_files: Dict[int, Path],
    meta: Dict[str, Any],
    species: str,
) -> xr.Dataset:
    lat_len = int(meta["latLen"])
    lon_len = int(meta["lonLen"])
    lats = np.asarray(meta["lats"], dtype=np.float64)
    lons = np.asarray(meta["lons"], dtype=np.float64)
    hours = sorted(hour_files)

    data = np.empty((len(hours), lat_len, lon_len), dtype=np.uint8)
    for idx, hour in enumerate(hours):
        data[idx] = read_slice_array(hour_files[hour], lat_len, lon_len)

    times = np.array(
        [f"{day}T{hour:02d}:00:00" for hour in hours],
        dtype="datetime64[ns]",
    )

    ds = xr.Dataset(
        {
            species: (("time", "lat", "lon"), data),
        },
        coords={
            "time": times,
            "lat": lats,
            "lon": lons,
        },
        attrs={
            "dataset_day": day,
            "species": species,
            "source": "RETINA",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "lon_min": meta.get("lonMin"),
            "lon_max": meta.get("lonMax"),
            "lat_min": meta.get("latMin"),
            "lat_max": meta.get("latMax"),
            "dice_size": meta.get("diceSize"),
            "hours_included": ",".join(f"{hour:02d}" for hour in hours),
        },
    )
    ds[species].attrs["_FillValue"] = NODATA_VALUE
    ds[species].attrs["long_name"] = species
    return ds


def build_time_day_netcdfs(
    dataset: str,
    species: str,
    out_root: Path,
    require_hours: int = 24,
) -> Dict[str, Any]:
    dataset_species = f"{dataset}-{species}"
    dataset_dir = out_root / dataset_species
    slices_dir = dataset_dir / "time-slices"
    index_path = dataset_dir / "index.json"
    output_dir = dataset_dir / "time-days"
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = load_index_meta(index_path)
    by_day = group_slices_by_day(slices_dir)

    complete_days: List[str] = []
    incomplete_days: List[Dict[str, Any]] = []
    written_files: List[str] = []
    skipped_existing: List[str] = []

    for day in sorted(by_day):
        out_path = output_dir / f"{day}.nc"
        if out_path.exists():
            skipped_existing.append(str(out_path))
            print(f"NetCDF ya existe, se omite: {out_path}")
            continue

        hour_files = by_day[day]
        missing_hours = sorted(set(range(require_hours)) - set(hour_files))
        present_hours = sorted(hour_files)

        if missing_hours:
            incomplete_days.append(
                {
                    "day": day,
                    "present_hours": present_hours,
                    "missing_hours": missing_hours,
                    "count": len(present_hours),
                }
            )
            print(
                f"Dia incompleto {day}: {len(present_hours)}/{require_hours} horas "
                f"(faltan {missing_hours})"
            )
            continue

        complete_days.append(day)
        daily_ds = build_daily_dataset(day, hour_files, meta, species)
        # daily_ds.to_netcdf(out_path)
        daily_ds.to_netcdf(out_path, engine="netcdf4", format="NETCDF4")
        written_files.append(str(out_path))
        print(f"NetCDF diario creado: {out_path}")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset,
        "species": species,
        "dataset_species": dataset_species,
        "slices_dir": str(slices_dir),
        "index_path": str(index_path),
        "output_dir": str(output_dir),
        "require_hours": require_hours,
        "days_found": len(by_day),
        "days_complete": len(complete_days),
        "days_incomplete": len(incomplete_days),
        "netcdfs_written": len(written_files),
        "netcdfs_skipped_existing": len(skipped_existing),
        "complete_days": complete_days,
        "incomplete_days": incomplete_days,
        "written_files": written_files,
        "skipped_existing": skipped_existing,
    }

    summary_path = output_dir / "build_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Resumen NetCDF diarios -> completos: {len(complete_days)}, "
        f"incompletos: {len(incomplete_days)}, creados: {len(written_files)}, "
        f"omitidos (ya existian): {len(skipped_existing)}"
    )
    print(f"Resumen guardado en: {summary_path}")
    return summary
