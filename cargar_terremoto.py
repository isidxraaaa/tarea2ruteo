import json
import psycopg2
from datetime import datetime, timezone
from getpass import getpass

# Leer GeoJSON del terremoto
with open("data/terremoto_maule_2010.geojson", encoding="utf-8") as archivo:
    datos = json.load(archivo)

evento = (
    datos["features"][0]
    if datos["type"] == "FeatureCollection"
    else datos
)

propiedades = evento["properties"]
lon, lat, profundidad = evento["geometry"]["coordinates"]

# Conectar a PostgreSQL
conexion = psycopg2.connect(
    host="localhost",
    port=5433,
    dbname="tarea2ruteo",
    user="postgres",
    password=getpass("Contraseña PostgreSQL: ")
)

cursor = conexion.cursor()

# Crear tabla
cursor.execute("""
    CREATE TABLE IF NOT EXISTS terremotos (
        event_id TEXT PRIMARY KEY,
        magnitud DOUBLE PRECISION,
        fecha TIMESTAMPTZ,
        profundidad_km DOUBLE PRECISION,
        geom geometry(Point, 4326)
    );
""")

# Insertar terremoto
cursor.execute("""
    INSERT INTO terremotos
    (event_id, magnitud, fecha, profundidad_km, geom)
    VALUES (
        %s, %s, %s, %s,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)
    )
    ON CONFLICT (event_id) DO UPDATE SET
        magnitud = EXCLUDED.magnitud,
        fecha = EXCLUDED.fecha,
        profundidad_km = EXCLUDED.profundidad_km,
        geom = EXCLUDED.geom;
""", (
    evento["id"],
    propiedades["mag"],
    datetime.fromtimestamp(propiedades["time"] / 1000, timezone.utc),
    profundidad,
    lon,
    lat
))

conexion.commit()

print("Terremoto cargado correctamente en PostgreSQL")
print("Magnitud:", propiedades["mag"])
print("Epicentro:", lat, lon)
print("Profundidad:", profundidad, "km")

cursor.close()
conexion.close()