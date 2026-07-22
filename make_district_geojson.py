"""One-time: convert Census cartographic district boundaries to GeoJSON."""

from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlretrieve
from zipfile import ZipFile, is_zipfile


DISTRICT_BOUNDARIES_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip"
)
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SOURCE_ZIP = DATA_DIR / "cb_2024_us_cd119_500k.zip"
OUTPUT_GEOJSON = DATA_DIR / "cd119.geojson"


def ensure_source_zip() -> Path:
    """Download the Census district boundary zip if it is not already present."""
    if SOURCE_ZIP.exists() and is_zipfile(SOURCE_ZIP):
        return SOURCE_ZIP
    if SOURCE_ZIP.exists():
        SOURCE_ZIP.unlink()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Missing {SOURCE_ZIP}. Downloading Census district boundaries...")
    temp_zip = SOURCE_ZIP.with_suffix(".zip.download")

    try:
        urlretrieve(DISTRICT_BOUNDARIES_URL, temp_zip)
    except (HTTPError, URLError, OSError) as exc:
        if temp_zip.exists():
            temp_zip.unlink()
        raise SystemExit(
            "Could not download Census district boundaries.\n"
            f"URL: {DISTRICT_BOUNDARIES_URL}\n"
            f"Save the zip to: {SOURCE_ZIP}\n"
            "Then rerun this script."
        ) from exc

    if not is_zipfile(temp_zip):
        temp_zip.unlink()
        raise SystemExit(
            "Downloaded Census district boundaries, but the result was not a valid zip file.\n"
            f"URL: {DISTRICT_BOUNDARIES_URL}\n"
            f"Save a valid zip to: {SOURCE_ZIP}\n"
            "Then rerun this script."
        )

    temp_zip.replace(SOURCE_ZIP)
    return SOURCE_ZIP


def locate_shapefile(zip_path: Path) -> str:
    """Return a GDAL-readable path to the .shp inside the zip.

    Handles zips where the shapefile is nested in a subfolder (e.g. a macOS
    Finder re-zip, which also adds __MACOSX metadata entries that break
    GDAL's dataset auto-detection).
    """
    with ZipFile(zip_path) as zf:
        shp_members = [
            name
            for name in zf.namelist()
            if name.endswith(".shp") and not name.startswith("__MACOSX/")
        ]
    if not shp_members:
        raise SystemExit(
            f"No .shp file found inside {zip_path}.\n"
            f"Delete it and rerun this script to re-download from:\n"
            f"{DISTRICT_BOUNDARIES_URL}"
        )
    return f"/vsizip/{zip_path}/{shp_members[0]}"


def main() -> None:
    try:
        import geopandas as gpd
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing optional dependency 'geopandas'. Install it with: pip install geopandas"
        ) from exc

    source_zip = ensure_source_zip()
    gdf = gpd.read_file(locate_shapefile(source_zip))
    gdf = gdf.to_crs(epsg=4326)  # lat/lon, what plotly expects
    gdf["geometry"] = gdf["geometry"].simplify(0.01)  # ~1km tolerance; 435 full-res polygons will make streamlit crawl
    gdf[["GEOID", "geometry"]].to_file(OUTPUT_GEOJSON, driver="GeoJSON")
    print(gdf[["GEOID"]].head())  # verify GEOID looks like '0101', '4828', ...


if __name__ == "__main__":
    main()
