"""
Mejoras candidatas a la estrategia en producción (filtro de tendencia en BTC, ver README):

  A) Más activos: cada uno con su PROPIO filtro de tendencia (cierre > SMA)
     y una porción fija del capital (p. ej. 50% BTC / 50% ETH). Si uno está
     fuera, su porción queda en USDT (Earn) — no se pasa al otro.
  B) Tamaño por volatilidad: la porción de cada activo se escala por
     min(1, vol_objetivo / vol_realizada_30d). Cuando el mercado está muy
     agitado se invierte menos; nunca se apalanca (máx 100% de la porción).

Todas las combinaciones se comparan contra la config en producción (solo
BTC, SMA100, sin vol) en el mismo período. Para aceptar una mejora, tiene
que ganar de forma CONSISTENTE en las tres SMA (50/100/150), no solo en una.

Para no operar todos los días por cambios chicos de volatilidad, solo se
rebalancea si algún peso se desvía más de `--banda` del objetivo, o si un
activo entra/sale de tendencia.

Uso:
    python -m lab.tendencia_multi
    python -m lab.tendencia_multi --days 3000 --banda 0.10
"""
import argparse
import itertools
import logging
import math
import os

import ccxt
import numpy as np
import pandas as pd

from lab.portafolio import SALIDA_DIR, cargar_ohlcv_cacheado, metricas, _ahora_utc, _fmt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DIAS_CALENTAMIENTO = 200

UNIVERSOS = {
    "BTC": {"BTC/USDT": 1.0},
    "BTC+ETH": {"BTC/USDT": 0.5, "ETH/USDT": 0.5},
    "BTC70+ETH30": {"BTC/USDT": 0.7, "ETH/USDT": 0.3},
}


def simular(opens, closes, porciones: dict, sma: int, vol_objetivo, inicio, p):
    activos = list(porciones)
    o_df, c_df = opens[activos], closes[activos]
    sma_df = c_df.rolling(sma).mean()
    vol_df = np.log(c_df).diff().rolling(p.vol_ventana).std() * math.sqrt(365) * 100
    fechas = c_df.index[c_df.index >= inicio]

    fee, slip = p.comision_pct / 100, p.slippage_pct / 100
    tasa_diaria = (1 + p.earn_apr_pct / 100) ** (1 / 365) - 1
    cash = p.capital
    qty = pd.Series(0.0, index=activos)
    objetivo = None
    equity, n_ordenes, exposicion = [], 0, []

    for fecha in fechas:
        o, c = o_df.loc[fecha], c_df.loc[fecha]

        # 1) Rebalanceo pendiente, a la apertura (ventas primero para liberar USDT)
        if objetivo is not None:
            valor = cash + float((qty * o).sum())
            difs = {s: valor * objetivo[s] - qty[s] * o[s] for s in activos}
            for s in sorted(activos, key=lambda s: difs[s]):
                diff = difs[s]
                if abs(diff) < p.minimo_orden:
                    continue
                if diff < 0:
                    vender = min(-diff / o[s], qty[s])
                    qty[s] -= vender
                    cash += vender * o[s] * (1 - slip) * (1 - fee)
                else:
                    monto = min(diff, cash / (1 + fee))
                    if monto < p.minimo_orden:
                        continue
                    qty[s] += monto / (o[s] * (1 + slip))
                    cash -= monto * (1 + fee)
                n_ordenes += 1
            objetivo = None

        # 2) Earn sobre el USDT ocioso, valuación al cierre
        cash *= 1 + tasa_diaria
        valor_pos = qty * c
        eq = cash + float(valor_pos.sum())
        equity.append(eq)
        exposicion.append(float(valor_pos.sum()) / eq if eq > 0 else 0)

        # 3) Pesos objetivo según la vela diaria cerrada
        deseado = {}
        for s in activos:
            if c[s] > sma_df.loc[fecha, s]:
                w = porciones[s]
                if vol_objetivo:
                    v = vol_df.loc[fecha, s]
                    w *= min(1.0, vol_objetivo / v) if v and v > 0 else 1.0
                deseado[s] = w
            else:
                deseado[s] = 0.0
        actual = {s: valor_pos[s] / eq for s in activos}
        cambio_estado = any((deseado[s] > 0) != (actual[s] * eq >= p.minimo_orden) for s in activos)
        desvio = max(abs(deseado[s] - actual[s]) for s in activos)
        if cambio_estado or desvio > p.banda:
            objetivo = deseado

    return pd.Series(equity, index=fechas), n_ordenes, float(np.mean(exposicion))


def main():
    parser = argparse.ArgumentParser(description="Tendencia multi-activo + tamaño por volatilidad")
    parser.add_argument("--days", type=int, default=3000)
    parser.add_argument("--ventana-dias", type=int, default=365)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--comision-pct", type=float, default=0.1)
    parser.add_argument("--slippage-pct", type=float, default=0.05)
    parser.add_argument("--earn-apr-pct", type=float, default=3.0)
    parser.add_argument("--minimo-orden", type=float, default=5.0)
    parser.add_argument("--vol-ventana", type=int, default=30)
    parser.add_argument("--banda", type=float, default=0.10, help="Desvío de peso que dispara un rebalanceo")
    p = parser.parse_args()

    exchange = ccxt.binance({"options": {"defaultType": "spot"}})
    simbolos = sorted({s for u in UNIVERSOS.values() for s in u})
    opens, closes = {}, {}
    for s in simbolos:
        df = cargar_ohlcv_cacheado(exchange, s, "1d", p.days + DIAS_CALENTAMIENTO)
        opens[s], closes[s] = df["Open"], df["Close"]
    opens, closes = pd.DataFrame(opens).dropna(), pd.DataFrame(closes).dropna()
    inicio = _ahora_utc() - pd.Timedelta(days=p.days)
    vacio = pd.DataFrame(columns=["pnl_pct", "pnl_usdt"])

    filas, curvas = [], {}
    for universo, sma, vol in itertools.product(UNIVERSOS, [50, 100, 150], [None, 40, 60, 80]):
        eq, n_ord, expo = simular(opens, closes, UNIVERSOS[universo], sma, vol, inicio, p)
        m = metricas(eq, vacio, p.earn_apr_pct)
        nombre = f"{universo} SMA{sma} vol{vol or '-'}"
        curvas[nombre] = eq
        filas.append({"universo": universo, "sma": sma, "vol_obj%": vol or "-",
                      "anualizado_%": m["anualizado_pct"], "max_dd_%": m["max_dd_pct"], "sharpe": m["sharpe"],
                      "calmar": m["anualizado_pct"] / abs(m["max_dd_pct"]), "ordenes_año": n_ord / (p.days / 365),
                      "exposicion_%": expo * 100})
    df = pd.DataFrame(filas)
    os.makedirs(SALIDA_DIR, exist_ok=True)
    df.to_csv(os.path.join(SALIDA_DIR, "tendencia_multi.csv"), index=False)

    fechas = curvas["BTC SMA100 vol-"].index
    btc = closes.loc[fechas, "BTC/USDT"]
    m_hold = metricas(btc / btc.iloc[0] * p.capital, vacio, p.earn_apr_pct)

    print("\n" + "=" * 110)
    print(f"TENDENCIA MULTI-ACTIVO — {fechas[0].date()} → {fechas[-1].date()}, comisión {p.comision_pct}%, "
          f"slippage {p.slippage_pct}%, banda {p.banda:.0%}, Earn {p.earn_apr_pct}%")
    print(f"Referencia BTC comprar y mantener: anualizado {m_hold['anualizado_pct']:.1f}%, "
          f"DD {m_hold['max_dd_pct']:.1f}%, Sharpe {m_hold['sharpe']:.2f}")
    print("Producción actual = 'BTC SMA100 vol -'")
    print("=" * 110)
    print(_fmt(df))

    # Consistencia: ¿cada variante le gana a 'BTC sin vol' con la MISMA SMA?
    print("\nCONSISTENCIA vs BTC sin vol (misma SMA) — cuántas de las 3 SMA mejoran:")
    base = df[(df.universo == "BTC") & (df["vol_obj%"] == "-")].set_index("sma")
    resumen = []
    for (u, v), g in df.groupby(["universo", "vol_obj%"], sort=False):
        if u == "BTC" and v == "-":
            continue
        g = g.set_index("sma")
        resumen.append({
            "variante": f"{u} vol{v}",
            "mejor_sharpe": f"{(g.sharpe > base.sharpe).sum()}/3",
            "menor_dd": f"{(g['max_dd_%'] > base['max_dd_%']).sum()}/3",
            "mejor_calmar": f"{(g.calmar > base.calmar).sum()}/3",
            "Δanualizado_medio": (g["anualizado_%"] - base["anualizado_%"]).mean(),
            "Δdd_medio": (g["max_dd_%"] - base["max_dd_%"]).mean(),
        })
    print(_fmt(pd.DataFrame(resumen)))

    # Por año: producción vs las mejores por calmar
    top = df.sort_values("calmar", ascending=False).head(3)
    elegidas = ["BTC SMA100 vol-"] + [f"{r.universo} SMA{r.sma} vol{r['vol_obj%']}" for _, r in top.iterrows()]
    elegidas = list(dict.fromkeys(elegidas))
    print("\nPOR AÑO (retorno % / DD %): producción vs top 3 por Calmar")
    filas_a = []
    t0 = fechas[0]
    while t0 < fechas[-1]:
        t1 = t0 + pd.Timedelta(days=p.ventana_dias)
        fila = {"desde": t0.date()}
        for n in elegidas:
            e = curvas[n][(curvas[n].index >= t0) & (curvas[n].index < t1)]
            if len(e) > 30:
                fila[n] = f"{(e.iloc[-1] / e.iloc[0] - 1) * 100:+.0f} / {(e / e.cummax() - 1).min() * 100:.0f}"
        b = btc[(btc.index >= t0) & (btc.index < t1)]
        fila["BTC hold"] = f"{(b.iloc[-1] / b.iloc[0] - 1) * 100:+.0f} / {(b / b.cummax() - 1).min() * 100:.0f}"
        filas_a.append(fila)
        t0 = t1
    print(pd.DataFrame(filas_a).to_string(index=False))


if __name__ == "__main__":
    main()
