from datetime import datetime, timedelta
import os
from typing import List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import requests
import uvicorn

# Carga las variables del archivo .env
load_dotenv()

# Configuración leída desde variables de entorno (sin hardcodear)
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 5001))
NASA_POWER_URL = os.getenv(
    "NASA_POWER_URL", "https://power.larc.nasa.gov/api/temporal/daily/point"
)
NASA_TIMEOUT = int(os.getenv("NASA_TIMEOUT", 60))
DEFAULT_ANIO_INICIO = int(os.getenv("DEFAULT_ANIO_INICIO", 2016))
DEFAULT_ANIO_FIN = int(os.getenv("DEFAULT_ANIO_FIN", 2025))

app = FastAPI(
    title="Servicio de Clima Histórico - Field Shift",
    description=(
        "Microservicio independiente que obtiene datos históricos (10 años) y"
        " del año en curso de NASA POWER."
    ),
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def inicio():
    return {
        "estado": "activo",
        "servicio": "Clima Histórico - Field Shift",
        "puerto": PORT,
        "documentacion": f"http://127.0.0.1:{PORT}/docs",
    }


# --- MODELOS DE ENTRADA ---
class Coordenada(BaseModel):
    lat: float = Field(..., ge=-90, le=90, example=21.0512)
    lon: float = Field(..., ge=-180, le=180, example=-101.6534)


class SolicitudParcela(BaseModel):
    latitud: Optional[float] = Field(None, ge=-90, le=90, example=21.0512)
    longitud: Optional[float] = Field(None, ge=-180, le=180, example=-101.6534)
    poligono: Optional[List[Coordenada]] = Field(
        None,
        description="Lista de vértices si el usuario dibujó un área en el mapa",
    )
    anio_inicio: int = Field(DEFAULT_ANIO_INICIO, ge=1981, le=2025)
    anio_fin: int = Field(DEFAULT_ANIO_FIN, ge=1981, le=2025)


# --- FUNCIONES AUXILIARES ---
NOMBRES_MESES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]


def calcular_centroide(poligono: List[Coordenada]) -> tuple[float, float]:
    lat_prom = sum(p.lat for p in poligono) / len(poligono)
    lon_prom = sum(p.lon for p in poligono) / len(poligono)
    return round(lat_prom, 6), round(lon_prom, 6)


def promedio_seguro(lista: list) -> float:
    return round(sum(lista) / len(lista), 2) if lista else 0.0


def consultar_nasa_power(
    lat: float, lon: float, fecha_inicio: str, fecha_fin: str
) -> dict:
    """Consulta un rango de fechas a NASA POWER usando la configuración del .env."""
    parametros = {
        "parameters": (
            "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN"
        ),
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": fecha_inicio,
        "end": fecha_fin,
        "format": "JSON",
    }

    try:
        respuesta = requests.get(
            NASA_POWER_URL, params=parametros, timeout=NASA_TIMEOUT
        )
        if respuesta.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"NASA POWER devolvió error {respuesta.status_code}:"
                    f" {respuesta.text[:200]}"
                ),
            )
        return respuesta.json().get("properties", {}).get("parameter", {})
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail="La API de NASA POWER tardó demasiado en responder.",
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502, detail=f"Error de conexión con NASA POWER: {str(e)}"
        )


def procesar_bloque_climatico(
    datos_nasa: dict, total_anios: int, es_anio_en_curso: bool = False
) -> dict:
    """Procesa los datos diarios de NASA POWER y devuelve indicadores y tabla mensual."""
    t2m = datos_nasa.get("T2M", {})
    t2m_max = datos_nasa.get("T2M_MAX", {})
    t2m_min = datos_nasa.get("T2M_MIN", {})
    precip = datos_nasa.get("PRECTOTCORR", {})
    rh2m = datos_nasa.get("RH2M", {})
    rad = datos_nasa.get("ALLSKY_SFC_SW_DWN", {})

    mensual = {
        m: {
            "t_prom": [],
            "t_max": [],
            "t_min": [],
            "lluvia": [],
            "hum": [],
            "rad": [],
        }
        for m in range(1, 13)
    }

    dias_calor_extremo = 0
    dias_helada = 0
    dias_torrencial = 0
    gdd_total = 0.0

    for fecha_str in t2m.keys():
        if any(
            dic.get(fecha_str, -999) == -999
            for dic in [t2m, t2m_max, t2m_min, precip, rh2m, rad]
        ):
            continue

        mes = int(fecha_str[4:6])
        v_tprom = t2m[fecha_str]
        v_tmax = t2m_max[fecha_str]
        v_tmin = t2m_min[fecha_str]
        v_lluvia = precip[fecha_str]
        v_hum = rh2m[fecha_str]
        v_rad = rad[fecha_str]

        mensual[mes]["t_prom"].append(v_tprom)
        mensual[mes]["t_max"].append(v_tmax)
        mensual[mes]["t_min"].append(v_tmin)
        mensual[mes]["lluvia"].append(v_lluvia)
        mensual[mes]["hum"].append(v_hum)
        mensual[mes]["rad"].append(v_rad)

        if v_tmax > 35.0:
            dias_calor_extremo += 1
        if v_tmin < 0.0:
            dias_helada += 1
        if v_lluvia > 20.0:
            dias_torrencial += 1

        gdd_total += max(0.0, ((v_tmax + v_tmin) / 2.0) - 10.0)

    climatologia_mensual = []
    lluvia_total = 0.0
    lluvia_pv = 0.0
    lluvia_oi = 0.0
    rad_pv_lista = []

    todas_tprom, todas_tmax, todas_tmin, todas_hum, todas_rad = (
        [],
        [],
        [],
        [],
        [],
    )

    for m in range(1, 13):
        datos_m = mensual[m]
        if es_anio_en_curso and len(datos_m["t_prom"]) == 0:
            continue

        lluvia_mes = round(sum(datos_m["lluvia"]) / total_anios, 1)
        lluvia_total += lluvia_mes

        if 5 <= m <= 10:
            lluvia_pv += lluvia_mes
            rad_pv_lista.extend(datos_m["rad"])
        else:
            lluvia_oi += lluvia_mes

        todas_tprom.extend(datos_m["t_prom"])
        todas_tmax.extend(datos_m["t_max"])
        todas_tmin.extend(datos_m["t_min"])
        todas_hum.extend(datos_m["hum"])
        todas_rad.extend(datos_m["rad"])

        climatologia_mensual.append({
            "mes": m,
            "nombre": NOMBRES_MESES[m - 1],
            "dias_registrados": (
                len(datos_m["t_prom"]) if es_anio_en_curso else None
            ),
            "temp_prom_c": promedio_seguro(datos_m["t_prom"]),
            "temp_max_c": promedio_seguro(datos_m["t_max"]),
            "temp_min_c": promedio_seguro(datos_m["t_min"]),
            "precipitacion_mm": lluvia_mes,
            "humedad_relativa_pct": promedio_seguro(datos_m["hum"]),
            "radiacion_mj_m2_dia": promedio_seguro(datos_m["rad"]),
        })

    return {
        "indicadores_clave": {
            "temperatura": {
                "promedio_c": promedio_seguro(todas_tprom),
                "maxima_promedio_c": promedio_seguro(todas_tmax),
                "minima_promedio_c": promedio_seguro(todas_tmin),
                "dias_calor_extremo_mayor_35c": round(
                    dias_calor_extremo / total_anios, 1
                ),
                "dias_helada_menor_0c": round(dias_helada / total_anios, 1),
                "grados_dia_acumulados": round(gdd_total / total_anios, 1),
            },
            "precipitacion": {
                "acumulada_mm": round(lluvia_total, 1),
                "acumulada_ciclo_pv_may_oct_mm": round(lluvia_pv, 1),
                "acumulada_ciclo_oi_nov_abr_mm": round(lluvia_oi, 1),
                "dias_lluvia_torrencial_mayor_20mm": round(
                    dias_torrencial / total_anios, 1
                ),
            },
            "humedad": {
                "humedad_relativa_promedio_pct": promedio_seguro(todas_hum),
                "humedad_relativa_minima_pct": (
                    round(min(todas_hum), 1) if todas_hum else 0.0
                ),
            },
            "radiacion": {
                "radiacion_solar_promedio_mj_m2_dia": promedio_seguro(
                    todas_rad
                ),
                "radiacion_ciclo_pv_mj_m2_dia": promedio_seguro(rad_pv_lista),
            },
        },
        "climatologia_mensual": climatologia_mensual,
    }


# --- ENDPOINT PRINCIPAL ---
@app.post("/api/v1/clima-historico")
def obtener_clima_historico(solicitud: SolicitudParcela):
    if solicitud.poligono and len(solicitud.poligono) >= 3:
        lat, lon = calcular_centroide(solicitud.poligono)
    elif solicitud.latitud is not None and solicitud.longitud is not None:
        lat, lon = solicitud.latitud, solicitud.longitud
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Debes enviar 'latitud' y 'longitud', o un 'poligono' con al"
                " menos 3 coordenadas."
            ),
        )

    if solicitud.anio_inicio > solicitud.anio_fin:
        raise HTTPException(
            status_code=400,
            detail="El año de inicio no puede ser mayor al año final.",
        )

    # 1. Consultar histórico de 10 años
    datos_decada = consultar_nasa_power(
        lat, lon, f"{solicitud.anio_inicio}0101", f"{solicitud.anio_fin}1231"
    )
    total_anios = (solicitud.anio_fin - solicitud.anio_inicio) + 1
    resultado_decada = procesar_bloque_climatico(
        datos_decada, total_anios, es_anio_en_curso=False
    )

    # 2. Consultar año actual hasta hace 7 días
    anio_actual = datetime.now().year
    fecha_limite_nasa = datetime.now() - timedelta(days=7)
    fecha_fin_actual_str = fecha_limite_nasa.strftime("%Y%m%d")

    datos_2026 = consultar_nasa_power(
        lat, lon, f"{anio_actual}0101", fecha_fin_actual_str
    )
    resultado_2026 = procesar_bloque_climatico(
        datos_2026, total_anios=1, es_anio_en_curso=True
    )

    return {
        "servicio": "clima_historico",
        "fuente": "NASA_POWER",
        "parcela": {"latitud_centroide": lat, "longitud_centroide": lon},
        "historico_decada": {
            "periodo": f"{solicitud.anio_inicio}-{solicitud.anio_fin}",
            **resultado_decada,
        },
        "anio_en_curso": {
            "anio": anio_actual,
            "datos_disponibles_hasta": fecha_limite_nasa.strftime("%Y-%m-%d"),
            **resultado_2026,
        },
    }


# --- ARRANQUE DEL SERVIDOR ---
if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
