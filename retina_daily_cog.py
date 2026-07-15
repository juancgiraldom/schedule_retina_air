#!/usr/bin/env python3
"""Agrupa slices horarios RETINA en COG diarios multibanda (1 banda por hora)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from retina_daily_netcdf import (
    build_daily_dataset,
    group_slices_by_day,
    load_index_meta,
)

COG_DRIVER_OPTIONS = [
    "COMPRESS=DEFLATE",
    "BLOCKSIZE=512",
    "OVERVIEWS=IGNORE_EXISTING",
]


def netcdf_to_cog(nc_path: Path, cog_path: Path) -> None:
    cog_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gdal_translate",
        "-of",
        "COG",
        "-a_srs",
        "EPSG:4326",
        "-co",
        COG_DRIVER_OPTIONS[0],
        "-co",
        COG_DRIVER_OPTIONS[1],
        "-co",
        COG_DRIVER_OPTIONS[2],
        str(nc_path),
        str(cog_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def build_time_day_cogs(
    dataset: str,
    species: str,
    out_root: Path,
    *,
    require_hours: int = 24,
    delete_intermediate_ttf: bool = True,
    save_intermediate_netcdf: bool = False,
) -> Dict[str, Any]:
    dataset_species = f"{dataset}-{species}"
    dataset_dir = out_root / dataset_species
    slices_dir = dataset_dir / "time-slices"
    index_path = dataset_dir / "index.json"
    output_dir = dataset_dir / "time-cogs"
    netcdf_dir = dataset_dir / "time-days"
    output_dir.mkdir(parents=True, exist_ok=True)
    if save_intermediate_netcdf:
        netcdf_dir.mkdir(parents=True, exist_ok=True)

    meta = load_index_meta(index_path)
    by_day = group_slices_by_day(slices_dir)

    complete_days: List[str] = []
    incomplete_days: List[Dict[str, Any]] = []
    written_files: List[str] = []
    written_netcdf_files: List[str] = []
    skipped_existing: List[str] = []
    deleted_ttf_files: List[str] = []
    failed_days: List[Dict[str, Any]] = []

    for day in sorted(by_day):
        cog_path = output_dir / f"{day}.tif"
        if cog_path.exists():
            skipped_existing.append(str(cog_path))
            print(f"COG ya existe, se omite: {cog_path}")
            if delete_intermediate_ttf:
                for hour, slice_path in sorted(by_day[day].items()):
                    if slice_path.exists():
                        slice_path.unlink()
                        deleted_ttf_files.append(str(slice_path))
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

        try:
            if save_intermediate_netcdf:
                nc_path = netcdf_dir / f"{day}.nc"
                daily_ds.to_netcdf(nc_path, engine="netcdf4", format="NETCDF4")
                written_netcdf_files.append(str(nc_path))
                netcdf_to_cog(nc_path, cog_path)
            else:
                with tempfile.TemporaryDirectory() as tmpdir:
                    nc_path = Path(tmpdir) / f"{day}.nc"
                    daily_ds.to_netcdf(nc_path, engine="netcdf4", format="NETCDF4")
                    netcdf_to_cog(nc_path, cog_path)

            written_files.append(str(cog_path))
            print(f"COG diario creado: {cog_path}")

            if delete_intermediate_ttf:
                for hour in sorted(hour_files):
                    slice_path = hour_files[hour]
                    if slice_path.exists():
                        slice_path.unlink()
                        deleted_ttf_files.append(str(slice_path))
                        print(f"Slice intermedio eliminado: {slice_path}")
        except Exception as exc:  # pylint: disable=broad-except
            failed_days.append(
                {
                    "day": day,
                    "cog_target": str(cog_path),
                    "error": str(exc),
                }
            )
            print(f"Error creando COG diario {day}: {exc}")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset,
        "species": species,
        "dataset_species": dataset_species,
        "slices_dir": str(slices_dir),
        "index_path": str(index_path),
        "output_dir": str(output_dir),
        "netcdf_dir": str(netcdf_dir) if save_intermediate_netcdf else None,
        "require_hours": require_hours,
        "delete_intermediate_ttf": delete_intermediate_ttf,
        "save_intermediate_netcdf": save_intermediate_netcdf,
        "bands_per_day": require_hours,
        "days_found": len(by_day),
        "days_complete": len(complete_days),
        "days_incomplete": len(incomplete_days),
        "cogs_written": len(written_files),
        "cogs_skipped_existing": len(skipped_existing),
        "ttf_deleted": len(deleted_ttf_files),
        "complete_days": complete_days,
        "incomplete_days": incomplete_days,
        "written_files": written_files,
        "written_netcdf_files": written_netcdf_files,
        "skipped_existing": skipped_existing,
        "deleted_ttf_files": deleted_ttf_files,
        "failed_days": failed_days,
    }

    summary_path = output_dir / "build_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Resumen COG diarios -> completos: {len(complete_days)}, "
        f"incompletos: {len(incomplete_days)}, creados: {len(written_files)}, "
        f"omitidos (ya existian): {len(skipped_existing)}, "
        f"ttf eliminados: {len(deleted_ttf_files)}, fallos: {len(failed_days)}"
    )
    print(f"Resumen guardado en: {summary_path}")
    return summary
