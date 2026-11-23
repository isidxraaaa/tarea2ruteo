import geopandas as gpd
import psycopg2
from psycopg2.extras import execute_values
from getpass import getpass

# 1. Cargar infraestructura de fibra óptica
ruta = "data/Respuesta_T5610/fibra_optica_detectada.shp"

fibra = gpd.read_file(ruta)

print("Registros encontrados:", len(fibra))

# 2. Conectar con PostgreSQL
conexion = psycopg2.connect(
    host="localhost",
    port=5433,
    database="tarea2ruteo",
    user="postgres",
    password=getpass("Contraseña PostgreSQL: ")
)

cursor = conexion.cursor()

# 3. Crear tabla de enlaces
cursor.execute("""
    CREATE TABLE IF NOT EXISTS enlaces_fibra (
        id BIGSERIAL PRIMARY KEY,
        geom geometry(LineString, 4326),
        source BIGINT,
        target BIGINT
    );
""")

# 4. Verificar si ya existen registros
cursor.execute("SELECT COUNT(*) FROM enlaces_fibra;")
cantidad = cursor.fetchone()[0]

if cantidad == 0:

    registros = [
        (geometria.wkt,)
        for geometria in fibra.geometry
    ]

    # 5. Insertar geometrías
    execute_values(
        cursor,
        "INSERT INTO enlaces_fibra (geom) VALUES %s",
        registros,
        template="(ST_GeomFromText(%s, 4326))"
    )

    print("Infraestructura importada correctamente")

else:
    print("La tabla ya contiene registros. No se duplicaron.")

conexion.commit()

# 6. Verificar resultados
cursor.execute("SELECT COUNT(*) FROM enlaces_fibra;")

print("Enlaces almacenados:", cursor.fetchone()[0])

cursor.close()
conexion.close()