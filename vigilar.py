"""Vigilante de cargadores Iberdrola en Alcoy.

Consulta cada minuto el estado de los cargadores de Iberdrola de la zona
y avisa por Telegram en cuanto una toma queda libre.

Modos (variable MODO):
  vigilar  -> repite hasta que algo quede libre o se acabe el tiempo (HORAS)
  estado   -> manda un resumen del estado actual y termina

Variables de entorno:
  TELEGRAM_TOKEN    token del bot (obligatorio)
  TELEGRAM_CHAT_ID  chat donde avisar (obligatorio)
  MODO              vigilar | estado          (por defecto: vigilar)
  HORAS             horas máximas vigilando   (por defecto: 3, máx. 5.5)
  INTERVALO         segundos entre consultas  (por defecto: 60)
  CUPR_IDS          ids concretos a vigilar, separados por comas (opcional;
                    si no se pone, vigila todos los de Iberdrola de la zona)
  ZONA              lat_min,lat_max,lon_min,lon_max (por defecto: Alcoy)

Usa la misma API que el mapa público de iberdrola.es. No es oficial:
si Iberdrola la cambia, el vigilante avisará de que no puede consultar.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

BASE = "https://www.iberdrola.es/o/webclipb/iberdrola/puntosrecargacontroller"
MAPA = "https://www.iberdrola.es/movilidad-electrica/puntos-de-recarga"
ZONA_ALCOY = (38.670, 38.725, -0.515, -0.440)  # lat_min, lat_max, lon_min, lon_max

ESTADOS = {
    "AVAILABLE": "🟢 libre",
    "OCCUPIED": "🔴 ocupado",
    "EV_CONNECTED": "🔴 coche conectado",
    "RESERVED": "🟡 reservado",
    "OUT_OF_SERVICE": "⚫ fuera de servicio",
}


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def telegram(texto: str) -> None:
    token = os.environ["TELEGRAM_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    datos = urllib.parse.urlencode({
        "chat_id": chat,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for intento in range(3):
        try:
            urllib.request.urlopen(url, data=datos, timeout=20).read()
            return
        except Exception as e:  # noqa: BLE001
            print(f"Error enviando a Telegram ({intento + 1}/3): {e}", file=sys.stderr)
            time.sleep(3)


# --------------------------------------------------------------------------
# Iberdrola
# --------------------------------------------------------------------------

class Bloqueado(RuntimeError):
    """Iberdrola ha rechazado la consulta (normalmente 403 del antibots)."""


class Iberdrola:
    def __init__(self) -> None:
        # curl_cffi imita la "huella" de un Chrome real, lo que ayuda a pasar
        # el filtro antibots de la web. Si no está, se usa requests normal.
        try:
            from curl_cffi import requests as cr
            self.s = cr.Session(impersonate="chrome")
        except ImportError:
            import requests
            self.s = requests.Session()
            self.s.headers["user-agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            )
        self.s.headers.update({
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "es-ES,es;q=0.9",
            "origin": "https://www.iberdrola.es",
            "referer": MAPA,
            "x-requested-with": "XMLHttpRequest",
        })
        self.calentar()

    def calentar(self) -> None:
        """Abre la página del mapa para obtener las cookies que pide la web."""
        try:
            self.s.get(MAPA, timeout=20)
        except Exception as e:  # noqa: BLE001
            print(f"Aviso: no se pudo abrir el mapa ({e})", file=sys.stderr)

    def _post(self, op: str, dto: dict) -> list[dict]:
        r = self.s.post(
            f"{BASE}/{op}",
            data=json.dumps({"dto": dto, "language": "es"}),
            headers={"content-type": "application/json; charset=UTF-8"},
            timeout=20,
        )
        if r.status_code == 403:
            raise Bloqueado(f"{op}: HTTP 403")
        if r.status_code != 200:
            raise RuntimeError(f"{op}: HTTP {r.status_code}")
        if "json" not in r.headers.get("content-type", ""):
            raise Bloqueado(f"{op}: respuesta no JSON")
        cuerpo = r.json()
        if cuerpo.get("errorAjax") or cuerpo.get("serviceException"):
            raise RuntimeError(f"{op}: error del servidor {cuerpo.get('errorAjax')!r}")
        return cuerpo.get("entidad") or []

    def listar(self, zona: tuple[float, float, float, float]) -> list[dict]:
        lat_min, lat_max, lon_min, lon_max = zona
        return self._post("getListarPuntosRecarga", {
            "chargePointTypesCodes": ["P"],  # P = red propia de Iberdrola
            "socketStatus": [],
            "advantageous": False,
            "connectorsType": [],
            "loadSpeed": [],
            "latitudeMin": lat_min,
            "latitudeMax": lat_max,
            "longitudeMin": lon_min,
            "longitudeMax": lon_max,
        })

    def detalle(self, ids: list[int]) -> list[dict]:
        salida: list[dict] = []
        for i in range(0, len(ids), 20):
            salida += self._post("getDatosPuntoRecarga", {"cuprId": ids[i:i + 20]})
        return salida


# --------------------------------------------------------------------------
# Interpretar respuestas
# --------------------------------------------------------------------------

def tomas(punto: dict) -> list[dict]:
    """Devuelve las tomas (conectores) de un punto con su estado."""
    loc = punto.get("locationData") or {}
    if NOMBRES.get(loc.get("cuprId"), "").lower() == "ignorar":
        return []
    salida = []
    for n, ls in enumerate(punto.get("logicalSocket") or [], start=1):
        fisicas = ls.get("physicalSocket") or [{}]
        ps = fisicas[0] or {}
        estado = ((ls.get("status") or {}).get("statusCode")
                  or (ps.get("status") or {}).get("statusCode")
                  or "DESCONOCIDO")
        t = {
            "clave": f"{loc.get('cuprId')}-{ls.get('logicalSocketId', n)}",
            "num": n,
            "estado": estado,
            "tipo": (ps.get("socketType") or {}).get("socketName"),
            "kw": ps.get("maxPower"),
            "precio": ((ps.get("appliedRate") or {}).get("recharge") or {}).get("finalPrice"),
        }
        if relevante(t):
            salida.append(t)
    return salida


def relevante(t: dict) -> bool:
    """Filtra las tomas que interesan: gratuitas y sin enchufes domésticos."""
    if os.environ.get("SOLO_GRATIS", "1") != "0" and (t["precio"] or 0) > 0:
        return False
    excluir = os.environ.get("EXCLUIR_TIPOS") or "Schuko"  # "ninguno" = no excluir
    tipos = {x.strip().lower() for x in excluir.split(",") if x.strip()}
    return (t["tipo"] or "").lower() not in tipos


def cargar_nombres() -> dict[int, str]:
    """Lee nombres.txt (id = nombre) para mostrar nombres propios."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nombres.txt")
    nombres: dict[int, str] = {}
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                linea = linea.split("#", 1)[0].strip()
                if "=" in linea:
                    k, _, val = linea.partition("=")
                    if k.strip().isdigit() and val.strip():
                        nombres[int(k)] = val.strip()
    except FileNotFoundError:
        pass
    return nombres


NOMBRES = cargar_nombres()


def nombre(punto: dict) -> str:
    loc = punto.get("locationData") or {}
    if loc.get("cuprId") in NOMBRES:
        return NOMBRES[loc["cuprId"]]
    dire = ((loc.get("supplyPointData") or {}).get("cpAddress") or {})
    calle = " ".join(x for x in (dire.get("streetName"), dire.get("streetNum")) if x)
    return calle or loc.get("cuprName") or f"Punto {loc.get('cuprId')}"


def enlace_mapa(punto: dict) -> str:
    loc = punto.get("locationData") or {}
    return f"https://www.google.com/maps/search/?api=1&query={loc.get('latitude')},{loc.get('longitude')}"


def describe_toma(t: dict) -> str:
    extra = ", ".join(x for x in (t["tipo"], f"{t['kw']} kW" if t["kw"] else None) if x)
    estado = ESTADOS.get(t["estado"], t["estado"].lower())
    return f"  Toma {t['num']}{f' ({extra})' if extra else ''}: {estado}"


def resumen(puntos: list[dict]) -> str:
    lineas = []
    for p in puntos:
        if not tomas(p):
            continue  # de pago, o solo con tomas excluidas
        cid = (p.get("locationData") or {}).get("cuprId")
        lineas.append(f"📍 <b>{nombre(p)}</b> <i>(id {cid})</i>")
        lineas += [describe_toma(t) for t in tomas(p)] or ["  (sin datos de tomas)"]
    return "\n".join(lineas) if lineas else "No he encontrado cargadores de Iberdrola en la zona."


def libres(puntos: list[dict]) -> list[tuple[dict, dict]]:
    return [(p, t) for p in puntos for t in tomas(p) if t["estado"] == "AVAILABLE"]


def aviso_libre(encontrados: list[tuple[dict, dict]]) -> str:
    lineas = ["⚡️ <b>¡Cargador libre!</b> Abre la app de Iberdrola y resérvalo ya.\n"]
    vistos = set()
    for p, t in encontrados:
        cid = (p.get("locationData") or {}).get("cuprId")
        if cid not in vistos:
            vistos.add(cid)
            lineas.append(f"📍 <b>{nombre(p)}</b> · <a href=\"{enlace_mapa(p)}\">mapa</a>")
        lineas.append(describe_toma(t))
    lineas.append("\nHe dejado de vigilar. Escribe /vigilar si lo necesitas otra vez.")
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# Programa principal
# --------------------------------------------------------------------------

def leer_zona() -> tuple[float, float, float, float]:
    z = os.environ.get("ZONA", "").strip()
    if not z:
        return ZONA_ALCOY
    a = [float(x) for x in z.split(",")]
    return (a[0], a[1], a[2], a[3])


def obtener_ids(api: Iberdrola) -> list[int]:
    fijos = os.environ.get("CUPR_IDS", "").strip()
    if fijos:
        return [int(x) for x in fijos.replace(" ", "").split(",") if x]
    puntos = api.listar(leer_zona())
    return sorted({(p.get("locationData") or {}).get("cuprId") for p in puntos} - {None})


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c)).strip()


def aplicar_filtro(puntos: list[dict], filtro: str) -> tuple[list[dict], list[str]]:
    """Se queda con los cargadores pedidos en Telegram (p. ej. "batoi, collao").

    Cada palabra (o trozo separado por comas) se busca dentro del nombre,
    sin importar mayúsculas ni tildes; también vale el id.
    Devuelve (cargadores elegidos, trozos que no coinciden con ninguno).
    """
    validos = [p for p in puntos if tomas(p)]
    filtro = filtro.strip()
    if not filtro:
        return validos, []

    relleno = {"de", "del", "la", "las", "los", "el", "y", "e", "i", "en", "d", "l"}

    def palabras(texto: str) -> list[str]:
        limpio = "".join(c if c.isalnum() else " " for c in normalizar(texto))
        return [w for w in limpio.split() if w not in relleno]

    def coincide(ps: list[str]) -> list[dict]:
        """Cargadores cuyo nombre contiene todas las palabras (vale el principio)."""
        hallados = []
        for p in validos:
            cid = str((p.get("locationData") or {}).get("cuprId"))
            del_nombre = palabras(nombre(p))
            if ps == [cid] or (ps and all(any(n.startswith(w) for n in del_nombre) for w in ps)):
                hallados.append(p)
        return hallados

    # Trozos: separados por comas, o agrupando palabras seguidas que
    # formen un mismo nombre ("pont sant jordi batoi" -> 2 cargadores).
    grupos: list[list[str]] = []
    if "," in filtro:
        grupos = [palabras(t) for t in filtro.split(",") if palabras(t)]
    else:
        ws, i = palabras(filtro), 0
        while i < len(ws):
            for j in range(len(ws), i, -1):
                if coincide(ws[i:j]) or j == i + 1:
                    grupos.append(ws[i:j])
                    i = j
                    break
    elegidos, sin_coincidencia = [], []
    for grupo in grupos:
        trozo = " ".join(grupo)
        hallados = coincide(grupo)
        if not hallados:
            sin_coincidencia.append(trozo.strip())
        for p in hallados:
            if p not in elegidos:
                elegidos.append(p)
    return elegidos, sin_coincidencia


def consultar(api: Iberdrola, ids: list[int]) -> list[dict]:
    """Consulta con un reintento (recalentando cookies) si hay bloqueo."""
    try:
        return api.detalle(ids)
    except Bloqueado:
        api.calentar()
        time.sleep(5)
        return api.detalle(ids)


def main() -> int:
    modo = os.environ.get("MODO", "vigilar").strip().lower()
    horas = min(float(os.environ.get("HORAS") or 3), 5.5)
    intervalo = max(int(os.environ.get("INTERVALO") or 60), 30)

    api = Iberdrola()
    try:
        ids = obtener_ids(api)
        if not ids:
            telegram("No he encontrado cargadores de Iberdrola en la zona configurada.")
            return 1
        puntos = consultar(api, ids)
    except Exception as e:  # noqa: BLE001
        telegram(f"❌ No he podido consultar a Iberdrola: {e}\n"
                 "Puede que hayan cambiado su web. Prueba más tarde con /estado.")
        return 1

    todos = puntos
    puntos, sin_coincidencia = aplicar_filtro(todos, os.environ.get("FILTRO", ""))
    nota = ""
    if sin_coincidencia:
        nota = f"⚠️ No encuentro: {', '.join(sin_coincidencia)}\n\n"
    if not puntos:
        disponibles = ", ".join(nombre(p) for p in aplicar_filtro(todos, "")[0])
        telegram(f"{nota}Ninguno de los nombres coincide con tus cargadores.\n"
                 f"Tus cargadores son: {disponibles}")
        return 1
    ids = [(p.get("locationData") or {}).get("cuprId") for p in puntos]

    if modo == "estado":
        telegram(f"{nota}📊 <b>Estado ahora</b>\n\n{resumen(puntos)}")
        return 0

    ya_libres = libres(puntos)
    if ya_libres:
        telegram(nota + aviso_libre(ya_libres))
        return 0

    telegram(f"{nota}👀 Vigilando {sum(1 for p in puntos if tomas(p))} cargador(es) durante {horas:g} h "
             f"(cada {intervalo} s). Te aviso en cuanto quede uno libre.\n\n"
             f"{resumen(puntos)}\n\nEscribe /parar para cancelar.")

    fin = time.time() + horas * 3600
    fallos = 0
    avisado_fallos = False
    while time.time() < fin:
        time.sleep(intervalo + random.uniform(-8, 8))
        try:
            puntos = [p for p in consultar(api, ids)
                      if (p.get("locationData") or {}).get("cuprId") in ids]
            fallos = 0
            if avisado_fallos:
                telegram("✅ Vuelvo a poder consultar a Iberdrola. Sigo vigilando.")
                avisado_fallos = False
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"Fallo {fallos}: {e}", file=sys.stderr)
            if fallos == 5 and not avisado_fallos:
                telegram("⚠️ Iberdrola está rechazando mis consultas. Sigo intentándolo, "
                         "pero ahora mismo no puedo garantizarte el aviso.")
                avisado_fallos = True
            if fallos >= 20:
                telegram("❌ Llevo 20 consultas fallidas seguidas y paro. "
                         "Prueba de nuevo más tarde con /vigilar.")
                return 1
            continue

        encontrados = libres(puntos)
        if encontrados:
            telegram(aviso_libre(encontrados))
            return 0
        print(time.strftime("%H:%M:%S"), "sin cambios")

    telegram(f"⌛️ Han pasado {horas:g} h y no se ha liberado ninguno. "
             "Escribe /vigilar para seguir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
