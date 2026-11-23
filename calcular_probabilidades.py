"""Modelo sísmico exploratorio para enlaces de fibra óptica (terremoto Maule 2010).

Base metodológica: Jiménez, Barrera & Cancela (IEEE Access, 2023),
https://doi.org/10.1109/ACCESS.2023.3255794

Escenario: PGA del USGS ShakeMap del sismo 27F, k_c=0.1 g,
alpha=0.26, tramos geodésicos de longitud <= 10 km.

ATENCIÓN: Los enlaces fuera del ShakeMap reciben prob_falla=0
ÚNICAMENTE como supuesto computacional de un escenario de mínima afectación
fuera de cobertura; la columna shapemap_estado permite identificarlos.
No equivale a una estimación física de daño cero. Se requiere sensibilidad.
"""

from collections import Counter
from getpass import getpass
from math import ceil, exp, log
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from pyproj import Geod
from scipy.spatial import cKDTree
from shapely import wkb

RUTA_GRID = Path('data/shakemap_maule_2010_grid.xml')
ALFA = 0.26  # vulnerabilidad de cable robusto, escenario ilustrativo
KC_G = 0.1  # aceleración crítica del terreno, en unidades g
LONGITUD_MAX_TRAMO_M = 10_000
GEOD = Geod(ellps='WGS84')


def cargar_grid():
    raiz = ET.parse(RUTA_GRID).getroot()
    spec = next(e for e in raiz if e.tag.split('}')[-1] == 'grid_specification')
    datos = next(e for e in raiz if e.tag.split('}')[-1] == 'grid_data')
    array = np.fromstring(datos.text, sep=' ').reshape(-1, 9)
    assert len(array) == int(spec.attrib['nlon']) * int(spec.attrib['nlat'])
    return array, cKDTree(array[:, :2]), (
        float(spec.attrib['lon_min']), float(spec.attrib['lon_max']),
        float(spec.attrib['lat_min']), float(spec.attrib['lat_max'])
    )


def puntos_en_tramos_geodesicos(linea, distancia_maxima_m=LONGITUD_MAX_TRAMO_M):
    """Devuelve puntos centrales y longitud real de subtramos de <= 10 km."""
    coords = list(linea.coords)
    medidas = []
    segmentos = []
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        azimut, _, metros = GEOD.inv(lon1, lat1, lon2, lat2)
        segmentos.append((lon1, lat1, azimut, metros))
        medidas.append(max(0.0, metros))
    total = sum(medidas)
    if total == 0:
        return []
    cantidad = max(1, ceil(total / distancia_maxima_m))
    tamano = total / cantidad
    puntos = []
    for i in range(cantidad):
        desplazamiento = (i + 0.5) * tamano
        recorrido = 0.0
        for lon, lat, azimut, metros in segmentos:
            if desplazamiento <= recorrido + metros or (lon, lat, azimut, metros) == segmentos[-1]:
                x, y, _ = GEOD.fwd(lon, lat, azimut, max(0.0, min(metros, desplazamiento - recorrido)))
                puntos.append((x, y, tamano / 1000.0))
                break
            recorrido += metros
    return puntos


def tasa_fallas_por_km(pga_porcentaje_g):
    """Fórmulas 5 y 6 de Jiménez et al. (2023), con tasa >= 0.

    PGA/%g se convierte a g. Saygili-Rathje entrega PGD en cm;
    para la relación empírica de reparación se expresa PGD en metros.
    Si PGA <= k_c, se asume sin desplazamiento por deslizamiento.
    """
    pga_g = pga_porcentaje_g / 100.0
    if pga_g <= KC_G:
        return 0.0
    r = KC_G / pga_g
    ln_pgd_cm = (
        5.52 - 4.43 * r - 20.39 * r**2
        + 42.61 * r**3 - 28.74 * r**4
        + 0.72 * log(pga_g)
    )
    pgd_m = exp(ln_pgd_cm) / 100.0
    return max(0.0, ALFA * (4.317 * pgd_m - 0.324))


def main():
    if not RUTA_GRID.is_file():
        raise FileNotFoundError(f'Falta {RUTA_GRID}. Ejecuta desde la carpeta del proyecto.')
    grid, arbol, (lon_min, lon_max, lat_min, lat_max) = cargar_grid()
    print('ShakeMap cargado:', len(grid), 'celdas')
    conexion = psycopg2.connect(
        host='localhost', port=5433, database='tarea2ruteo',
        user='postgres', password=getpass('Contrasena PostgreSQL: ')
    )
    try:
        with conexion.cursor() as cur:
            cur.execute('''
                SELECT id, ST_AsBinary(geom),
                    ST_CoveredBy(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326)),
                    ST_Intersects(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326))
                FROM public.enlaces_fibra ORDER BY id
            ''', (lon_min, lat_min, lon_max, lat_max,
                  lon_min, lat_min, lon_max, lat_max))
            enlaces = cur.fetchall()

            actualizaciones = []
            cantidades = Counter()
            for id_enlace, geometria, dentro, intersecta in enlaces:
                estado = 'dentro' if dentro else ('parcial' if intersecta else 'fuera')
                cantidades[estado] += 1
                linea = wkb.loads(bytes(geometria))
                peligro_acumulado = 0.0
                pga_max_g = None

                for lon, lat, longitud_km in puntos_en_tramos_geodesicos(linea):
                    if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                        continue  # subtramo sin datos; no inferir PGA
                    _, indice = arbol.query([lon, lat])
                    pga_pct_g = float(grid[indice, 3])  # cuarta columna: PGA (%g)
                    pga_max_g = max(pga_max_g or 0, pga_pct_g / 100.0)
                    peligro_acumulado += longitud_km * tasa_fallas_por_km(pga_pct_g)

                probabilidad = 1.0 - exp(-peligro_acumulado)
                probabilidad = max(0.0, min(1.0, probabilidad))
                actualizaciones.append((id_enlace, probabilidad, estado, pga_max_g))

            cur.execute('''
                ALTER TABLE public.enlaces_fibra
                ADD COLUMN IF NOT EXISTS shakemap_estado TEXT;
                ALTER TABLE public.enlaces_fibra
                ADD COLUMN IF NOT EXISTS pga_max_g DOUBLE PRECISION;
            ''')
            execute_values(cur, '''
                UPDATE public.enlaces_fibra AS e
                SET prob_falla = t.prob_falla,
                    shakemap_estado = t.shakemap_estado,
                    pga_max_g = t.pga_max_g
                FROM (VALUES %s) AS t(id, prob_falla, shakemap_estado, pga_max_g)
                WHERE e.id = t.id
            ''', actualizaciones, template='(%s::bigint,%s::float8,%s::text,%s::float8)', page_size=500)
        conexion.commit()
        print('Enlaces actualizados:', len(actualizaciones))
        print('Cobertura:', dict(cantidades))
        with conexion.cursor() as cur:
            cur.execute('''
                SELECT COUNT(*), COUNT(*) FILTER (WHERE prob_falla > 0),
                       ROUND(AVG(prob_falla)::numeric,6),
                       ROUND(MAX(prob_falla)::numeric,6)
                FROM public.enlaces_fibra
            ''')
            total, con_falla, media, maximo = cur.fetchone()
            print('Total:', total, '| prob_falla > 0:', con_falla,
                  '| promedio:', media, '| maximo:', maximo)
        print('ATENCION: los enlaces fuera de ShakeMap quedan en 0 por escenario,')
        print('NO porque se haya comprobado ausencia de peligro sísmico.')
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


if __name__ == '__main__':
    main()
