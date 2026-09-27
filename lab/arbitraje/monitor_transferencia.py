"""
Monitor de arbitraje CON TRANSFERENCIA entre Binance y otros exchanges —
SOLO LECTURA, no opera ni usa claves.

Idea a medir: comprar una moneda donde cotiza más barata, transferirla y
venderla donde cotiza más cara (siempre con Binance en uno de los dos lados).

Para cada moneda con par /USDT en Binance y en otro exchange, en ambos
sentidos, calcula el resultado NETO para un capital dado (default 50 USDT):
  comisión de compra + comisión de RETIRO (fija, en la moneda) + comisión de venta,
usando el libro de órdenes real (precio promedio para ese monto, no solo el
mejor precio) cuando la oportunidad pasa un primer filtro.

También registra:
- si el retiro en el origen y el depósito en el destino están habilitados
  (cuando el exchange lo publica sin clave: KuCoin, HTX, Gate);
- cuánto DURA cada oportunidad (una transferencia tarda de segundos a una
  hora: una diferencia que dura 1 minuto no se puede aprovechar);
- diferencias > 20% como SOSPECHOSAS (casi siempre es otra moneda con el
  mismo ticker, o retiros suspendidos).

⚠️ Limitaciones:
- Binance, OKX y MEXC no publican comisiones de retiro sin clave: se estima
  con la mediana de lo que cobran KuCoin/HTX por esa moneda (o 1 USDT).
- No modela el movimiento de precio DURANTE la transferencia.
- ccxt del proyecto es de 2023: Bybit y Bitget no funcionan para escaneo
  completo con esa versión (no se actualiza porque la usa el bot en vivo).

Uso (dejarlo corriendo en una terminal aparte, p. ej. 24 h):
    python -m lab.arbitraje.monitor_transferencia --horas 24
    python -m lab.arbitraje.monitor_transferencia --horas 0.1 --capital 50
"""
import argparse
import csv
import logging
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import ccxt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SALIDA_DIR = "resultados_arbitraje"
EXCHANGES = ["binance", "okx", "kucoin", "gate", "huobi", "mexc"]
# Comisión taker spot aproximada por exchange (cuenta estándar, sin descuentos)
COMISION_TRADING = {"binance": 0.10, "okx": 0.10, "kucoin": 0.10, "gate": 0.20, "huobi": 0.20, "mexc": 0.05}
UMBRAL_SOSPECHOSO_PCT = 20.0
REFRESCO_MONEDAS_SEG = 3600


class InfoRetiros:
    """Comisión de retiro más barata (red habilitada) y estado de retiro/depósito
    por exchange y moneda, cuando el exchange lo publica sin clave."""

    def __init__(self, clientes):
        self.clientes = clientes
        self.datos = {}          # ex -> moneda -> {"fee": float|None, "retiro": bool|None, "deposito": bool|None}
        self.ultima_carga = 0

    def refrescar(self):
        if time.time() - self.ultima_carga < REFRESCO_MONEDAS_SEG:
            return
        for nombre, ex in self.clientes.items():
            try:
                monedas = ex.fetch_currencies()
            except Exception:
                continue
            if not monedas:
                continue
            d = {}
            for codigo, c in monedas.items():
                redes = c.get("networks") or {}
                fees = [r.get("fee") for r in redes.values() if r.get("withdraw") and r.get("fee") is not None]
                d[codigo] = {
                    "fee": min(fees) if fees else c.get("fee"),
                    "retiro": c.get("withdraw"),
                    "deposito": c.get("deposit"),
                }
            self.datos[nombre] = d
        self.ultima_carga = time.time()
        logger.info(f"🔄 Info de retiros/depósitos disponible en: {', '.join(self.datos) or 'ninguno'}")

    def fee_retiro(self, ex, moneda, precio, fee_defecto_usdt):
        """(fee en unidades de la moneda, 'real'|'estimada')."""
        info = self.datos.get(ex, {}).get(moneda)
        if info and info["fee"] is not None:
            return info["fee"], "real"
        conocidas = [d[moneda]["fee"] for d in self.datos.values()
                     if moneda in d and d[moneda]["fee"] is not None]
        if conocidas:
            return statistics.median(conocidas), "estimada"
        return fee_defecto_usdt / precio, "estimada"

    def estado(self, ex, moneda, campo):
        info = self.datos.get(ex, {}).get(moneda)
        return None if not info else info.get(campo)


def _vwap_compra(libro, monto_usdt):
    """Cantidad de moneda que se obtiene gastando monto_usdt (recorre asks)."""
    restante, cantidad = monto_usdt, 0.0
    for precio, vol in libro["asks"]:
        costo = precio * vol
        if costo >= restante:
            return cantidad + restante / precio
        cantidad += vol
        restante -= costo
    return None  # libro sin profundidad suficiente


def _vwap_venta(libro, cantidad):
    """USDT que se obtienen vendiendo 'cantidad' (recorre bids)."""
    restante, usdt = cantidad, 0.0
    for precio, vol in libro["bids"]:
        if vol >= restante:
            return usdt + restante * precio
        usdt += vol * precio
        restante -= vol
    return None


def _neto(capital, qty_comprada, fee_compra, fee_retiro, usdt_venta_por_qty, fee_venta):
    qty = qty_comprada * (1 - fee_compra) - fee_retiro
    if qty <= 0:
        return -100.0
    return (usdt_venta_por_qty(qty) * (1 - fee_venta) / capital - 1) * 100


def _leer_tickers(ex):
    try:
        return ex.fetch_tickers()
    except Exception as e:
        logger.warning(f"⚠️ {ex.id}: error leyendo tickers: {str(e)[:80]}")
        return {}


def main():
    parser = argparse.ArgumentParser(description="Monitor de arbitraje con transferencia (solo lectura)")
    parser.add_argument("--horas", type=float, default=24)
    parser.add_argument("--intervalo", type=float, default=60, help="Segundos entre snapshots")
    parser.add_argument("--capital", type=float, default=50.0, help="USDT por operación")
    parser.add_argument("--volumen-minimo", type=float, default=50_000, help="Volumen 24h mínimo (USDT) en ambos lados")
    parser.add_argument("--umbral-pct", type=float, default=0.0, help="Registrar oportunidades netas por encima de esto")
    parser.add_argument("--fee-retiro-defecto-usdt", type=float, default=1.0)
    parser.add_argument("--exchanges", nargs="+", default=EXCHANGES)
    args = parser.parse_args()

    clientes = {}
    for nombre in args.exchanges:
        try:
            ex = getattr(ccxt, nombre)({"enableRateLimit": True, "options": {"defaultType": "spot"}})
            ex.load_markets()
            clientes[nombre] = ex
        except Exception as e:
            logger.warning(f"⚠️ {nombre}: no disponible ({str(e)[:80]}) — se omite.")
    if "binance" not in clientes or len(clientes) < 2:
        logger.error("❌ Se necesita Binance y al menos otro exchange.")
        return
    otros = [n for n in clientes if n != "binance"]
    retiros = InfoRetiros(clientes)

    os.makedirs(SALIDA_DIR, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M")
    ruta_csv = os.path.join(SALIDA_DIR, f"transferencia_{marca}.csv")
    ruta_resumen = os.path.join(SALIDA_DIR, f"transferencia_{marca}_resumen.txt")
    seguimiento = {}   # (moneda, compra_en, venta_en) -> dict
    snapshots, fin = 0, time.time() + args.horas * 3600

    columnas = ["ts", "moneda", "compra_en", "venta_en", "ask", "bid", "bruto_pct", "neto_pct",
                "fee_retiro", "fee_tipo", "retiro_origen", "deposito_destino", "vol24_min_usdt", "sospechosa"]
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        while time.time() < fin:
            t0 = time.time()
            retiros.refrescar()
            # Todos los exchanges a la vez, para que los precios sean del mismo instante
            with ThreadPoolExecutor(max_workers=len(clientes)) as pool:
                tickers = dict(zip(clientes, pool.map(_leer_tickers, clientes.values())))
            snapshots += 1
            ts = datetime.now().isoformat(timespec="seconds")

            precios = {}  # moneda -> ex -> (bid, ask, vol24)
            for nombre, tk in tickers.items():
                for sym, t in tk.items():
                    if not sym.endswith("/USDT") or ":" in sym:
                        continue
                    bid, ask = t.get("bid"), t.get("ask")
                    if not bid or not ask or bid <= 0 or ask <= 0:
                        continue
                    vol = t.get("quoteVolume") or ((t.get("baseVolume") or 0) * (t.get("last") or 0))
                    precios.setdefault(sym.split("/")[0], {})[nombre] = (bid, ask, vol or 0)

            for moneda, por_ex in precios.items():
                if "binance" not in por_ex:
                    continue
                for otro in otros:
                    if otro not in por_ex:
                        continue
                    for compra_en, venta_en in (("binance", otro), (otro, "binance")):
                        _, ask, vol_a = por_ex[compra_en]
                        bid, _, vol_b = por_ex[venta_en]
                        if min(vol_a, vol_b) < args.volumen_minimo:
                            continue
                        bruto = (bid / ask - 1) * 100
                        if bruto <= 0:
                            continue
                        fee_retiro, fee_tipo = retiros.fee_retiro(compra_en, moneda, ask, args.fee_retiro_defecto_usdt)
                        fc = COMISION_TRADING.get(compra_en, 0.1) / 100
                        fv = COMISION_TRADING.get(venta_en, 0.1) / 100
                        # Filtro rápido con el mejor precio; solo si pasa, se consulta el libro
                        neto_top = _neto(args.capital, args.capital / ask, fc, fee_retiro, lambda q: q * bid, fv)
                        if neto_top <= args.umbral_pct:
                            continue
                        sospechosa = bruto > UMBRAL_SOSPECHOSO_PCT
                        neto = neto_top
                        if not sospechosa:
                            try:
                                libro_a = clientes[compra_en].fetch_order_book(f"{moneda}/USDT", limit=20)
                                libro_b = clientes[venta_en].fetch_order_book(f"{moneda}/USDT", limit=20)
                                qty = _vwap_compra(libro_a, args.capital)
                                usdt_bruto = _vwap_venta(libro_b, qty) if qty else None
                                if not qty or not usdt_bruto:
                                    continue
                                neto = _neto(args.capital, qty, fc, fee_retiro,
                                             lambda q: _vwap_venta(libro_b, q) or 0.0, fv)
                            except Exception:
                                continue
                            if neto <= args.umbral_pct:
                                continue
                            # Diferencia medida con el LIBRO (no con el ticker): si es enorme, o si el
                            # neto supera al bruto del ticker, los datos no son coherentes (ticker viejo,
                            # otra moneda con el mismo símbolo) -> sospechosa.
                            bruto_libro = (usdt_bruto / args.capital - 1) * 100
                            sospechosa = bruto_libro > UMBRAL_SOSPECHOSO_PCT or neto > bruto

                        fila = {
                            "ts": ts, "moneda": moneda, "compra_en": compra_en, "venta_en": venta_en,
                            "ask": ask, "bid": bid, "bruto_pct": round(bruto, 3), "neto_pct": round(neto, 3),
                            "fee_retiro": fee_retiro, "fee_tipo": fee_tipo,
                            "retiro_origen": retiros.estado(compra_en, moneda, "retiro"),
                            "deposito_destino": retiros.estado(venta_en, moneda, "deposito"),
                            "vol24_min_usdt": round(min(vol_a, vol_b)), "sospechosa": sospechosa,
                        }
                        w.writerow(fila)
                        clave = (moneda, compra_en, venta_en)
                        s = seguimiento.setdefault(clave, {"primera": ts, "veces": 0, "max_neto": -100.0,
                                                           "bloqueada": False, "sospechosa": sospechosa})
                        s["ultima"], s["veces"] = ts, s["veces"] + 1
                        s["max_neto"] = max(s["max_neto"], neto)
                        s["bloqueada"] |= fila["retiro_origen"] is False or fila["deposito_destino"] is False
                        s["sospechosa"] |= sospechosa
            f.flush()

            if snapshots % 30 == 0:
                _escribir_resumen(ruta_resumen, seguimiento, snapshots, args, list(clientes))
                logger.info(f"📸 {snapshots} snapshots | {len(seguimiento)} oportunidades distintas vistas "
                            f"(resumen en {ruta_resumen})")
            time.sleep(max(0, args.intervalo - (time.time() - t0)))

    texto = _escribir_resumen(ruta_resumen, seguimiento, snapshots, args, list(clientes))
    print(texto)
    print(f"Detalle: {ruta_csv}")


def _escribir_resumen(ruta, seguimiento, snapshots, args, exchanges) -> str:
    def minutos(s):
        return (datetime.fromisoformat(s["ultima"]) - datetime.fromisoformat(s["primera"])).total_seconds() / 60

    viables = {k: s for k, s in seguimiento.items() if not s["bloqueada"] and not s["sospechosa"]}
    lineas = [
        "=" * 100,
        f"ARBITRAJE CON TRANSFERENCIA — {snapshots} snapshots cada {args.intervalo:.0f}s "
        f"({snapshots * args.intervalo / 3600:.1f} h), capital {args.capital} USDT",
        f"Exchanges: {', '.join(exchanges)} | volumen mínimo {args.volumen_minimo:,.0f} USDT/24h",
        "=" * 100,
        f"Oportunidades distintas con neto > {args.umbral_pct}%: {len(seguimiento)}",
        f"  - sospechosas (bruto > {UMBRAL_SOSPECHOSO_PCT}%, probable otra moneda / retiros suspendidos): "
        f"{sum(s['sospechosa'] for s in seguimiento.values())}",
        f"  - con retiro o depósito BLOQUEADO confirmado: {sum(s['bloqueada'] and not s['sospechosa'] for s in seguimiento.values())}",
        f"  - viables (sin bloqueo conocido): {len(viables)}",
    ]
    for umbral in (0.5, 1.0, 2.0):
        lineas.append(f"      neto máx > {umbral}%: {sum(s['max_neto'] > umbral for s in viables.values())}")
    duraderas = {k: s for k, s in viables.items() if minutos(s) >= 10}
    lineas.append(f"      que duraron ≥ 10 min (tiempo para transferir): {len(duraderas)}")

    top = sorted(viables.items(), key=lambda kv: -kv[1]["max_neto"])[:25]
    if top:
        lineas += ["", "TOP VIABLES (neto con el libro real para el capital indicado):",
                   f"  {'moneda':<10} {'compra en':<9} {'vende en':<9} {'neto máx%':>9} {'USDT/op':>8} "
                   f"{'veces':>6} {'duró(min)':>9}"]
        for (moneda, a, b), s in top:
            lineas.append(f"  {moneda:<10} {a:<9} {b:<9} {s['max_neto']:>9.2f} "
                          f"{args.capital * s['max_neto'] / 100:>8.2f} {s['veces']:>6} {minutos(s):>9.0f}")
    lineas += ["", "Cómo leerlo: una oportunidad sirve si el neto supera ~0.5% Y dura más que la transferencia",
               "(≥10 min para redes rápidas). Las de Binance/OKX/MEXC como origen usan comisión de retiro estimada.",
               "⚠️ Una diferencia grande que dura HORAS casi siempre es: otra moneda con el mismo ticker, o",
               "retiros/depósitos suspendidos en un lado (Binance/OKX/MEXC no publican su estado sin clave).",
               "Antes de operar cualquiera: confirmar a mano en ambos exchanges que el retiro/depósito de esa red",
               "está habilitado y cuánto cobra el retiro."]
    texto = "\n".join(lineas)
    try:
        with open(ruta, "w", encoding="utf-8") as fr:
            fr.write(texto + "\n")
    except OSError:
        pass
    return texto


if __name__ == "__main__":
    main()
