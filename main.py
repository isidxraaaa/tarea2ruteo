"""Punto de entrada reproducible — resiliencia de infraestructura Internet de Chile.

Ejecutar desde la raiz del proyecto: python main.py

Requisitos: Docker Desktop, Python 3.11+, paquetes numpy, scipy, psycopg2,
shapely y pyproj; archivos compose.yaml, .env, main.sql,
calcular_probabilidades.py y data/shakemap_maule_2010_grid.xml.

Usa solamente la base creada por Docker Compose en 127.0.0.1:5434.
"""

from __future__ import annotations

import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from math import comb, exp, sqrt
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import webbrowser

import psycopg2
from psycopg2.extras import execute_values
from shapely import wkb

import calcular_probabilidades as modelo

ROOT = Path(__file__).resolve().parent
SIMULACIONES = 5000
SEMILLA = 2026


def leer_contrasena() -> str:
    ruta = ROOT / '.env'
    if not ruta.is_file():
        raise FileNotFoundError('No existe .env. Crea POSTGRES_PASSWORD=...')
    for linea in ruta.read_text(encoding='utf-8-sig').splitlines():
        linea = linea.strip()
        if linea.startswith('POSTGRES_PASSWORD='):
            valor = linea.split('=', 1)[1].strip().strip('"').strip("'")
            if valor:
                return valor
    raise ValueError('No se encontro POSTGRES_PASSWORD en .env')


def levantar_docker() -> None:
    print('[1/5] Iniciando base de datos con Docker Compose...', flush=True)
    subprocess.run(['docker', 'compose', 'up', '-d'], cwd=ROOT, check=True)


def conectar(password: str):
    print('[2/5] Comprobando conexion con PostgreSQL en puerto 5434...', flush=True)
    ultimo_error = None
    for _ in range(40):
        try:
            conexion = psycopg2.connect(
                host='127.0.0.1', port=5434, dbname='tarea2ruteo',
                user='postgres', password=password, connect_timeout=3
            )
            with conexion.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM public.enlaces_fibra')
                cantidad = cursor.fetchone()[0]
                cursor.execute('SELECT COUNT(*) FROM public.enlaces_fibra_vertices_pgr')
                vertices = cursor.fetchone()[0]
            if cantidad != 1915 or vertices != 2079:
                conexion.close()
                raise RuntimeError(
                    f'Datos incompletos: {cantidad} enlaces, {vertices} vertices. '
                    'Comprueba la importacion inicial de main.sql.'
                )
            print(f'    Base verificada: {cantidad} enlaces y {vertices} vertices.')
            return conexion
        except psycopg2.OperationalError as exc:
            ultimo_error = exc
            time.sleep(1)
    raise ConnectionError('No fue posible conectar a PostgreSQL en 5434') from ultimo_error


def actualizar_probabilidades(conexion) -> dict:
    """Recalcula prob_falla con las mismas funciones del script validado."""
    print('[3/5] Recalculando probabilidades de falla desde ShakeMap...', flush=True)
    ruta_grid = ROOT / 'data' / 'shakemap_maule_2010_grid.xml'
    if not ruta_grid.is_file():
        raise FileNotFoundError(f'Falta el archivo ShakeMap: {ruta_grid}')
    # El modulo original emplea una ruta relativa; hacemos que sea absoluta.
    modelo.RUTA_GRID = ruta_grid
    grid, arbol, (lon_min, lon_max, lat_min, lat_max) = modelo.cargar_grid()
    conteo = Counter()
    actualizaciones = []
    try:
        with conexion.cursor() as cursor:
            cursor.execute('''
                SELECT id, ST_AsBinary(geom),
                       ST_CoveredBy(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326)),
                       ST_Intersects(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326))
                FROM public.enlaces_fibra ORDER BY id
            ''', (lon_min, lat_min, lon_max, lat_max,
                  lon_min, lat_min, lon_max, lat_max))
            enlaces = cursor.fetchall()
            for id_enlace, geom_binario, dentro, intersecta in enlaces:
                estado = 'dentro' if dentro else ('parcial' if intersecta else 'fuera')
                conteo[estado] += 1
                linea = wkb.loads(bytes(geom_binario))
                acumulado = 0.0
                pga_max_g = None
                for lon, lat, longitud_km in modelo.puntos_en_tramos_geodesicos(linea):
                    if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                        continue
                    _, indice = arbol.query([lon, lat])
                    pga_pct_g = float(grid[indice, 3])
                    pga_max_g = max(pga_max_g or 0, pga_pct_g / 100)
                    acumulado += longitud_km * modelo.tasa_fallas_por_km(pga_pct_g)
                prob_falla = max(0.0, min(1.0, 1.0 - exp(-acumulado)))
                actualizaciones.append((id_enlace, prob_falla, estado, pga_max_g))

            cursor.execute('''
                ALTER TABLE public.enlaces_fibra
                    ADD COLUMN IF NOT EXISTS shakemap_estado TEXT;
                ALTER TABLE public.enlaces_fibra
                    ADD COLUMN IF NOT EXISTS pga_max_g DOUBLE PRECISION;
            ''')
            execute_values(cursor, '''
                UPDATE public.enlaces_fibra AS e
                SET prob_falla = v.prob,
                    shakemap_estado = v.estado,
                    pga_max_g = v.pga
                FROM (VALUES %s) AS v(id, prob, estado, pga)
                WHERE e.id = v.id
            ''', actualizaciones,
                template='(%s::bigint,%s::float8,%s::text,%s::float8)',
                page_size=500)
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    print(f'    Actualizados {len(actualizaciones)} enlaces; cobertura {dict(conteo)}.')
    print('    Aviso: fuera del ShakeMap se usa prob_falla=0 como SUPUESTO, no como dato de seguridad.')
    return dict(conteo)


class UnionFind:
    def __init__(self, total_vertices: int, claves: set[int]):
        self.padre = list(range(total_vertices))
        self.tamano = [1] * total_vertices
        self.cantidad_claves = [int(i in claves) for i in range(total_vertices)]
        self.pares = 0

    def buscar(self, x: int) -> int:
        while self.padre[x] != x:
            self.padre[x] = self.padre[self.padre[x]]
            x = self.padre[x]
        return x

    def unir(self, a: int, b: int) -> None:
        a, b = self.buscar(a), self.buscar(b)
        if a == b:
            return
        if self.tamano[a] < self.tamano[b]:
            a, b = b, a
        self.pares += self.cantidad_claves[a] * self.cantidad_claves[b]
        self.padre[b] = a
        self.tamano[a] += self.tamano[b]
        self.cantidad_claves[a] += self.cantidad_claves[b]


def calcular_attr(conexion) -> dict:
    print('[4/5] Calculando ATTR inicial y posterior (5000 simulaciones)...', flush=True)
    with conexion.cursor() as cursor:
        cursor.execute('SELECT id, cnt FROM public.enlaces_fibra_vertices_pgr ORDER BY id')
        vertices = cursor.fetchall()
        cursor.execute('SELECT source, target, prob_falla FROM public.enlaces_fibra ORDER BY id')
        registros = cursor.fetchall()

    indices = {nodo: i for i, (nodo, _) in enumerate(vertices)}
    claves = {i for i, (_, grado) in enumerate(vertices) if grado == 1 or grado >= 3}
    enlaces = []
    for a, b, prob in registros:
        if a not in indices or b not in indices:
            raise ValueError(f'Enlace sin vertices validos: {a}, {b}')
        if prob is None or not 0 <= prob <= 1:
            raise ValueError('Hay probabilidades fuera del intervalo [0, 1]')
        enlaces.append((indices[a], indices[b], float(prob)))

    N = len(claves)
    posibles = comb(N, 2)
    if posibles == 0:
        raise ValueError('Se necesitan al menos dos nodos clave')

    def pares_de_una_muestra(probabilistico: bool, rng=None) -> int:
        red = UnionFind(len(vertices), claves)
        for a, b, prob in enlaces:
            if probabilistico and rng.random() < prob:
                continue
            red.unir(a, b)
        return red.pares

    pares_iniciales = pares_de_una_muestra(False)
    inicial = pares_iniciales / posibles
    rng = random.Random(SEMILLA)
    muestras = [pares_de_una_muestra(True, rng) / posibles for _ in range(SIMULACIONES)]
    posterior = statistics.mean(muestras)
    margen = 1.96 * statistics.stdev(muestras) / sqrt(SIMULACIONES)
    reduccion = (inicial - posterior) / inicial * 100 if inicial else 0
    resultado = {
        'nodos_clave': N,
        'enlaces': len(enlaces),
        'pares_posibles': posibles,
        'pares_iniciales': pares_iniciales,
        'inicial': inicial,
        'posterior': posterior,
        'reduccion_pct': reduccion,
        'simulaciones': SIMULACIONES,
        'semilla': SEMILLA,
        'intervalo_95': [max(0, posterior - margen), min(1, posterior + margen)],
    }
    print(f'    Inicial: {inicial:.6f} | posterior: {posterior:.6f} | perdida relativa: {reduccion:.2f}%')
    print(f'    IC 95% aproximado: {resultado["intervalo_95"][0]:.6f} a {resultado["intervalo_95"][1]:.6f}')
    return resultado


def obtener_datos_mapa(conexion, attr: dict, cobertura: dict) -> dict:
    print('[5/5] Preparando capas geograficas para Leaflet...', flush=True)
    with conexion.cursor() as cursor:
        cursor.execute('''
            SELECT id, prob_falla, shakemap_estado, pga_max_g,
                   ST_AsGeoJSON(geom)
            FROM public.enlaces_fibra ORDER BY id
        ''')
        features = []
        for id_enlace, prob, estado, pga, geom_json in cursor.fetchall():
            features.append({
                'type': 'Feature',
                'geometry': json.loads(geom_json),
                'properties': {
                    'id': id_enlace,
                    'prob': float(prob),
                    'estado': estado,
                    'pga': float(pga) if pga is not None else None,
                }
            })

        cursor.execute('''
            SELECT id, cnt, ST_X(the_geom), ST_Y(the_geom)
            FROM public.enlaces_fibra_vertices_pgr
            WHERE cnt=1 OR cnt>=3
            ORDER BY id
        ''')
        nodos = [{'id': idn, 'grado': grado, 'lon': lon, 'lat': lat}
                 for idn, grado, lon, lat in cursor.fetchall()]

        cursor.execute('''
            SELECT e.nodo_id, e.funcion, e.justificacion,
                   ST_X(v.the_geom), ST_Y(v.the_geom)
            FROM public.nodos_estrategicos e
            JOIN public.enlaces_fibra_vertices_pgr v ON v.id=e.nodo_id
            ORDER BY e.nodo_id
        ''')
        estrategicos = [
            {'id': idn, 'funcion': funcion, 'justificacion': justificacion,
             'lon': lon, 'lat': lat}
            for idn, funcion, justificacion, lon, lat in cursor.fetchall()
        ]

        cursor.execute('''
            SELECT event_id, magnitud, ST_X(geom), ST_Y(geom)
            FROM public.terremotos
            ORDER BY fecha LIMIT 1
        ''')
        terremoto = cursor.fetchone()
        if terremoto is None:
            raise RuntimeError('No existe el terremoto en la base')
        terremoto_datos = {
            'id': terremoto[0], 'magnitud': float(terremoto[1]),
            'lon': terremoto[2], 'lat': terremoto[3]
        }

    return {
        'enlaces': {'type': 'FeatureCollection', 'features': features},
        'nodos': nodos,
        'estrategicos': estrategicos,
        'terremoto': terremoto_datos,
        'attr': attr,
        'cobertura': cobertura,
    }


HTML = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resiliencia de la infraestructura de Internet de Chile</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#163044;background:#f2f6fa}
*{box-sizing:border-box}body{margin:0}main{display:grid;grid-template-columns:340px 1fr;height:100vh}
aside{padding:23px;overflow:auto;background:white;box-shadow:2px 0 15px #00000012;z-index:500}
h1{font-size:21px;line-height:1.3;margin:2px 0 8px}.sub{color:#607587;font-size:13px;line-height:1.5}
.kpi{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:22px 0 14px}.kpi>div{border:1px solid #e0e9ef;border-radius:13px;padding:13px}
.kpi small{display:block;color:#637a8d;font-size:11px}.kpi strong{display:block;font-size:24px;margin-top:5px}
.loss{background:#fff3e9;border-radius:12px;padding:13px;margin:12px 0;color:#75452d}
.loss strong{font-size:20px}.note{font-size:12px;color:#5d7181;line-height:1.5}
.legend{margin:18px 0}.legend-item{display:flex;align-items:center;gap:9px;margin:9px 0;font-size:12px}
.swatch{height:5px;width:28px;border-radius:10px;display:inline-block}
#map{width:100%;height:100%}input[type=checkbox]{accent-color:#256ea8}
.layer{padding:6px 0;font-size:13px}hr{border:0;border-top:1px solid #e7ebef;margin:18px 0}
@media(max-width:750px){main{display:flex;flex-direction:column}aside{max-height:40vh}#map{min-height:60vh}}
</style></head><body><main>
<aside>
<p class="sub">Tarea 2 · pgRouting + PostGIS + USGS ShakeMap</p>
<h1>Resiliencia de la infraestructura de Internet de Chile</h1>
<p class="sub">Terremoto del Maule (27F), magnitud 8,8. Estimación probabilística, no daño histórico observado.</p>
<div class="kpi"><div><small>ATTR inicial</small><strong id="inicial">—</strong></div><div><small>ATTR posterior</small><strong id="posterior">—</strong></div></div>
<div class="loss"><span>Reducción relativa estimada</span><br><strong id="perdida">—</strong></div>
<p class="note" id="metadatos">Cargando datos de PostgreSQL…</p>
<hr><b>Capas del mapa</b>
<div class="layer"><label><input type="checkbox" id="mostrarNodos"> Mostrar los 563 nodos clave</label></div>
<div class="layer"><label><input type="checkbox" id="mostrarEstrategicos" checked> Mostrar 4 nodos estratégicos</label></div>
<div class="layer"><label><input type="checkbox" id="mostrarTerremoto" checked> Mostrar epicentro 27F</label></div>
<div class="legend"><b>Probabilidad de falla del enlace</b>
<div class="legend-item"><span class="swatch" style="background:#7da6bd"></span>0 % · Dentro de cobertura</div>
<div class="legend-item"><span class="swatch" style="background:#e7c661"></span>Mayor que 0 % y hasta 20 %</div>
<div class="legend-item"><span class="swatch" style="background:#eb8b49"></span>Mayor que 20 % y hasta 50 %</div>
<div class="legend-item"><span class="swatch" style="background:#c83640"></span>Mayor que 50 %</div>
<div class="legend-item"><span class="swatch" style="background:#a3a8b0"></span>Sin cobertura ShakeMap (supuesto 0 %)</div>
</div>
<hr><p class="note">Haz clic en cualquier línea para conocer el identificador del enlace, su PGA máximo y su probabilidad de falla.</p>
<p class="note"><b>Limitación:</b> las fallas se simulan de forma independiente. El 0 % fuera del ShakeMap es un supuesto de alcance, no una medición de seguridad.</p>
</aside><div id="map"></div></main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(async function(){
  const respuesta=await fetch('/api/data');
  if(!respuesta.ok)throw new Error('No se pudieron obtener datos');
  const datos=await respuesta.json();
  const a=datos.attr;
  const pct=x=>(x*100).toFixed(2).replace('.',',')+' %';
  document.getElementById('inicial').textContent=pct(a.inicial);
  document.getElementById('posterior').textContent=pct(a.posterior);
  document.getElementById('perdida').textContent=a.reduccion_pct.toFixed(2).replace('.',',')+' %';
  document.getElementById('metadatos').textContent=`${a.enlaces} enlaces · ${a.nodos_clave} nodos clave · ${a.simulaciones.toLocaleString('es-CL')} simulaciones · semilla ${a.semilla} · IC 95 % aprox.: ${a.intervalo_95.map(x=>x.toFixed(6)).join(' – ')}`;

  const map=L.map('map',{preferCanvas:true}).setView([-35.5,-71.5],6);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:18,attribution:'© OpenStreetMap contributors'
  }).addTo(map);
  const estilo=f=>{
    const p=f.properties;
    if(p.estado==='fuera')return {color:'#a3a8b0',weight:2.5,opacity:.65,dashArray:'5 6'};
    if(p.estado==='parcial')return {color:'#a3a8b0',weight:2.5,opacity:.8,dashArray:'3 6'};
    return {color:p.prob>.5?'#c83640':p.prob>.2?'#eb8b49':p.prob>0?'#e7c661':'#7da6bd',weight:p.prob>.5?4:3,opacity:.88};
  };
  const fibra=L.geoJSON(datos.enlaces,{style:estilo,onEachFeature:(f,l)=>{
    const p=f.properties;
    const cobertura=p.estado==='fuera'?'Fuera de cobertura':p.estado==='parcial'?'Cobertura parcial':'Dentro del ShakeMap';
    l.bindPopup(`<b>Enlace ${p.id}</b><br>Probabilidad: <b>${pct(p.prob)}</b><br>PGA máx.: ${p.pga===null?'Sin dato':p.pga.toFixed(4)+' g'}<br>${cobertura}`);
  }}).addTo(map);
  const nodos=L.layerGroup(datos.nodos.map(n=>L.circleMarker([n.lat,n.lon],{
    radius:3.3,color:n.grado>=3?'#3156a7':'#4d999e',weight:1,fillOpacity:.8
  }).bindPopup(`<b>Nodo ${n.id}</b><br>Grado: ${n.grado}<br>${n.grado>=3?'Intercambio potencial':'Origen/destino potencial'} (supuesto)`)));
  const estrategicos=L.layerGroup(datos.estrategicos.map(n=>L.circleMarker([n.lat,n.lon],{
    radius:8,color:'#4b2d84',weight:2,fillColor:'#b18bed',fillOpacity:1
  }).bindPopup(`<b>Nodo estratégico ${n.id}</b><br>${n.funcion}<br>${n.justificacion}`))).addTo(map);
  const t=datos.terremoto;
  const sismo=L.layerGroup([L.circleMarker([t.lat,t.lon],{
    radius:10,color:'#80121c',weight:3,fillColor:'#e94451',fillOpacity:1
  }).bindPopup(`<b>Epicentro 27F</b><br>Magnitud ${t.magnitud}<br>USGS: ${t.id}`)]).addTo(map);
  document.getElementById('mostrarNodos').addEventListener('change',e=>e.target.checked?map.addLayer(nodos):map.removeLayer(nodos));
  document.getElementById('mostrarEstrategicos').addEventListener('change',e=>e.target.checked?map.addLayer(estrategicos):map.removeLayer(estrategicos));
  document.getElementById('mostrarTerremoto').addEventListener('change',e=>e.target.checked?map.addLayer(sismo):map.removeLayer(sismo));
  map.setView([-35.5, -71.5], 7);
})().catch(err=>{document.getElementById('metadatos').textContent='Error al cargar el mapa: '+err.message;});
</script></body></html>'''


def servidor(datos: dict, puerto: int, abrir_navegador: bool) -> None:
    datos_json = json.dumps(datos, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    html = HTML.encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                cuerpo, tipo = html, 'text/html; charset=utf-8'
            elif self.path == '/api/data':
                cuerpo, tipo = datos_json, 'application/json; charset=utf-8'
            elif self.path == '/health':
                cuerpo, tipo = b'OK', 'text/plain; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', tipo)
            self.send_header('Content-Length', str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, format, *args):
            return

    with ThreadingHTTPServer(('127.0.0.1', puerto), Handler) as httpd:
        url = f'http://127.0.0.1:{puerto}'
        print(f'\nMapa Leaflet disponible en: {url}', flush=True)
        print('Presiona Ctrl+C para detener el servidor web. Docker conserva los datos.', flush=True)
        if abrir_navegador:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServidor detenido.')


def main():
    parser = argparse.ArgumentParser(description='Base de datos + ShakeMap + ATTR + mapa Leaflet')
    parser.add_argument('--puerto', type=int, default=8765, help='Puerto local del mapa (8765)')
    parser.add_argument('--sin-navegador', action='store_true', help='No abrir navegador automaticamente')
    parser.add_argument('--sin-compose', action='store_true', help='Omitir docker compose up -d')
    args = parser.parse_args()
    if not args.sin_compose:
        levantar_docker()
    conexion = conectar(leer_contrasena())
    try:
        cobertura = actualizar_probabilidades(conexion)
        attr = calcular_attr(conexion)
        datos = obtener_datos_mapa(conexion, attr, cobertura)
    finally:
        conexion.close()
    servidor(datos, args.puerto, not args.sin_navegador)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'\nERROR: {exc}', file=sys.stderr)
        sys.exit(1)
