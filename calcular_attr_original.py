import psycopg2
from collections import Counter
from math import comb
from getpass import getpass

# Conexión a PostgreSQL
conexion = psycopg2.connect(
    host="localhost",
    port=5433,
    database="tarea2ruteo",
    user="postgres",
    password=getpass("Contraseña PostgreSQL: ")
)

cursor = conexion.cursor()

# Obtener componentes conectadas y nodos
cursor.execute("""
    SELECT
        c.component,
        v.id,
        v.cnt
    FROM pgr_connectedComponents(
        'SELECT id, source, target,
        1.0::float8 AS cost
        FROM public.enlaces_fibra'
    ) AS c
    JOIN enlaces_fibra_vertices_pgr AS v
        ON c.node = v.id;
""")

resultados = cursor.fetchall()

# Seleccionar nodos clave:
# Grado 1: origen/destino potencial
# Grado >= 3: intercambio potencial

nodos_clave = [
    (componente, nodo)
    for componente, nodo, grado in resultados
    if grado == 1 or grado >= 3
]

# Número total de nodos clave
N = len(nodos_clave)

# Contar nodos clave por componente
componentes = Counter(
    componente
    for componente, nodo in nodos_clave
)

# Calcular pares conectados
pares_conectados = sum(
    comb(cantidad, 2)
    for cantidad in componentes.values()
)

# Calcular pares posibles
pares_posibles = comb(N, 2)

# Calcular ATTR
attr = (
    pares_conectados / pares_posibles
    if pares_posibles > 0 else 0
)

print("\n=== ATTR INFRAESTRUCTURA INICIAL ===")
print("Nodos clave:", N)
print("Componentes con nodos clave:", len(componentes))
print("Pares posibles:", pares_posibles)
print("Pares conectados:", pares_conectados)
print("ATTR inicial:", round(attr, 6))
print("Conectividad:", round(attr * 100, 2), "%")

cursor.close()
conexion.close()