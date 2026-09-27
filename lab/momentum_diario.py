"""
Familia NUEVA (no está en la tabla de ideas rechazadas del README, que son
todas combinaciones de indicadores en velas de 1h): MOMENTUM DIARIO con
rotación semanal entre los símbolos del scanner.

Regla (pocos parámetros a propósito, para no sobreajustar):
- Cada `rebalanceo_dias` días, al cierre diario:
  - Elegibles: cierre > SMA(sma) Y retorno de los últimos `lookback` días > 0.
  - Opcional (filtro_btc): si BTC está bajo su SMA(sma), todo a USDT.
  - Se compran los `top` elegibles con mayor retorno en `lookback`, 1/top
    del capital cada uno. Los lugares sin elegible quedan en USDT (Earn).
- Ejecución a la apertura del día siguiente, con comisión + slippage sobre
  lo efectivamente operado. El USDT ocioso rinde el APR de Earn supuesto.

Anti-sobreajuste: además de la config base (fijada ANTES de ver resultados:
SMA 100, lookback 60, top 3, sin filtro BTC), se corre una grilla de
vecinos y se reporta la MEDIANA y cuántas combinaciones son positivas — si
solo funciona una combinación puntual, es ruido.

⚠️ Sesgo de supervivencia: los 9 símbolos se eligieron hoy, sabiendo que
siguen vivos y relevantes. En 2021 alguien habría elegido otros (LUNA, FTT...).
Esto infla cualquier estrategia long-only, incluida esta.

Uso:
    python -m lab.momentum_diario
    python -m lab.momentum_diario --days 1825 --earn-apr-pct 3
"""
import argparse
import itertools
import logging
import os

import ccxt
import numpy as np
import pandas as pd

from lab.portafolio import (
    SYMBOLS_SCANNER, SALIDA_DIR, cargar_ohlcv_cacheado, metricas, _ahora_utc, _fmt,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DIAS_CALENTAMIENTO = 260  # SMA150 + lookback 90 + margen

# Top 30 por capitalización (sin stablecoins) a septiembre de 2021 — el
# universo que alguien habría elegido ANTES del período simulado, incluidas
# las que después colapsaron o fueron deslistadas (LUNA, FTT, MATIC, EOS,
# XMR...). Reduce el sesgo de supervivencia de usar las 9 del scanner.
UNIVERSO_TOP30_2021 = [
    "BTC/USDT", "ETH/USDT", "ADA/USDT", "BNB/USDT", "XRP/USDT", "SOL/USDT", "DOT/USDT",
    "DOGE/USDT", "LUNA/USDT", "UNI/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "BCH/USDT",
    "ALGO/USDT", "MATIC/USDT", "FIL/USDT", "ATOM/USDT", "ICP/USDT", "TRX/USDT", "XLM/USDT",
    "VET/USDT", "ETC/USDT", "THETA/USDT", "FTT/USDT", "AXS/USDT", "EOS/USDT", "XTZ/USDT",
    "AAVE/USDT", "XMR/USDT",
]
UNIVERSOS = {"scanner": SYMBOLS_SCANNER, "top30_2021": UNIVERSO_TOP30_2021}


def truncar_en_hueco(df: pd.DataFrame, max_dias: int = 3) -> pd.DataFrame:
    """Corta la serie en el primer hueco de más de `max_dias` días. Binance
    reutiliza símbolos (LUNA/USDT: la LUNA original se desploma el
    2022-05-13 y el 2022-05-31 arranca LUNA 2.0 en el mismo par) — sin este
    corte el backtest vería un +17.000.000% falso. Tras el corte, el símbolo
    se trata como deslistado (venta forzada al último cierre)."""
    saltos = df.index.to_series().diff() > pd.Timedelta(days=max_dias)
    if saltos.any():
        return df.loc[:saltos.idxmax() - pd.Timedelta(seconds=1)]
    return df


def simular_momentum(opens: pd.DataFrame, closes: pd.DataFrame, inicio, p, sma, lookback, top, filtro_btc):
    sma_df = closes.rolling(sma).mean()
    ret_df = closes / closes.shift(lookback) - 1
    fechas = closes.index[closes.index >= inicio]
    fee, slip = p.comision_pct / 100, p.slippage_pct / 100
    tasa_diaria = (1 + p.earn_apr_pct / 100) ** (1 / 365) - 1

    ultimo_valido = closes.apply(lambda col: col.last_valid_index())
    ultimo_cierre = closes.ffill()

    cash = p.capital
    qty = pd.Series(0.0, index=closes.columns)
    equity = []
    objetivo = None  # pesos a ejecutar en la apertura siguiente
    operado_total = 0.0

    for n, fecha in enumerate(fechas):
        o = opens.loc[fecha]
        c = closes.loc[fecha]

        # 0) Deslistados: si la serie terminó y queda saldo, venta forzada
        #    al último cierre conocido (supuesto optimista: en la realidad
        #    puede que no haya liquidez para salir a ese precio).
        for s in closes.columns:
            if qty[s] > 0 and fecha > ultimo_valido[s]:
                precio = ultimo_cierre.loc[fecha, s] * (1 - slip)
                cash += qty[s] * precio * (1 - fee)
                operado_total += qty[s] * precio
                qty[s] = 0.0

        # 1) Ejecutar rebalanceo pendiente a la apertura
        if objetivo is not None:
            valor_apertura = cash + float((qty * o.fillna(0)).sum())
            for s in closes.columns:
                if np.isnan(o[s]):
                    continue
                deseado = valor_apertura * objetivo.get(s, 0.0)
                actual = qty[s] * o[s]
                diff = deseado - actual
                if abs(diff) < max(p.minimo_orden, valor_apertura * 0.01):
                    continue
                if diff > 0:
                    precio = o[s] * (1 + slip)
                    monto = min(diff, cash / (1 + fee))
                    qty[s] += monto / precio
                    cash -= monto * (1 + fee)
                    operado_total += monto
                else:
                    precio = o[s] * (1 - slip)
                    vender = min(-diff / o[s], qty[s])
                    qty[s] -= vender
                    cash += vender * precio * (1 - fee)
                    operado_total += vender * precio
            objetivo = None

        # 2) Interés de Earn sobre el USDT ocioso y valuación al cierre
        cash *= 1 + tasa_diaria
        eq = cash + float((qty * c.fillna(0)).sum())
        equity.append(eq)

        # 3) Señal al cierre cada N días
        if n % p.rebalanceo_dias == 0:
            elegibles = (c > sma_df.loc[fecha]) & (ret_df.loc[fecha] > 0)
            if filtro_btc and not (c.get("BTC/USDT", np.nan) > sma_df.loc[fecha].get("BTC/USDT", np.nan)):
                elegibles[:] = False
            ranking = ret_df.loc[fecha][elegibles].sort_values(ascending=False)
            elegidos = list(ranking.index[:top])
            objetivo = {s: 1 / top for s in elegidos}

    serie = pd.Series(equity, index=fechas)
    return serie, operado_total


def main():
    parser = argparse.ArgumentParser(description="Momentum diario con rotación — backtest + robustez")
    parser.add_argument("--universo", choices=list(UNIVERSOS), default="scanner")
    parser.add_argument("--symbols", nargs="+", help="Lista explícita; tiene prioridad sobre --universo")
    parser.add_argument("--grilla-top", nargs="+", type=int, default=[2, 3])
    parser.add_argument("--days", type=int, default=1825)
    parser.add_argument("--ventana-dias", type=int, default=365)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--comision-pct", type=float, default=0.1)
    parser.add_argument("--slippage-pct", type=float, default=0.05)
    parser.add_argument("--earn-apr-pct", type=float, default=3.0)
    parser.add_argument("--rebalanceo-dias", type=int, default=7)
    parser.add_argument("--minimo-orden", type=float, default=5.0)
    # Config base, fijada antes de mirar resultados
    parser.add_argument("--sma", type=int, default=100)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--filtro-btc", action="store_true")
    p = parser.parse_args()

    exchange = ccxt.binance({"options": {"defaultType": "spot"}})
    symbols = p.symbols or UNIVERSOS[p.universo]
    opens, closes = {}, {}
    for s in symbols:
        logger.info(f"📥 {s}: velas diarias...")
        try:
            df = cargar_ohlcv_cacheado(exchange, s, "1d", p.days + DIAS_CALENTAMIENTO)
        except Exception as e:
            logger.warning(f"⚠️ {s}: sin datos ({e}) — se omite.")
            continue
        if df.empty:
            logger.warning(f"⚠️ {s}: sin velas — se omite.")
            continue
        df = truncar_en_hueco(df)
        opens[s], closes[s] = df["Open"], df["Close"]
    opens, closes = pd.DataFrame(opens), pd.DataFrame(closes)
    inicio = _ahora_utc() - pd.Timedelta(days=p.days)
    vacio = pd.DataFrame(columns=["pnl_pct", "pnl_usdt"])

    # --- Benchmarks ---
    fechas = closes.index[closes.index >= inicio]
    btc = closes.loc[fechas, "BTC/USDT"]
    eq_btc = btc / btc.iloc[0] * p.capital
    base_canasta = closes.loc[fechas].bfill().iloc[0]
    eq_canasta = (closes.loc[fechas].ffill() / base_canasta).mean(axis=1) * p.capital

    # --- Config base, por ventana ---
    eq_base, operado = simular_momentum(opens, closes, inicio, p, p.sma, p.lookback, p.top, p.filtro_btc)
    filas = []
    t0 = fechas[0]
    while t0 < fechas[-1]:
        t1 = t0 + pd.Timedelta(days=p.ventana_dias)
        idx = eq_base.index[(eq_base.index >= t0) & (eq_base.index < t1)]
        if len(idx) >= 60:
            fila = {"desde": idx[0].date(), "hasta": idx[-1].date()}
            for nombre, eq in [("estrategia", eq_base), ("btc_hold", eq_btc), ("canasta_hold", eq_canasta)]:
                m = metricas(eq.loc[idx], vacio, p.earn_apr_pct)
                fila[f"{nombre}_ret%"] = m["retorno_pct"]
                fila[f"{nombre}_dd%"] = m["max_dd_pct"]
            fila["earn_%"] = m["earn_pct"]
            filas.append(fila)
        t0 = t1

    print("\n" + "=" * 110)
    print(f"MOMENTUM DIARIO — config base: SMA {p.sma}, lookback {p.lookback}d, top {p.top}, "
          f"filtro BTC {'sí' if p.filtro_btc else 'no'}, rebalanceo cada {p.rebalanceo_dias}d")
    print(f"comisión {p.comision_pct}%/lado, slippage {p.slippage_pct}%, USDT ocioso al {p.earn_apr_pct}% APR")
    print("=" * 110)
    print("\nPOR VENTANA:")
    print(_fmt(pd.DataFrame(filas)))

    print("\nTOTAL (anualizado, drawdown máximo, Sharpe):")
    tot = []
    for nombre, eq in [("estrategia", eq_base), ("btc_hold", eq_btc), ("canasta_hold", eq_canasta)]:
        m = metricas(eq, vacio, p.earn_apr_pct)
        tot.append({"serie": nombre, "retorno_%": m["retorno_pct"], "anualizado_%": m["anualizado_pct"],
                    "max_dd_%": m["max_dd_pct"], "sharpe": m["sharpe"]})
    print(_fmt(pd.DataFrame(tot)))
    print(f"  Volumen operado: {operado:,.0f} USDT (rotación ≈ {operado / p.capital / (p.days / 365):.1f}x capital/año)")

    # --- Robustez: grilla de vecinos ---
    grilla = []
    for sma, lb, top, fb in itertools.product([50, 100, 150], [30, 60, 90], p.grilla_top, [False, True]):
        eq, _ = simular_momentum(opens, closes, inicio, p, sma, lb, top, fb)
        m = metricas(eq, vacio, p.earn_apr_pct)
        grilla.append({"sma": sma, "lookback": lb, "top": top, "filtro_btc": fb,
                       "anualizado_%": m["anualizado_pct"], "max_dd_%": m["max_dd_pct"], "sharpe": m["sharpe"]})
    df_grilla = pd.DataFrame(grilla)
    os.makedirs(SALIDA_DIR, exist_ok=True)
    sufijo = p.universo if not p.symbols else "custom"
    df_grilla.to_csv(os.path.join(SALIDA_DIR, f"momentum_grilla_{sufijo}.csv"), index=False)
    pd.DataFrame(filas).to_csv(os.path.join(SALIDA_DIR, f"momentum_ventanas_{sufijo}.csv"), index=False)

    print(f"\nROBUSTEZ ({len(df_grilla)} combinaciones vecinas):")
    print(f"  Anualizado  — mediana {df_grilla['anualizado_%'].median():.1f}% | "
          f"peor {df_grilla['anualizado_%'].min():.1f}% | mejor {df_grilla['anualizado_%'].max():.1f}%")
    print(f"  Drawdown    — mediana {df_grilla['max_dd_%'].median():.1f}% | peor {df_grilla['max_dd_%'].min():.1f}%")
    print(f"  Sharpe      — mediana {df_grilla['sharpe'].median():.2f}")
    print(f"  Combinaciones con anualizado > Earn ({p.earn_apr_pct}%): "
          f"{(df_grilla['anualizado_%'] > p.earn_apr_pct).sum()}/{len(df_grilla)}")
    print("\n" + _fmt(df_grilla.sort_values("sharpe", ascending=False)))


if __name__ == "__main__":
    main()
