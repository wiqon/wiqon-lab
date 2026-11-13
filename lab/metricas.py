"""
Métricas estándar del laboratorio: UNA sola definición para todos los experimentos.

Todas se calculan sobre la curva de capital DIARIA (último valor de cada día, UTC). Las estrategias que
simulan con velas de 5 minutos se pasan a diario antes de medir, así todas se comparan con la misma vara.
Cripto opera 365 días al año: se anualiza con 365 (no 252).

Fórmulas (equity E_t diaria, retornos r_t = E_t / E_{t-1} - 1, T = días calendario entre la primera y la última fecha):

    retorno_total   = E_fin / E_ini - 1
    cagr            = (E_fin / E_ini) ^ (365 / T) - 1                     (anualizado compuesto)
    volatilidad     = desvío(r_t) · √365                                   (anualizada)
    max_drawdown    = min_t ( E_t / max_{s≤t} E_s - 1 )                    (la peor caída desde un máximo previo)
    dias_en_dd_max  = mayor cantidad de días seguidos por debajo de un máximo previo
    sharpe          = media(r_t) / desvío(r_t) · √365                      (tasa libre de riesgo = 0)
    sortino         = media(r_t) / desvío_bajista · √365,  desvío_bajista = √ media( min(r_t, 0)² )
    calmar          = cagr / |max_drawdown|
    anios           = retorno de cada año calendario (el primero y el último pueden ser parciales)

Sharpe y Sortino solo se informan con al menos MIN_DIAS_RATIOS días: con menos, la muestra no alcanza.
No se agrega ninguna métrica que no tenga definición acá.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

DIAS_ANIO = 365
MIN_DIAS_RATIOS = 180


def a_diaria(equity: pd.Series) -> pd.Series:
    """Curva de capital diaria: último valor de cada día. Acepta series intradía o ya diarias."""
    eq = equity.dropna().astype(float)
    if not isinstance(eq.index, pd.DatetimeIndex):
        raise TypeError("la curva de capital necesita un índice de fechas")
    return eq.resample("1D").last().dropna()


def drawdown(eq: pd.Series) -> pd.Series:
    return eq / eq.cummax() - 1


def dias_en_drawdown_max(eq: pd.Series) -> int:
    """Mayor racha de días seguidos por debajo del máximo previo (0 si nunca cayó)."""
    bajo = (eq < eq.cummax()).to_numpy()
    mejor = actual = 0
    for b in bajo:
        actual = actual + 1 if b else 0
        mejor = max(mejor, actual)
    return int(mejor)


def por_anio(eq: pd.Series) -> dict[int, float]:
    """Retorno de cada año calendario en %, encadenado: el año empieza en el cierre del anterior."""
    fin = eq.resample("YE").last()
    ini = pd.concat([pd.Series([eq.iloc[0]], index=[eq.index[0]]), fin.iloc[:-1]])
    return {int(a): float(v) for a, v in zip(fin.index.year, (fin.to_numpy() / ini.to_numpy() - 1) * 100)}


def calcular(equity: pd.Series, operaciones: int | None = None, exposicion: pd.Series | None = None) -> dict:
    """Métricas estándar de una curva de capital (en %, salvo los ratios).

    operaciones: cantidad de órdenes ejecutadas, si el simulador la conoce.
    exposicion:  serie booleana/0-1 diaria de «posicionado en el activo»; da el % del tiempo expuesto.
    """
    eq = a_diaria(equity)
    if len(eq) < 2 or eq.iloc[0] <= 0:
        raise ValueError("la curva necesita al menos dos días y capital inicial positivo")
    dias = (eq.index[-1] - eq.index[0]).days
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (DIAS_ANIO / dias) - 1 if total > -1 else -1.0
    r = eq.pct_change().dropna()
    vol = r.std() * math.sqrt(DIAS_ANIO)
    mdd = drawdown(eq).min()
    hay_muestra = len(r) >= MIN_DIAS_RATIOS
    sharpe = r.mean() / r.std() * math.sqrt(DIAS_ANIO) if hay_muestra and r.std() > 0 else None
    bajista = math.sqrt((np.minimum(r, 0) ** 2).mean())
    sortino = r.mean() / bajista * math.sqrt(DIAS_ANIO) if hay_muestra and bajista > 0 else None
    anios = por_anio(eq)
    out = {
        "desde": eq.index[0].strftime("%Y-%m-%d"),
        "hasta": eq.index[-1].strftime("%Y-%m-%d"),
        "dias": int(dias),
        "retorno_total_pct": total * 100,
        "cagr_pct": cagr * 100,
        "volatilidad_pct": vol * 100,
        "max_drawdown_pct": mdd * 100,
        "dias_en_dd_max": dias_en_drawdown_max(eq),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": cagr / abs(mdd) if mdd < 0 else None,
        "mejor_anio_pct": max(anios.values()),
        "peor_anio_pct": min(anios.values()),
        "anios_positivos": sum(v > 0 for v in anios.values()),
        "anios_total": len(anios),
        "operaciones": operaciones,
        "exposicion_pct": None,
    }
    if exposicion is not None:
        ex = exposicion.astype(float).resample("1D").last().reindex(eq.index).ffill().fillna(0)
        out["exposicion_pct"] = float(ex.mean() * 100)
    return out


def redondear(m: dict, dec: int = 2) -> dict:
    return {k: (round(v, dec) if isinstance(v, float) else v) for k, v in m.items()}
