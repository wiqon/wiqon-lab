"""
Grid bot de spot en BTC/USDT (como los de Binance, Pionex, Bitsgap o Cryptohopper), con datos reales.

Modelo (grid geométrico de spot, el más común):
- Al iniciar, el rango es [P·(1−a), P·(1+a)] con N intervalos geométricos. Cada intervalo maneja
  1/N del capital: los que están por encima del precio arrancan con BTC comprado (para tener qué vender),
  los de abajo con USDT esperando para comprar.
- Precio que BAJA y cruza el nivel inferior de un intervalo con USDT → compra en ese nivel.
  Precio que SUBE y cruza el nivel superior de un intervalo con BTC → vende en ese nivel.
- Comisión por cada orden (0,1 % = Binance spot sin descuentos). Órdenes límite: sin slippage.
- Recorrido dentro de cada vela de 5 minutos: Apertura → Mínimo → Máximo → Cierre si cerró en alza,
  Apertura → Máximo → Mínimo → Cierre si cerró en baja. No ve oscilaciones dentro de la vela: con 5 minutos
  el efecto es chico y, si algo, subestima vueltas del grid.
- Reinicio (lo que hace la gente con su bot): cada 7, 30 o 90 días, o cuando el precio sale del rango.
  Reiniciar = vender todo el BTC a mercado (con comisión) y armar un grid nuevo centrado en el precio.
- El USDT dentro del grid no rinde (está en órdenes).

Se reportan dos cosas que los paneles mezclan:
  ganancia_grid   la suma de las vueltas cerradas (compra → venta un nivel arriba): lo que muestran los paneles
  resultado real  el valor total de la cuenta, que incluye el BTC comprado que quedó valiendo menos

Benchmarks: comprar y mantener BTC; regla de tendencia de WIQON (cierre diario sobre la media de 100 días).

Uso:
    python -m lab.grid_bot                 # 64 combinaciones, 2018-01 a 2026-09
    python -m lab.grid_bot --rapido        # 8 combinaciones (para probar)
"""
import argparse
import bisect
import io
import logging
import math
import os
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from lab.portafolio import SALIDA_DIR as _SALIDA_BACKTEST  # noqa: F401  (mismo estilo de salida)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("grid")

CACHE = os.path.join("data", "klines_5m")
SALIDA = "resultados"
COMISION = 0.001
URL = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-{}.zip"


# ------------------------------------------------------------------ datos
def velas_5m(desde="2018-01", hasta="2026-09") -> pd.DataFrame:
    """Velas de 5 minutos del archivo público de Binance (un zip por mes, en caché local)."""
    os.makedirs(CACHE, exist_ok=True)
    meses = pd.period_range(desde, hasta, freq="M")
    partes = []
    for m in meses:
        f = os.path.join(CACHE, f"{m}.csv")
        if not os.path.exists(f):
            log.info("descargando %s", m)
            with urllib.request.urlopen(URL.format(m), timeout=60) as r:
                z = zipfile.ZipFile(io.BytesIO(r.read()))
                with z.open(z.namelist()[0]) as fh:
                    open(f, "wb").write(fh.read())
        d = pd.read_csv(f, header=None, usecols=[0, 1, 2, 3, 4], names=["t", "o", "h", "l", "c"])
        if d["t"].iloc[0] > 1e14:  # desde 2025 el archivo trae microsegundos
            d["t"] = d["t"] // 1000
        partes.append(d)
    df = pd.concat(partes, ignore_index=True).drop_duplicates("t").sort_values("t")
    df.index = pd.to_datetime(df["t"], unit="ms")
    return df[["o", "h", "l", "c"]].astype(float)


# ------------------------------------------------------------------ simulación
def simular_grid(df: pd.DataFrame, amplitud: float, n: int, reinicio: str, capital: float = 10_000.0):
    """Devuelve (equity diaria, métricas del grid). reinicio: '7d', '30d', '90d' o 'rango'."""
    o, h, l, c = (df[k].to_numpy() for k in ("o", "h", "l", "c"))
    t = df.index.to_numpy()
    dias_reinicio = {"7d": 7, "30d": 30, "90d": 90}.get(reinicio)
    usdt, comisiones, ganancia_grid, vueltas, reinicios = capital, 0.0, 0.0, 0, 0
    eq_t, eq_v = [], []
    niveles = None

    def armar(precio, efectivo):
        """Grid nuevo centrado en 'precio' con todo el efectivo. Devuelve el estado de los intervalos."""
        nonlocal comisiones
        lo, hi = precio * (1 - amplitud), precio * (1 + amplitud)
        lv = [lo * (hi / lo) ** (i / n) for i in range(n + 1)]
        por = efectivo / n
        btc = [0.0] * n          # BTC que tiene cada intervalo (si tiene)
        costo = [0.0] * n        # USDT que costó ese BTC
        caja = [0.0] * n         # USDT esperando comprar
        for j in range(n):
            if lv[j + 1] > precio:                       # intervalo por encima: arranca con BTC comprado a mercado
                q = por / precio * (1 - COMISION)
                comisiones += por * COMISION
                btc[j], costo[j] = q, por
            else:                                        # por debajo: USDT con orden de compra en lv[j]
                caja[j] = por
        return lv, btc, costo, caja

    def cerrar(precio, btc, caja):
        """Vende todo el BTC a mercado y devuelve el efectivo total."""
        nonlocal comisiones
        total_btc = sum(btc)
        venta = total_btc * precio
        comisiones += venta * COMISION
        return sum(caja) + venta * (1 - COMISION)

    def mover(desde, hasta, lv, btc, costo, caja):
        """Ejecuta las órdenes cruzadas al ir de 'desde' a 'hasta'."""
        nonlocal comisiones, ganancia_grid, vueltas
        if hasta < desde:   # baja: compras en niveles inferiores cruzados (lv[j] en [hasta, desde))
            j0 = bisect.bisect_left(lv, hasta)
            j1 = bisect.bisect_left(lv, desde)
            for j in range(min(j1, n) - 1, max(j0, 0) - 1, -1):
                if caja[j] > 0:
                    q = caja[j] / lv[j] * (1 - COMISION)
                    comisiones += caja[j] * COMISION
                    btc[j], costo[j], caja[j] = q, caja[j], 0.0
        elif hasta > desde:  # sube: ventas en niveles superiores cruzados (lv[j+1] en (desde, hasta])
            k0 = bisect.bisect_right(lv, desde)
            k1 = bisect.bisect_right(lv, hasta)
            for k in range(max(k0, 1), min(k1, n + 1)):
                j = k - 1
                if btc[j] > 0:
                    bruto = btc[j] * lv[k]
                    neto = bruto * (1 - COMISION)
                    comisiones += bruto * COMISION
                    ganancia_grid += neto - costo[j]
                    vueltas += 1
                    caja[j], btc[j], costo[j] = neto, 0.0, 0.0

    inicio_grid = t[0]
    lv, btc, costo, caja = armar(o[0], usdt)
    usdt = 0.0
    dia_actual = None
    for i in range(len(c)):
        # reinicio por tiempo o por salir del rango
        if dias_reinicio and (t[i] - inicio_grid) >= np.timedelta64(dias_reinicio, "D"):
            efectivo = cerrar(o[i], btc, caja)
            lv, btc, costo, caja = armar(o[i], efectivo); inicio_grid = t[i]; reinicios += 1
        elif reinicio == "rango" and (o[i] > lv[-1] or o[i] < lv[0]):
            efectivo = cerrar(o[i], btc, caja)
            lv, btc, costo, caja = armar(o[i], efectivo); inicio_grid = t[i]; reinicios += 1
        if c[i] >= o[i]:
            camino = (o[i], l[i], h[i], c[i])
        else:
            camino = (o[i], h[i], l[i], c[i])
        for a, b in zip(camino, camino[1:]):
            mover(a, b, lv, btc, costo, caja)
        dia = t[i].astype("datetime64[D]")
        if dia != dia_actual:
            if dia_actual is not None:
                eq_t.append(dia_actual); eq_v.append(valor)
            dia_actual = dia
        valor = sum(caja) + sum(btc) * c[i]
    eq_t.append(dia_actual); eq_v.append(valor)
    equity = pd.Series(eq_v, index=pd.to_datetime(eq_t))
    return equity, {"ganancia_grid_usdt": ganancia_grid, "vueltas": vueltas, "comisiones_usdt": comisiones, "reinicios": reinicios}


# ------------------------------------------------------------------ métricas y benchmarks
def metricas(equity: pd.Series) -> dict:
    if len(equity) < 2:
        return {"retorno_pct": np.nan, "anualizado_pct": np.nan, "max_dd_pct": np.nan, "sharpe": np.nan}
    ret = equity.iloc[-1] / equity.iloc[0] - 1
    dias = (equity.index[-1] - equity.index[0]).days or 1
    diario = equity.pct_change().dropna()
    return {
        "retorno_pct": ret * 100,
        "anualizado_pct": ((1 + ret) ** (365 / dias) - 1) * 100 if ret > -1 else -100.0,
        "max_dd_pct": (equity / equity.cummax() - 1).min() * 100,
        "sharpe": diario.mean() / diario.std() * math.sqrt(365) if diario.std() > 0 else np.nan,
    }


def comprar_y_mantener(df: pd.DataFrame, capital=10_000.0) -> pd.Series:
    d = df["c"].resample("1D").last().dropna()
    return d / df["o"].iloc[0] * capital * (1 - COMISION)


def regla_tendencia(df: pd.DataFrame, capital=10_000.0) -> pd.Series:
    """Cierre diario sobre la media de 100 días → BTC; si no, USDT. Señal al cierre, ejecución a la apertura siguiente."""
    d = df["c"].resample("1D").last().dropna()
    sma = d.rolling(100).mean()
    dentro = (d > sma).shift(1).fillna(False)
    ret = d.pct_change().fillna(0)
    cambios = dentro.astype(int).diff().abs().fillna(0)
    eq = capital * (1 + ret.where(dentro, 0) - cambios * COMISION).cumprod()
    return eq


def anual(equity: pd.Series) -> pd.Series:
    fin = equity.resample("YE").last()
    ini = pd.concat([pd.Series([equity.iloc[0]], index=[equity.index[0]]), fin.iloc[:-1]])
    return pd.Series((fin.to_numpy() / ini.to_numpy() - 1) * 100, index=fin.index.year)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2018-01"); ap.add_argument("--hasta", default="2026-09")
    ap.add_argument("--rapido", action="store_true")
    a = ap.parse_args()
    df = velas_5m(a.desde, a.hasta)
    log.info("velas: %d (%s → %s)", len(df), df.index[0], df.index[-1])
    amplitudes = [0.10, 0.30] if a.rapido else [0.05, 0.10, 0.20, 0.30]
    niveles = [20] if a.rapido else [10, 20, 50, 100]
    reinicios = ["30d", "rango"] if a.rapido else ["7d", "30d", "90d", "rango"]
    hold, trend = comprar_y_mantener(df), regla_tendencia(df)
    filas, anuales = [], {"Comprar y mantener": anual(hold), "Regla de tendencia (media 100 d)": anual(trend)}
    corte = pd.Timestamp("2023-01-01")
    for amp in amplitudes:
        for n in niveles:
            for r in reinicios:
                eq, g = simular_grid(df, amp, n, r)
                m = metricas(eq)
                dentro, fuera = metricas(eq[eq.index < corte]), metricas(eq[eq.index >= corte])
                filas.append({"amplitud_pct": amp * 100, "niveles": n, "reinicio": r, **{k: round(v, 2) for k, v in m.items()},
                              "anualizado_2018_2022_pct": round(dentro["anualizado_pct"], 2), "anualizado_2023_2026_pct": round(fuera["anualizado_pct"], 2),
                              "ganancia_grid_pct": round(g["ganancia_grid_usdt"] / 100, 2), "vueltas": g["vueltas"],
                              "comisiones_pct": round(g["comisiones_usdt"] / 100, 2), "reinicios": g["reinicios"]})
                log.info("±%d%% · %d niveles · reinicio %s → %.1f%% anual · DD %.1f%% · ganancia del grid %.0f%% vs real %.0f%%",
                         amp * 100, n, r, m["anualizado_pct"], m["max_dd_pct"], filas[-1]["ganancia_grid_pct"], m["retorno_pct"])
                if amp == 0.10 and n == 20 and r == "30d":
                    anuales["Grid típico (±10 %, 20 niveles, reinicio mensual)"] = anual(eq)
    tabla = pd.DataFrame(filas)
    os.makedirs(SALIDA, exist_ok=True)
    tabla.to_csv(os.path.join(SALIDA, "grid_bot.csv"), index=False)
    pd.DataFrame(anuales).round(1).to_csv(os.path.join(SALIDA, "grid_bot_anual.csv"))
    bh, tr = metricas(hold), metricas(trend)
    resumen = {
        "combinaciones": len(tabla),
        "mediana_anualizado_pct": round(tabla["anualizado_pct"].median(), 1),
        "mejor_anualizado_pct": round(tabla["anualizado_pct"].max(), 1),
        "peor_anualizado_pct": round(tabla["anualizado_pct"].min(), 1),
        "mediana_max_dd_pct": round(tabla["max_dd_pct"].median(), 1),
        "mediana_ganancia_grid_pct": round(tabla["ganancia_grid_pct"].median(), 1),
        "mediana_retorno_real_pct": round(tabla["retorno_pct"].median(), 1),
        "combinaciones_que_superan_hold": int((tabla["anualizado_pct"] > bh["anualizado_pct"]).sum()),
        "hold_anualizado_pct": round(bh["anualizado_pct"], 1), "hold_max_dd_pct": round(bh["max_dd_pct"], 1),
        "tendencia_anualizado_pct": round(tr["anualizado_pct"], 1), "tendencia_max_dd_pct": round(tr["max_dd_pct"], 1),
        "mediana_2018_2022_pct": round(tabla["anualizado_2018_2022_pct"].median(), 1),
        "mediana_2023_2026_pct": round(tabla["anualizado_2023_2026_pct"].median(), 1),
    }
    pd.Series(resumen).to_csv(os.path.join(SALIDA, "grid_bot_resumen.csv"), header=["valor"])
    print(pd.Series(resumen).to_string())
    print(pd.DataFrame(anuales).round(1).to_string())


if __name__ == "__main__":
    main()
