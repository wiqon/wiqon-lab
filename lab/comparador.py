"""
Comparador de estrategias en las MISMAS condiciones (Sprint 4 del plan de evolución).

Mismo activo (BTC/USDT spot de Binance), misma ventana (velas de 5 minutos de enero de 2018 a septiembre de 2026),
mismo capital inicial y misma comisión (0,1 % por orden). Cada estrategia usa el simulador que generó los números
ya publicados; acá solo se alinean las curvas y se miden con lab/metricas.py (una sola definición para todas).

    Comprar y mantener      compra al inicio y no vende (benchmark)
    Filtro de tendencia     BTC si el cierre diario está sobre la media de 100 días; si no, USDT (sin rendimiento)
    Grid típico             ±10 %, 20 niveles, reinicio mensual (lab/grid_bot.py)
    DCA bot típico          toma de ganancia 2 %, 8 órdenes de seguridad cada 2 %, escala 1,5 (lab/dca.py)

Control: antes de escribir nada, el script vuelve a calcular el anualizado y la caída máxima con el criterio de los
experimentos #6 y #7 y los compara con los CSV publicados; si no coinciden, se detiene.

Uso:  python -m lab.comparador
Salida: resultados/comparador_{serie,metricas,anual,control}.csv y resultados/comparador.json
"""
from __future__ import annotations

import json
import logging
import os

import pandas as pd

from lab import metricas as M
from lab.dca import simular_dca_bot
from lab.grid_bot import COMISION, SALIDA, comprar_y_mantener, metricas as metricas_publicadas, regla_tendencia, simular_grid, velas_5m

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("comparador")

DESDE, HASTA = "2018-01", "2026-09"
CORTE = "2023-01-01"   # mismo corte que el experimento #6: 2018-2022 (incluye dos bajistas) contra 2023-2026
ESTRATEGIAS = {
    "hold": "Comprar y mantener BTC",
    "tendencia": "Filtro de tendencia (media de 100 días)",
    "grid": "Grid típico (±10 %, 20 niveles, reinicio mensual)",
    "dca_bot": "DCA bot típico (TP 2 %, 8 órdenes cada 2 %, escala 1,5)",
}
TOLERANCIA = 0.15      # puntos porcentuales entre lo recalculado y lo publicado (los CSV publicados redondean a 2 decimales)


def correr(df: pd.DataFrame) -> tuple[dict, dict]:
    curvas, extra = {}, {}
    curvas["hold"] = comprar_y_mantener(df)
    extra["hold"] = {"operaciones": 1, "unidad_operaciones": "compra inicial", "exposicion": None}

    curvas["tendencia"] = regla_tendencia(df)
    d = df["c"].resample("1D").last().dropna()
    dentro = (d > d.rolling(100).mean()).shift(1).fillna(False).astype(int)
    extra["tendencia"] = {"operaciones": int(dentro.diff().abs().sum()), "unidad_operaciones": "órdenes (entradas y salidas)",
                          "exposicion": dentro}

    log.info("simulando el grid típico (velas de 5 minutos)…")
    curvas["grid"], g = simular_grid(df, 0.10, 20, "30d")
    extra["grid"] = {"operaciones": int(g["vueltas"]), "unidad_operaciones": "vueltas cerradas (compra + venta)", "exposicion": None,
                     "ganancia_mostrada_por_el_panel_pct": g["ganancia_grid_usdt"] / 100, "comisiones_pct_del_capital": g["comisiones_usdt"] / 100}

    log.info("simulando el DCA bot típico (velas de 5 minutos)…")
    eq, panel, info = simular_dca_bot(df, 0.02, 0.02, 8, 1.5)
    curvas["dca_bot"] = eq
    extra["dca_bot"] = {"operaciones": int(info["deals"]), "unidad_operaciones": "ciclos cerrados (deals)", "exposicion": None,
                        "ganancia_mostrada_por_el_panel_pct": info["ganancia_panel_usdt"] / 100, "ciclo_mas_largo_dias": info["deal_mas_largo_dias"]}
    return curvas, extra


def control(curvas: dict) -> pd.DataFrame:
    """Recalcula con el criterio publicado y compara contra los CSV de los experimentos #6 y #7."""
    grid = pd.read_csv(os.path.join(SALIDA, "grid_bot.csv"))
    fila_g = grid[(grid.amplitud_pct == 10) & (grid.niveles == 20) & (grid.reinicio == "30d")].iloc[0]
    resumen_g = pd.read_csv(os.path.join(SALIDA, "grid_bot_resumen.csv"), index_col=0)["valor"].astype(float)
    dca = pd.read_csv(os.path.join(SALIDA, "dca_bot.csv"))
    fila_d = dca[(dca.tp_pct == 2) & (dca.desv_pct == 2) & (dca.ordenes_seguridad == 8) & (dca.escala_volumen == 1.5)].iloc[0]
    publicados = {
        "hold": (resumen_g["hold_anualizado_pct"], resumen_g["hold_max_dd_pct"]),
        "tendencia": (resumen_g["tendencia_anualizado_pct"], resumen_g["tendencia_max_dd_pct"]),
        "grid": (fila_g["anualizado_pct"], fila_g["max_dd_pct"]),
        "dca_bot": (fila_d["anualizado_pct"], fila_d["max_dd_pct"]),
    }
    filas = []
    for k, (an_pub, dd_pub) in publicados.items():
        m = metricas_publicadas(curvas[k])
        filas.append({"estrategia": k, "anualizado_publicado": an_pub, "anualizado_recalculado": round(m["anualizado_pct"], 2),
                      "max_dd_publicado": dd_pub, "max_dd_recalculado": round(m["max_dd_pct"], 2),
                      "coincide": abs(m["anualizado_pct"] - an_pub) <= TOLERANCIA and abs(m["max_dd_pct"] - dd_pub) <= TOLERANCIA})
    return pd.DataFrame(filas)


def main():
    df = velas_5m(DESDE, HASTA)
    log.info("velas: %d (%s → %s)", len(df), df.index[0], df.index[-1])
    curvas, extra = correr(df)

    ctl = control(curvas)
    print(ctl.to_string(index=False))
    if not ctl["coincide"].all():
        raise SystemExit("CONTROL FALLIDO: el comparador no reproduce los números publicados; no se escribe nada.")

    diarias = {k: M.a_diaria(v) for k, v in curvas.items()}
    inicio = max(s.index[0] for s in diarias.values())
    fin = min(s.index[-1] for s in diarias.values())
    diarias = {k: s.loc[inicio:fin] for k, s in diarias.items()}

    filas, resultado = [], {"desde": str(inicio.date()), "hasta": str(fin.date()), "activo": "BTC/USDT (Binance spot)",
                            "comision_por_orden_pct": COMISION * 100, "capital_inicial_usdt": 10_000, "corte_periodos": CORTE,
                            "estrategias": {}}
    for k, eq in diarias.items():
        ex = extra[k]["exposicion"]
        periodos = {"completo": eq, "2018-2022": eq.loc[:pd.Timestamp(CORTE) - pd.Timedelta(days=1)], "2023-2026": eq.loc[CORTE:]}
        res = {}
        for p, s in periodos.items():
            m = M.redondear(M.calcular(s, operaciones=extra[k]["operaciones"] if p == "completo" else None,
                                       exposicion=ex.loc[s.index[0]:s.index[-1]] if ex is not None else (pd.Series(1, index=s.index) if k == "hold" else None)))
            res[p] = m
            filas.append({"estrategia": k, "nombre": ESTRATEGIAS[k], "periodo": p, **m})
        resultado["estrategias"][k] = {"nombre": ESTRATEGIAS[k], "unidad_operaciones": extra[k]["unidad_operaciones"], "metricas": res,
                                       "anios": {str(a): round(v, 1) for a, v in M.por_anio(eq).items()},
                                       **{kk: round(vv, 1) for kk, vv in extra[k].items() if kk not in ("operaciones", "unidad_operaciones", "exposicion")}}

    os.makedirs(SALIDA, exist_ok=True)
    serie = pd.DataFrame({k: (s / s.iloc[0] * 100).round(2) for k, s in diarias.items()})
    serie.index.name = "fecha"
    serie.to_csv(os.path.join(SALIDA, "comparador_serie.csv"))
    pd.DataFrame(filas).to_csv(os.path.join(SALIDA, "comparador_metricas.csv"), index=False)
    pd.DataFrame({k: M.por_anio(s) for k, s in diarias.items()}).round(1).to_csv(os.path.join(SALIDA, "comparador_anual.csv"), index_label="anio")
    ctl.to_csv(os.path.join(SALIDA, "comparador_control.csv"), index=False)
    with open(os.path.join(SALIDA, "comparador.json"), "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)

    tabla = pd.DataFrame(filas)
    print(tabla[tabla.periodo == "completo"][["estrategia", "cagr_pct", "max_drawdown_pct", "dias_en_dd_max", "volatilidad_pct", "sharpe", "sortino",
                                              "calmar", "peor_anio_pct", "anios_positivos", "anios_total", "operaciones", "exposicion_pct"]].to_string(index=False))
    print(tabla.pivot(index="estrategia", columns="periodo", values="cagr_pct").to_string())


if __name__ == "__main__":
    main()
