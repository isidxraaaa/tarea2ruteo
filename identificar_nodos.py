import geopandas as gpd
from collections import Counter

# Cargar infraestructura
fibra = gpd.read_file(
    "data/Respuesta_T5610/fibra_optica_detectada.shp"
)

# Obtener extremos de cada tramo
extremos = []

for linea in fibra.geometry:
    extremos.append(tuple(round(v, 6) for v in linea.coords[0]))
    extremos.append(tuple(round(v, 6) for v in linea.coords[-1]))

# Contar conexiones
conteo = Counter(extremos)

# Clasificación topológica preliminar
datos = []

for (lon, lat), grado in conteo.items():

    if grado == 1:
        tipo = "Terminal"
    elif grado == 2:
        tipo = "Intermedio"
    else:
        tipo = "Interconexion potencial"

    datos.append({
        "longitud": lon,
        "latitud": lat,
        "grado": grado,
        "tipo": tipo
    })

# Crear archivo geográfico
nodos = gpd.GeoDataFrame(
    datos,
    geometry=gpd.points_from_xy(
        [d["longitud"] for d in datos],
        [d["latitud"] for d in datos]
    ),
    crs="EPSG:4326"
)

nodos.to_file(
    "data/nodos_preliminares.geojson",
    driver="GeoJSON"
)

print("Nodos identificados:", len(nodos))
print("Archivo GeoJSON generado correctamente")