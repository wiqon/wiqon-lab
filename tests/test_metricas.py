"""Pruebas de lab/metricas.py con casos de resultado conocido (python -m pytest tests -q)."""
import math

import numpy as np
import pandas as pd
import pytest

from lab import metricas as M


def serie(valores, desde="2020-01-01", freq="1D"):
    return pd.Series(valores, index=pd.date_range(desde, periods=len(valores), freq=freq), dtype=float)


def test_crecimiento_constante_da_cagr_exacto():
    # +10 % en exactamente 365 días → CAGR 10 %, sin caídas
    eq = serie(100 * 1.1 ** (np.arange(366) / 365))
    m = M.calcular(eq)
    assert m["dias"] == 365
    assert m["cagr_pct"] == pytest.approx(10.0, abs=1e-9)
    assert m["retorno_total_pct"] == pytest.approx(10.0, abs=1e-9)
    assert m["max_drawdown_pct"] == 0
    assert m["dias_en_dd_max"] == 0
    assert m["calmar"] is None          # sin caída no hay Calmar


def test_drawdown_conocido_y_su_duracion():
    # 100 → 120 (máximo) → 90 (−25 %) → 130: la caída dura los 3 días que pasa debajo de 120
    eq = serie([100, 120, 110, 90, 100, 130])
    m = M.calcular(eq)
    assert m["max_drawdown_pct"] == pytest.approx(-25.0)
    assert m["dias_en_dd_max"] == 3


def test_intradia_se_mide_igual_que_su_cierre_diario():
    # una curva de 5 minutos da las mismas métricas que sus cierres diarios
    idx = pd.date_range("2021-01-01", periods=288 * 400, freq="5min")
    eq = pd.Series(100 + np.sin(np.arange(len(idx)) / 5000) * 10 + np.arange(len(idx)) / 2000, index=idx)
    assert M.calcular(eq) == M.calcular(eq.resample("1D").last())


def test_ratios_solo_con_muestra_suficiente():
    corta = serie(100 + np.random.default_rng(1).normal(0, 1, 30).cumsum())
    assert M.calcular(corta)["sharpe"] is None and M.calcular(corta)["sortino"] is None
    larga = serie(100 * np.exp(np.random.default_rng(2).normal(0.001, 0.02, 400).cumsum()))
    m = M.calcular(larga)
    r = larga.pct_change().dropna()
    assert m["sharpe"] == pytest.approx(r.mean() / r.std() * math.sqrt(365))
    assert m["volatilidad_pct"] == pytest.approx(r.std() * math.sqrt(365) * 100)


def test_sortino_ignora_la_volatilidad_al_alza():
    # mismas caídas, más subas: el Sortino mejora y el desvío bajista no cambia
    base = np.tile([0.01, -0.01], 200)
    alcista = np.tile([0.03, -0.01], 200)
    m1, m2 = M.calcular(serie(100 * np.cumprod(1 + base))), M.calcular(serie(100 * np.cumprod(1 + alcista)))
    assert m2["sortino"] > m1["sortino"]


def test_anios_encadenados():
    eq = pd.Series([100, 110, 99, 120], index=pd.to_datetime(["2020-01-01", "2020-12-31", "2021-12-31", "2022-06-30"]))
    a = M.por_anio(eq)
    assert a[2020] == pytest.approx(10.0) and a[2021] == pytest.approx(-10.0) and a[2022] == pytest.approx(120 / 99 * 100 - 100)


def test_exposicion():
    eq = serie(np.linspace(100, 110, 200))
    ex = pd.Series([1] * 50 + [0] * 150, index=eq.index)
    assert M.calcular(eq, exposicion=ex)["exposicion_pct"] == pytest.approx(25.0)


def test_rechaza_curvas_invalidas():
    with pytest.raises(ValueError):
        M.calcular(serie([100]))
    with pytest.raises(TypeError):
        M.calcular(pd.Series([1.0, 2.0]))
