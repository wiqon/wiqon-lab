"""
Arbitraje de funding (cash-and-carry), neutral al precio:
  comprar X en SPOT + abrir SHORT del mismo tamaño en el perpetuo USDT-M.
El precio se cancela; cuando el funding es positivo, los largos del
perpetuo le pagan al short cada 8h (o 4h). Cuando es negativo, se paga.

Modelo (sobre el historial REAL de funding de Binance):
- Capital por unidad de nocional = 1 (spot) + 1/apalancamiento_short
  (margen del short). Con 2x hacen falta 1.5 USDT por cada 1 USDT cubierto,
  así que el rendimiento sobre el capital es funding / 1.5.
- Costo de abrir o cerrar la cobertura: spot (comisión + slippage) + perp
  (comisión taker + slippage), ida y vuelta.
- Mientras no hay cobertura, el USDT rinde el APR de Earn.
- NO modela: diferencias spot/perp al entrar y salir (basis), el riesgo de
  liquidación del short si el precio sube mucho sin rebalancear margen,
  ni las transferencias spot<->futuros. Todo eso resta; es un techo.

Reglas comparadas:
  siempre      cobertura abierta todo el tiempo en un símbolo.
  umbral       abrir si el funding de los últimos 7 días anualizado > X%,
               cerrar si baja de 0%.
  rotacion     cada semana, cobertura en el símbolo con mayor funding de
               los últimos 7 días (si supera el umbral), si no, Earn.

Uso:
    python -m lab.funding_carry
    python -m lab.funding_carry --apalancamiento-short 3
"""
import argparse
import logging
import math
import os

import ccxt
import numpy as np
import pandas as pd

from lab.portafolio import SALIDA_DIR, metricas, _ahora_utc, _fmt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join("data", "funding_cache")
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "NEAR"]


def cargar_funding(exchange, base: str, desde_ms: int) -> pd.Series:
    """Historial de funding (tasa por evento) con cache incremental en CSV."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    ruta = os.path.join(CACHE_DIR, f"{base}.csv")
    serie = pd.Series(dtype=float)
    if os.path.exists(ruta):
        serie = pd.read_csv(ruta, index_col=0, parse_dates=True).iloc[:, 0]
        desde_ms = int(serie.index.max().timestamp() * 1000) + 1

    filas = []
    ahora = exchange.milliseconds()
    while desde_ms < ahora:
        lote = exchange.fetch_funding_rate_history(f"{base}/USDT:USDT", since=desde_ms, limit=1000)
        if not lote:
            break
        filas += [(r["timestamp"], r["fundingRate"]) for r in lote]
        if lote[-1]["timestamp"] + 1 <= desde_ms:
            break
        desde_ms = lote[-1]["timestamp"] + 1
        if len(lote) < 1000:
            break

    if filas:
        nuevo = pd.Series([f for _, f in filas], index=pd.to_datetime([t for t, _ in filas], unit="ms"))
        serie = pd.concat([serie, nuevo])
        serie = serie[~serie.index.duplicated(keep="last")].sort_index()
        serie.rename("funding").to_csv(ruta)
    return serie


def simular(diario: pd.DataFrame, regla: str, p, simbolo: str = None) -> tuple:
    """diario: funding sumado por día (columna por símbolo). Devuelve
    (equity, cambios de posición, % del tiempo cubierto)."""
    fechas = diario.index
    factor_capital = 1 + 1 / p.apalancamiento_short
    costo_ida_vuelta = 2 * (p.comision_spot_pct + p.comision_perp_pct + 2 * p.slippage_pct) / 100
    costo_lado = costo_ida_vuelta / 2 / factor_capital       # sobre el capital total
    tasa_earn = (1 + p.earn_apr_pct / 100) ** (1 / 365) - 1
    media_7d_anual = diario.rolling(7).mean() * 365 * 100      # funding diario promedio -> % anual

    eq, activo, cambios, dias_cubierto = p.capital, None, 0, 0
    curva = []
    for i, fecha in enumerate(fechas):
        # Rendimiento del día con la posición decidida AYER (sin mirar el futuro)
        if activo is None:
            eq *= 1 + tasa_earn
        else:
            f = diario.at[fecha, activo]
            eq *= 1 + (0.0 if np.isnan(f) else f) / factor_capital
            dias_cubierto += 1
        curva.append(eq)

        # Decisión al final del día para el día siguiente
        m = media_7d_anual.loc[fecha]
        if regla == "siempre":
            nuevo = simbolo if not np.isnan(diario.at[fecha, simbolo]) else None
        elif regla == "umbral":
            v = m[simbolo]
            if activo is None:
                nuevo = simbolo if v > p.umbral_entrada_apr else None
            else:
                nuevo = None if (np.isnan(v) or v < p.umbral_salida_apr) else simbolo
        else:  # rotacion semanal
            nuevo = activo
            if i % 7 == 0:
                candidatos = m.dropna()
                if len(candidatos) and candidatos.max() > p.umbral_entrada_apr:
                    nuevo = candidatos.idxmax()
                else:
                    nuevo = None
            elif activo is not None and (np.isnan(m[activo]) or m[activo] < p.umbral_salida_apr):
                nuevo = None

        if nuevo != activo:
            if activo is not None:
                eq *= 1 - costo_lado
            if nuevo is not None:
                eq *= 1 - costo_lado
            cambios += 1
            activo = nuevo

    return pd.Series(curva, index=fechas), cambios, dias_cubierto / len(fechas) * 100


def main():
    parser = argparse.ArgumentParser(description="Backtest de arbitraje de funding (spot largo + perp corto)")
    parser.add_argument("--days", type=int, default=2190, help="Días de historia (default 6 años)")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--apalancamiento-short", type=float, default=2.0)
    parser.add_argument("--comision-spot-pct", type=float, default=0.1)
    parser.add_argument("--comision-perp-pct", type=float, default=0.05, help="Taker USDT-M")
    parser.add_argument("--slippage-pct", type=float, default=0.05)
    parser.add_argument("--earn-apr-pct", type=float, default=3.0)
    parser.add_argument("--umbral-entrada-apr", type=float, default=10.0)
    parser.add_argument("--umbral-salida-apr", type=float, default=0.0)
    p = parser.parse_args()

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    exchange.load_markets()
    desde = _ahora_utc() - pd.Timedelta(days=p.days + 10)
    desde_ms = int(desde.timestamp() * 1000)

    series = {}
    for b in SYMBOLS:
        logger.info(f"📥 Funding {b}...")
        s = cargar_funding(exchange, b, desde_ms)
        series[b] = s[s.index >= desde].resample("1D").sum(min_count=1)
    diario = pd.DataFrame(series)
    inicio = _ahora_utc() - pd.Timedelta(days=p.days)
    diario = diario[diario.index >= inicio.normalize()]
    vacio = pd.DataFrame(columns=["pnl_pct", "pnl_usdt"])

    print("\n" + "=" * 100)
    print(f"ARBITRAJE DE FUNDING — {diario.index[0].date()} → {diario.index[-1].date()} | short {p.apalancamiento_short}x "
          f"(capital = {1 + 1 / p.apalancamiento_short:.2f}x nocional) | Earn {p.earn_apr_pct}%")
    print("=" * 100)

    # Funding bruto anualizado por año y símbolo (antes de costos, sobre nocional)
    print("\nFUNDING BRUTO ANUALIZADO (% sobre nocional, promedio por año calendario):")
    anual = (diario.groupby(diario.index.year).mean() * 365 * 100)
    print(anual.round(1).to_string())

    filas = []
    for b in SYMBOLS:
        for regla in ("siempre", "umbral"):
            eq, cambios, cubierto = simular(diario, regla, p, b)
            m = metricas(eq, vacio, p.earn_apr_pct)
            filas.append({"estrategia": f"{regla} {b}", "anualizado_%": m["anualizado_pct"],
                          "max_dd_%": m["max_dd_pct"], "cambios_año": cambios / (p.days / 365),
                          "tiempo_cubierto_%": cubierto})
    eq_rot, cambios, cubierto = simular(diario, "rotacion", p)
    m = metricas(eq_rot, vacio, p.earn_apr_pct)
    filas.append({"estrategia": "rotacion 9 símbolos", "anualizado_%": m["anualizado_pct"],
                  "max_dd_%": m["max_dd_pct"], "cambios_año": cambios / (p.days / 365), "tiempo_cubierto_%": cubierto})
    filas.append({"estrategia": "Earn (referencia)", "anualizado_%": p.earn_apr_pct, "max_dd_%": 0.0,
                  "cambios_año": 0.0, "tiempo_cubierto_%": 0.0})
    df = pd.DataFrame(filas)
    os.makedirs(SALIDA_DIR, exist_ok=True)
    df.to_csv(os.path.join(SALIDA_DIR, "funding_carry.csv"), index=False)
    print("\nRENDIMIENTO SOBRE EL CAPITAL TOTAL (neto de costos de entrada/salida):")
    print(_fmt(df.sort_values("anualizado_%", ascending=False)))

    print("\nROTACIÓN — por año (%):")
    por_anio = eq_rot.groupby(eq_rot.index.year).agg(lambda e: (e.iloc[-1] / e.iloc[0] - 1) * 100)
    print(por_anio.round(2).to_string())

    # ¿Cuánto rinde con la tendencia BTC encendida vs apagada? (para combinar con producción)
    try:
        btc = pd.read_csv(os.path.join("data", "ohlcv_cache", "BTC_USDT_1d.csv"), index_col=0, parse_dates=True)["Close"]
        en_tendencia = (btc > btc.rolling(100).mean()).reindex(diario.index).ffill()
        f_btc = diario["BTC"] * 365 * 100
        print("\nFUNDING BTC anualizado según el estado de la estrategia en producción:")
        print(f"  BTC sobre SMA100 (estrategia EN BTC):  {f_btc[en_tendencia == True].mean():.1f}%")
        print(f"  BTC bajo  SMA100 (estrategia en USDT): {f_btc[en_tendencia == False].mean():.1f}%")
    except Exception as e:
        logger.info(f"(sin comparación con tendencia: {e})")

    print("\n⚠️ Techo optimista: no incluye basis al entrar/salir, riesgo de liquidación del short ni transferencias.")


if __name__ == "__main__":
    main()
