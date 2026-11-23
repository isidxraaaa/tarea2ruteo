
import random
import statistics
from getpass import getpass
from math import comb

import psycopg2

SIMULACIONES = 5000
SEMILLA = 2026


class UnionFind:
    def __init__(self, cantidad_vertices, nodos_clave):
        self.padre = list(range(cantidad_vertices))
        self.tamano = [1] * cantidad_vertices
        self.claves = [
            1 if i in nodos_clave else 0
            for i in range(cantidad_vertices)
        ]
        self.pares_conectados = 0

    def buscar(self, x):
        while self.padre[x] != x:
            self.padre[x] = self.padre[self.padre[x]]
            x = self.padre[x]
        return x

    def unir(self, a, b):
        a = self.buscar(a)
        b = self.buscar(b)

        if a == b:
            return

        if self.tamano[a] < self.tamano[b]:
            a, b = b, a

        # Nuevos pares de nodos clave conectados
        self.pares_conectados += self.claves[a] * self.claves[b]

        self.padre[b] = a
        self.tamano[a] += self.tamano[b]
        self.claves[a] += self.claves[b]


def calcular_pares(cantidad_vertices, claves, enlaces, probabilistico, rng):
    red = UnionFind(cantidad_vertices, claves)

    for origen, destino, prob_falla in enlaces:
        # En Monte Carlo, cada enlace puede fallar
        if probabilistico and rng.random() < prob_falla:
            continue

        red.unir(origen, destino)

    return red.pares_conectados


def main():
    conexion = psycopg2.connect(
        host="localhost",
        port=5433,
        database="tarea2ruteo",
        user="postgres",
        password=getpass("Contrasena PostgreSQL: ")
    )

    try:
        with conexion.cursor() as cursor:
            # Obtener vertices de la topologia pgRouting
            cursor.execute("""
                SELECT id, cnt
                FROM public.enlaces_fibra_vertices_pgr
                ORDER BY id;
            """)
            vertices = cursor.fetchall()

            # Convertir IDs a indices para Union-Find
            indices = {
                nodo_id: indice
                for indice, (nodo_id, grado) in enumerate(vertices)
            }

            # Nodos clave: grado 1 o grado >= 3
            claves = {
                indice
                for indice, (nodo_id, grado) in enumerate(vertices)
                if grado == 1 or grado >= 3
            }

            # Obtener enlaces y probabilidades de falla
            cursor.execute("""
                SELECT source, target, prob_falla
                FROM public.enlaces_fibra
                ORDER BY id;
            """)
            registros = cursor.fetchall()

        enlaces = []

        for origen, destino, prob in registros:
            if origen not in indices or destino not in indices:
                raise ValueError("Enlace con vertice no encontrado")

            if prob is None or not 0 <= prob <= 1:
                raise ValueError("Probabilidad fuera del rango [0, 1]")

            enlaces.append((
                indices[origen],
                indices[destino],
                float(prob)
            ))

        N = len(claves)
        pares_posibles = comb(N, 2)

        if pares_posibles == 0:
            raise ValueError("Se necesitan al menos dos nodos clave")

        # Escenario inicial: todos los enlaces operativos
        pares_inicial = calcular_pares(
            len(vertices), claves, enlaces, False, None
        )
        attr_inicial = pares_inicial / pares_posibles

        print("\n=== ATTR INICIAL ===")
        print("Nodos clave:", N)
        print("Enlaces:", len(enlaces))
        print("Pares posibles:", pares_posibles)
        print("Pares conectados:", pares_inicial)
        print("ATTR inicial:", round(attr_inicial, 6))
        print("Conectividad inicial:", round(attr_inicial * 100, 2), "%")

        # Monte Carlo: fallas independientes de enlaces
        rng = random.Random(SEMILLA)
        resultados = []

        for _ in range(SIMULACIONES):
            pares = calcular_pares(
                len(vertices), claves, enlaces, True, rng
            )
            resultados.append(pares / pares_posibles)

        attr_montecarlo = statistics.mean(resultados)
        desviacion = statistics.stdev(resultados)
        error_estandar = desviacion / (SIMULACIONES ** 0.5)
        margen_95 = 1.96 * error_estandar

        print("\n=== ATTR PROBABILISTICO: MONTE CARLO ===")
        print("Simulaciones:", SIMULACIONES)
        print("Semilla:", SEMILLA)
        print("ATTR estimado:", round(attr_montecarlo, 6))
        print("Conectividad estimada:", round(attr_montecarlo * 100, 2), "%")
        print(
            "Intervalo aproximado 95%:",
            round(max(0, attr_montecarlo - margen_95), 6),
            "a",
            round(min(1, attr_montecarlo + margen_95), 6)
        )

    finally:
        conexion.close()


if __name__ == "__main__":
    main()
