
# Resiliencia de la infraestructura de Internet de Chile

## Descripción

Proyecto de análisis de la resiliencia de la infraestructura de fibra óptica de Chile frente al terremoto del Maule de 2010 (Mw 8,8).

Se utilizan PostgreSQL, PostGIS, pgRouting, Python, USGS ShakeMap y Leaflet para representar la infraestructura, estimar probabilidades de falla y calcular la conectividad mediante Average Two-Terminal Reliability (ATTR).

## Tecnologías

- Python 3.11
- PostgreSQL 16
- PostGIS 3.5
- pgRouting 3.7.3
- Docker Compose
- Leaflet
- NumPy y SciPy

## Datos utilizados

- 1.915 enlaces de fibra óptica.
- 2.079 vértices de la topología.
- 563 nodos clave.
- 4 nodos estratégicos modelados.
- Terremoto del Maule de 2010, obtenido del USGS.
- ShakeMap del USGS con 235.204 puntos geográficos.

## Requisitos previos

Tener instalados:

- Python 3.11
- Docker Desktop con el motor en ejecución
- Git

## Instalación

### 1. Clonar el repositorio

```powershell
git clone URL_DEL_REPOSITORIO
cd tarea2ruteo
```

Reemplazar `URL_DEL_REPOSITORIO` por la URL correspondiente de GitHub.

### 2. Crear un entorno virtual

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Instalar las dependencias

```powershell
python -m pip install -r requirements.txt
```

### 4. Configurar PostgreSQL

Crear un archivo `.env` en la carpeta principal:

```dotenv
POSTGRES_PASSWORD=CAMBIAR_POR_UNA_CONTRASENA_SEGURA
```

No subir este archivo a GitHub.

### 5. Ejecutar el proyecto

Con Docker Desktop abierto, ejecutar:

```powershell
python main.py
```

El programa realiza automáticamente:

1. Inicio de PostgreSQL mediante Docker Compose.
2. Carga de la infraestructura desde `main.sql` en una base nueva.
3. Verificación de la topología.
4. Cálculo de probabilidades de falla utilizando ShakeMap.
5. Cálculo del ATTR inicial y posterior mediante 5.000 simulaciones Monte Carlo.
6. Inicio del servidor web con el mapa Leaflet.

### 6. Abrir el mapa

Acceder desde el navegador a:

http://127.0.0.1:8765

El mapa permite visualizar:

- Enlaces coloreados según probabilidad de falla.
- Nodos clave y nodos estratégicos.
- Epicentro del terremoto de 2010.
- Información individual de cada enlace.
- Resultados del ATTR antes y después del terremoto.

Para detener el servidor web, presionar `Ctrl + C` en la terminal.

## Resultados obtenidos

| Indicador | Resultado |
|---|---:|
| ATTR inicial | 0,012572 |
| ATTR posterior | 0,010691 |
| Reducción relativa | 14,96 % |
| Enlaces con probabilidad de falla positiva | 333 |
| Enlaces con probabilidad superior al 50 % | 50 |

## Limitaciones

El modelo utiliza una representación parcial de la infraestructura de Internet chilena.

Las probabilidades de falla son estimaciones y no corresponden a daños históricos observados.

Los enlaces fuera de la cobertura ShakeMap reciben probabilidad cero como supuesto computacional. Esto no implica ausencia de peligro sísmico.

La simulación considera fallas independientes entre enlaces, condicionadas a sus probabilidades estimadas.

## Archivos principales

- `main.py`: ejecución principal del proyecto y aplicación Leaflet.
- `main.sql`: respaldo reproducible de la base de datos.
- `compose.yaml`: configuración de PostgreSQL mediante Docker.
- `calcular_attr.py`: cálculo Monte Carlo del ATTR.
- `calcular_probabilidades.py`: modelo probabilístico sísmico.
- `cargar_infraestructura.py`: importación de fibra óptica.
- `cargar_terremoto.py`: importación del evento sísmico.
- `requirements.txt`: dependencias de Python.
- `data/`: infraestructura geográfica y datos sísmicos.

## Fuente de los datos sísmicos

United States Geological Survey (USGS).

Evento: `official20100227063411530_30`

https://earthquake.usgs.gov/
