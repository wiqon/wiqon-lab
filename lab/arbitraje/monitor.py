"""
Monitor de arbitraje — SOLO LECTURA, no ejecuta ninguna orden ni usa claves.

Mide si existen oportunidades de arbitraje que sobrevivan a las comisiones,
y de qué tamaño, antes de escribir una sola línea de ejecución (regla de oro
del proyecto: evidencia primero).

Dos modos:

  triangular  Dentro de Binance Spot: USDT -> A -> B -> USDT usando el mejor
              bid/ask de cada par (bookTicker). Descuenta la comisión en cada
              una de las 3 patas y calcula el tamaño máximo ejecutable con el
              volumen del primer nivel del libro.

  inter       Entre exchanges (Binance, OKX, Bybit, KuCoin) para los símbolos
              del scanner: comprar en el ask de uno y vender en el bid de otro,
              descontando comisión en ambos. Supone inventario ya repartido en
              los dos exchanges (sin transferencias, que tardan minutos y cuestan).

⚠️ Limitaciones que hay que tener en cuenta al leer los resultados:
- El snapshot de bookTicker de todos los pares NO es atómico: cada par puede
  venir de un instante distinto, lo que genera oportunidades "fantasma".
- Desde una conexión doméstica la latencia a Binance es de 100-300 ms; los
  bots profesionales operan en microsegundos desde servidores co-ubicados.
  Una oportunidad vista acá casi nunca sigue existiendo cuando llega la orden.
- Por eso esto mide un TECHO optimista. Si incluso el techo es ~0, no hay
  nada que explotar a esta escala.

Uso:
    python -m lab.arbitraje.monitor triangular --minutos 30
    python -m lab.arbitraje.monitor inter --minutos 30 --intervalo 10
    python -m lab.arbitraje.monitor triangular --comision-pct 0.075
"""
import argparse
import csv
import logging
import os
import time
from datetime import datetime

import ccxt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SALIDA_DIR = "resultados_arbitraje"
SYMBOLS_SCANNER = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "XRP/USDT",
                   "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "NEAR/USDT"]
EXCHANGES_INTER = ["binance", "okx", "bybit", "kucoin"]


# ---------------------------------------------------------------------------
# Triangular
# ---------------------------------------------------------------------------

def _construir_ciclos(markets: dict, moneda_base: str):
    """Pares spot activos indexados por (desde, hacia) y lista de ciclos
    moneda_base -> A -> B -> moneda_base."""
    conversiones = {}  # (desde, hacia) -> (symbol, lado) ; lado 'buy' = pagar quote para recibir base
    for m in markets.values():
        if not m.get("spot") or not m.get("active"):
            continue
        base, quote, sym = m["base"], m["quote"], m["symbol"]
        conversiones[(quote, base)] = (sym, "buy")
        conversiones[(base, quote)] = (sym, "sell")

    vecinos = {}
    for (desde, hacia) in conversiones:
        vecinos.setdefault(desde, set()).add(hacia)

    ciclos = []
    for a in vecinos.get(moneda_base, ()):
        for b in vecinos.get(a, ()):
            if b in (moneda_base, a):
                continue
            if (b, moneda_base) in conversiones:
                ciclos.append((moneda_base, a, b))
    return conversiones, ciclos


def _pata(conversiones, tickers, desde, hacia, fee):
    """Devuelve (unidades de 'hacia' por 1 unidad de 'desde' neto de
    comisión, capacidad máxima expresada en unidades de 'desde') o None."""
    sym, lado = conversiones[(desde, hacia)]
    t = tickers.get(sym)
    if not t:
        return None
    if lado == "buy":
        ask, vol = t.get("ask"), t.get("askVolume")
        if not ask or not vol:
            return None
        return (1 / ask) * (1 - fee), vol * ask
    bid, vol = t.get("bid"), t.get("bidVolume")
    if not bid or not vol:
        return None
    return bid * (1 - fee), vol


def evaluar_triangular(conversiones, ciclos, tickers, fee):
    resultados = []
    for (x, a, b) in ciclos:
        p1 = _pata(conversiones, tickers, x, a, fee)
        p2 = _pata(conversiones, tickers, a, b, fee)
        p3 = _pata(conversiones, tickers, b, x, fee)
        if not (p1 and p2 and p3):
            continue
        (r1, c1), (r2, c2), (r3, c3) = p1, p2, p3
        final = r1 * r2 * r3
        # Capacidad en la moneda base: la pata más chica del primer nivel manda
        capacidad = min(c1, c2 / r1, c3 / (r1 * r2))
        bruto = final / (1 - fee) ** 3
        resultados.append((f"{x}->{a}->{b}->{x}", (bruto - 1) * 100, (final - 1) * 100, capacidad))
    return resultados


def correr_triangular(args):
    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    markets = exchange.load_markets()
    conversiones, ciclos = _construir_ciclos(markets, args.moneda_base)
    fee = args.comision_pct / 100
    logger.info(f"🔺 {len(ciclos)} ciclos triangulares desde {args.moneda_base} | comisión {args.comision_pct}%/pata")

    os.makedirs(SALIDA_DIR, exist_ok=True)
    ruta = os.path.join(SALIDA_DIR, f"triangular_{datetime.now():%Y%m%d_%H%M}.csv")
    fin = time.time() + args.minutos * 60
    snapshots, oportunidades, mejor_neto = 0, [], None

    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "ciclo", "bruto_pct", "neto_pct", "capacidad_usdt", "ganancia_usdt_techo"])
        while time.time() < fin:
            t0 = time.time()
            try:
                tickers = exchange.fetch_bids_asks()
            except Exception as e:
                logger.warning(f"⚠️ Error leyendo bookTicker: {e}")
                time.sleep(args.intervalo)
                continue
            snapshots += 1
            ts = datetime.now().isoformat(timespec="seconds")
            for ciclo, bruto, neto, cap in evaluar_triangular(conversiones, ciclos, tickers, fee):
                mejor_neto = neto if mejor_neto is None else max(mejor_neto, neto)
                if neto > args.umbral_pct and cap >= args.capacidad_minima:
                    ganancia = cap * neto / 100
                    oportunidades.append((ciclo, neto, cap, ganancia))
                    w.writerow([ts, ciclo, round(bruto, 4), round(neto, 4), round(cap, 2), round(ganancia, 4)])
            f.flush()
            if snapshots % 10 == 0:
                logger.info(f"📸 {snapshots} snapshots | {len(oportunidades)} oportunidades netas > {args.umbral_pct}% "
                            f"(capacidad ≥ {args.capacidad_minima} USDT) | mejor neto visto {mejor_neto:.4f}%")
            time.sleep(max(0, args.intervalo - (time.time() - t0)))

    _resumen("TRIANGULAR (Binance Spot)", snapshots, oportunidades, mejor_neto, ruta, args)


# ---------------------------------------------------------------------------
# Entre exchanges
# ---------------------------------------------------------------------------

def correr_inter(args):
    clientes = {}
    for nombre in args.exchanges:
        try:
            ex = getattr(ccxt, nombre)({"enableRateLimit": True, "options": {"defaultType": "spot"}})
            ex.load_markets()
            clientes[nombre] = ex
        except Exception as e:
            logger.warning(f"⚠️ {nombre}: no disponible ({e}) — se omite.")
    if len(clientes) < 2:
        logger.error("❌ Se necesitan al menos 2 exchanges accesibles.")
        return

    fee = args.comision_pct / 100
    os.makedirs(SALIDA_DIR, exist_ok=True)
    ruta = os.path.join(SALIDA_DIR, f"inter_{datetime.now():%Y%m%d_%H%M}.csv")
    fin = time.time() + args.minutos * 60
    snapshots, oportunidades, mejor_neto = 0, [], None

    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "symbol", "compra_en", "venta_en", "ask", "bid", "bruto_pct", "neto_pct",
                    "capacidad_usdt", "ganancia_usdt_techo"])
        while time.time() < fin:
            t0 = time.time()
            libros = {}
            for nombre, ex in clientes.items():
                symbols = [s for s in args.symbols if s in ex.markets]
                try:
                    libros[nombre] = ex.fetch_tickers(symbols)
                except Exception as e:
                    logger.warning(f"⚠️ {nombre}: error leyendo tickers: {e}")
            snapshots += 1
            ts = datetime.now().isoformat(timespec="seconds")
            for s in args.symbols:
                for compra_en, lc in libros.items():
                    for venta_en, lv in libros.items():
                        if compra_en == venta_en:
                            continue
                        tc, tv = lc.get(s), lv.get(s)
                        if not tc or not tv or not tc.get("ask") or not tv.get("bid"):
                            continue
                        ask, bid = tc["ask"], tv["bid"]
                        bruto = (bid / ask - 1) * 100
                        neto = (bid * (1 - fee) / (ask * (1 + fee)) - 1) * 100
                        mejor_neto = neto if mejor_neto is None else max(mejor_neto, neto)
                        vol_ask = tc.get("askVolume") or 0
                        vol_bid = tv.get("bidVolume") or 0
                        cap = min(vol_ask, vol_bid) * ask if vol_ask and vol_bid else float("nan")
                        if neto > args.umbral_pct:
                            ganancia = cap * neto / 100 if cap == cap else float("nan")
                            oportunidades.append((f"{s} {compra_en}->{venta_en}", neto, cap, ganancia))
                            w.writerow([ts, s, compra_en, venta_en, ask, bid, round(bruto, 4), round(neto, 4),
                                        round(cap, 2) if cap == cap else "", round(ganancia, 4) if ganancia == ganancia else ""])
            f.flush()
            if snapshots % 10 == 0:
                logger.info(f"📸 {snapshots} snapshots | {len(oportunidades)} oportunidades netas > {args.umbral_pct}% "
                            f"| mejor neto visto {mejor_neto:.4f}%")
            time.sleep(max(0, args.intervalo - (time.time() - t0)))

    _resumen(f"ENTRE EXCHANGES ({', '.join(clientes)})", snapshots, oportunidades, mejor_neto, ruta, args)


# ---------------------------------------------------------------------------

def _resumen(titulo, snapshots, oportunidades, mejor_neto, ruta, args):
    print("\n" + "=" * 90)
    print(f"ARBITRAJE {titulo} — {args.minutos} min, {snapshots} snapshots, comisión {args.comision_pct}%")
    print("=" * 90)
    print(f"Mejor resultado neto visto en todo el período: {mejor_neto:.4f}%" if mejor_neto is not None else "Sin datos.")
    print(f"Oportunidades netas > {args.umbral_pct}%: {len(oportunidades)}")
    if oportunidades:
        por_ciclo = {}
        for ciclo, neto, cap, ganancia in oportunidades:
            d = por_ciclo.setdefault(ciclo, [0, 0.0, 0.0])
            d[0] += 1
            d[1] = max(d[1], neto)
            d[2] += ganancia if ganancia == ganancia else 0
        top = sorted(por_ciclo.items(), key=lambda kv: -kv[1][2])[:15]
        print("\nTop por ganancia-techo acumulada (si se hubiera capturado CADA snapshot al 100%, sin latencia):")
        print(f"  {'ciclo':<40} {'veces':>6} {'mejor_neto%':>12} {'techo_usdt':>11}")
        for ciclo, (n, mejor, gan) in top:
            print(f"  {ciclo:<40} {n:>6} {mejor:>12.4f} {gan:>11.4f}")
        total = sum(g for _, _, _, g in oportunidades if g == g)
        print(f"\nTecho total teórico en el período: {total:.4f} USDT")
    print(f"\nDetalle: {ruta}")
    print("Recordá: esto es un techo optimista (snapshots no atómicos, sin latencia, solo 1er nivel del libro).")


def main():
    parser = argparse.ArgumentParser(description="Monitor de arbitraje (solo lectura)")
    parser.add_argument("modo", choices=["triangular", "inter"])
    parser.add_argument("--minutos", type=float, default=30)
    parser.add_argument("--intervalo", type=float, default=5, help="Segundos entre snapshots")
    parser.add_argument("--comision-pct", type=float, default=0.1, help="Por pata. 0.075 con descuento BNB")
    parser.add_argument("--umbral-pct", type=float, default=0.0, help="Registrar solo oportunidades netas por encima de esto")
    parser.add_argument("--capacidad-minima", type=float, default=10.0, help="USDT mínimos ejecutables (triangular)")
    parser.add_argument("--moneda-base", default="USDT")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS_SCANNER)
    parser.add_argument("--exchanges", nargs="+", default=EXCHANGES_INTER)
    args = parser.parse_args()

    if args.modo == "triangular":
        correr_triangular(args)
    else:
        correr_inter(args)


if __name__ == "__main__":
    main()
