from pathlib import Path
from datetime import datetime, timezone
from download_retina_year_back import run

PROJECT_DIR = Path.cwd()
print("Proyecto:", PROJECT_DIR)
print("Ahora UTC:", datetime.now(timezone.utc).isoformat())

# Parametros de descarga
DATASET = "MADRID2"
SPECIES = "NO2"
DAYS_BACK = 365
OUT_DIR = "data"
REFRESH_INDEX_EVERY = 1
START_HOURS_BACK = 24  # empezar desde ayer
STOP_AFTER_MISSING = 24  # parar tras N 404 consecutivos (~84 slices disponibles)
BUILD_DAILY_COG = True
BUILD_DAILY_NETCDF = False
DELETE_INTERMEDIATE_TTF = True

print({
    "dataset": DATASET,
    "species": SPECIES,
    "days_back": DAYS_BACK,
    "out": OUT_DIR,
    "refresh_index_every": REFRESH_INDEX_EVERY,
    "start_hours_back": START_HOURS_BACK,
    "stop_after_missing": STOP_AFTER_MISSING,
    "build_daily_cog": BUILD_DAILY_COG,
    "build_daily_netcdf": BUILD_DAILY_NETCDF,
    "delete_intermediate_ttf": DELETE_INTERMEDIATE_TTF,
})

# Ejecutar descarga y construir GeoTIFF diarios multibanda (DEFLATE)
result = run(
    dataset=DATASET,
    species=SPECIES,
    out_root=Path(OUT_DIR),
    days_back=DAYS_BACK,
    refresh_index_every=max(1, REFRESH_INDEX_EVERY),
    stop_after_missing=max(0, STOP_AFTER_MISSING),
    start_hours_back=max(0, START_HOURS_BACK),
    build_daily_cog=BUILD_DAILY_COG,
    build_daily_netcdf=BUILD_DAILY_NETCDF,
    delete_intermediate_ttf=DELETE_INTERMEDIATE_TTF,
)

print("Resultado descarga:", result.get("stats"))
if result.get("cog_summary"):
    print("GeoTIFF diarios:", result["cog_summary"].get("written_files"))
    print("TTF eliminados:", result["cog_summary"].get("deleted_ttf_files"))
if result.get("netcdf_summary"):
    print("NetCDF diarios:", result["netcdf_summary"].get("written_files"))
