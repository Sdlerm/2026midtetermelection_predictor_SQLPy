"""One-time: convert Census cartographic district boundaries to GeoJSON.
Download first (about 30MB):
  https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip
into data/. If that URL 404s, browse census.gov 'cartographic boundary files'
for the cd119 (119th Congress) product. Requires: pip install geopandas
"""
import geopandas as gpd

gdf = gpd.read_file("data/cb_2024_us_cd119_500k.zip")
gdf = gdf.to_crs(epsg=4326)                      # lat/lon, what plotly expects
gdf["geometry"] = gdf["geometry"].simplify(0.01) # ~1km tolerance; 435 full-res polygons will make streamlit crawl
gdf[["GEOID", "geometry"]].to_file("data/cd119.geojson", driver="GeoJSON")
print(gdf[["GEOID"]].head())                     # verify GEOID looks like '0101', '4828', ...