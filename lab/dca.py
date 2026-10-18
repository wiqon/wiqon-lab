"""
DCA en BTC/USDT con datos reales: dos cosas distintas que se venden con la misma palabra.

A) DCA de ahorro: comprar un monto fijo cada semana o cada mes.
   A1. Invertir US$ 10.000 de una vez contra repartirlos en 12 compras mensuales, para cada mes de inicio
       desde enero de 2018. Se mide al terminar las 12 compras (mes 12) y a los 36 meses.
   A2. Aportar US$ 100 por semana de 2018 a 2026, de tres formas: comprar siempre; comprar solo cuando el
       cierre diario está sobre la media de 100 días (si no, el USDT espera); y la regla completa (además
       vende todo cuando el cierre cae debajo de la media).

B) "DCA bot" de trading (3Commas, Cryptohopper, Pionex, Bitsgap): una orden base, órdenes de seguridad
   cada vez más abajo para bajar el precio promedio y toma de ganancia sobre ese promedio. Al cerrar un
   ciclo ("deal") abre otro en la vela siguiente.
   - Tamaños: orden base = 1 unidad, orden de seguridad k = escala_volumen^(k−1) unidades. La unidad se
     calcula para que la base más todas las órdenes de seguridad usen todo el capital disponible al abrir:
     el bot nunca se queda sin plata para la siguiente orden (lo que recomiendan las plataformas).
   - Órdenes de seguridad cada desv % por debajo del precio de entrada (desviaciones iguales).
   - Toma de ganancia: tp % sobre el precio promedio (con la comisión de compra incluida).
   - Sin stop loss (la configuración habitual). Comisión 0,1 % por orden. Órdenes límite sin slippage;
     la orden base va a mercado, a la apertura de la vela.
   - Recorrido dentro de la vela de 5 minutos: Apertura → Mínimo → Máximo → Cierre si cerró en alza,
     Apertura → Máximo → Mínimo → Cierre si cerró en baja. Si toma ganancia, el resto de la vela se ignora.
   - El USDT que espera no rinde.
   Se separa lo que muestra el panel (suma de las ganancias de los deals cerrados) del valor real de la
   cuenta, que incluye el deal abierto atrapado abajo.

Uso:
    python -m lab.dca            # todo (36 configuraciones del bot)
    python -m lab.dca --rapido   # 4 configuraciones del bot
"""
import argparse
import logging
import os

import numpy as np
import pandas as pd

from lab.grid_bot import COMISION, SALIDA, anual, comprar_y_mantener, metricas, regla_tendencia, velas_5m

log = logging.getLogger("dca")


def diario(df: pd.DataFrame) -> pd.Series:
    return df["c"].resample("1D").last().dropna()


def precio_desde(d: pd.Series, fecha) -> tuple:
    """Primer cierre diario en o después de la fecha (None si ya no hay datos)."""
    i = d.index.searchsorted(fecha)
    return (d.index[i], float(d.iloc[i])) if i < len(d) else (None, None)


# ------------------------------------------------------------------ A1: de una vez contra 12 compras
def una_vez_contra_dca(d: pd.Series, capital=10_000.0, compras=12) -> pd.DataFrame:
    filas = []
    for ini in pd.date_range(d.index[0].normalize(), d.index[-1], freq="MS"):
        f12, p12 = precio_desde(d, ini + pd.DateOffset(months=compras))
        if f12 is None or f12 > d.index[-1]:
            break
        precios = [precio_desde(d, ini + pd.DateOffset(months=k))[1] for k in range(compras)]
        btc_una = capital * (1 - COMISION) / precios[0]
        btc_dca = sum(capital / compras * (1 - COMISION) / p for p in precios)
        f36, p36 = precio_desde(d, ini + pd.DateOffset(months=36))
        hay36 = f36 is not None and (f36 - (ini + pd.DateOffset(months=36))).days <= 3
        filas.append({"inicio": ini.strftime("%Y-%m"),
                      "una_vez_mes12": round(btc_una * p12, 0), "dca_mes12": round(btc_dca * p12, 0),
                      "una_vez_mes36": round(btc_una * p36, 0) if hay36 else np.nan,
                      "dca_mes36": round(btc_dca * p36, 0) if hay36 else np.nan})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ A2: ahorro semanal
def ahorro_semanal(d: pd.Series, aporte=100.0) -> tuple:
    sma = d.rolling(100).mean()
    dentro_ayer = (d > sma).shift(1, fill_value=False).to_numpy()  # señal del cierre anterior
    lunes = d.index.dayofweek == 0
    aportado = 0.0
    siempre_btc = 0.0
    acum_btc, acum_usdt = 0.0, 0.0
    regla_btc, regla_usdt = 0.0, 0.0
    filas = []
    for i, (fecha, p) in enumerate(d.items()):
        if lunes[i]:
            aportado += aporte
            siempre_btc += aporte * (1 - COMISION) / p
            acum_usdt += aporte
            regla_usdt += aporte
        if dentro_ayer[i]:
            if acum_usdt > 0:
                acum_btc += acum_usdt * (1 - COMISION) / p; acum_usdt = 0.0
            if regla_usdt > 0:
                regla_btc += regla_usdt * (1 - COMISION) / p; regla_usdt = 0.0
        elif regla_btc > 0:
            regla_usdt += regla_btc * p * (1 - COMISION); regla_btc = 0.0
        filas.append((fecha, aportado, siempre_btc * p, acum_btc * p + acum_usdt, regla_btc * p + regla_usdt))
    serie = pd.DataFrame(filas, columns=["fecha", "aportado", "siempre", "solo_en_tendencia", "regla_completa"]).set_index("fecha")
    serie = serie[serie["aportado"] > 0]
    resumen = {}
    for col, nombre in (("siempre", "Comprar todas las semanas"), ("solo_en_tendencia", "Comprar solo sobre la media de 100 días"),
                        ("regla_completa", "Regla completa (también vende debajo de la media)")):
        rel = serie[col] / serie["aportado"]
        resumen[nombre] = {"aportado": round(serie["aportado"].iloc[-1], 0), "valor_final": round(serie[col].iloc[-1], 0),
                           "multiplo": round(rel.iloc[-1], 2), "peor_valor_sobre_aportado_pct": round(rel.min() * 100, 1),
                           "fecha_peor": rel.idxmin().strftime("%Y-%m-%d"),
                           "max_caida_pct": round((serie[col] / serie[col].cummax() - 1).min() * 100, 1)}
    return serie, pd.DataFrame(resumen).T


# ------------------------------------------------------------------ B: DCA bot
def simular_dca_bot(df: pd.DataFrame, tp: float, desv: float, n_so: int, escala: float, capital: float = 10_000.0):
    o, h, l, c = (df[k].to_numpy().tolist() for k in ("o", "h", "l", "c"))
    dias = df.index.floor("D")
    dia = dias.asi8.tolist()
    pesos = [1.0] + [escala ** k for k in range(n_so)]
    total = sum(pesos)
    factores = [1 - desv * (k + 1) for k in range(n_so)]
    assert factores[-1] > 0, "las órdenes de seguridad bajan de cero"
    q = 1 - COMISION
    caja, btc, costo = capital, 0.0, 0.0
    en_deal, niveles, montos, k, tp_px = False, [], [], 0, float("inf")
    realizado, deals, abierto_desde, max_dias, velas_sin_balas = 0.0, 0, 0, 0.0, 0
    eq_t, eq_v, eq_p = [], [], []
    N = len(c)
    for i in range(N):
        if not en_deal:  # orden base a mercado
            p = o[i]
            unidad = caja / total
            gasto = unidad * pesos[0]
            btc, costo, caja = gasto * q / p, gasto, caja - gasto
            niveles, montos, k = [p * f for f in factores], [unidad * w for w in pesos[1:]], 0
            tp_px = costo / btc * (1 + tp)
            en_deal, abierto_desde = True, i
        tramos = ("baja", "sube") if c[i] >= o[i] else ("sube", "baja", "cierre")
        for tramo in tramos:
            if tramo == "baja":
                while k < n_so and l[i] <= niveles[k]:
                    gasto = montos[k]
                    btc += gasto * q / niveles[k]; costo += gasto; caja -= gasto; k += 1
                tp_px = costo / btc * (1 + tp)
            else:
                techo = h[i] if tramo == "sube" else c[i]
                if techo >= tp_px:
                    ingreso = btc * tp_px * q
                    realizado += ingreso - costo; caja += ingreso
                    btc = costo = 0.0
                    deals += 1
                    max_dias = max(max_dias, (i - abierto_desde) / 288)
                    en_deal = False
                    break
        if en_deal and k == n_so:
            velas_sin_balas += 1
        if i == N - 1 or dia[i + 1] != dia[i]:
            eq_t.append(dia[i]); eq_v.append(caja + btc * c[i]); eq_p.append(capital + realizado)
    if en_deal:
        max_dias = max(max_dias, (N - 1 - abierto_desde) / 288)
    idx = dias.unique()
    assert len(idx) == len(eq_v)
    return pd.Series(eq_v, index=idx), pd.Series(eq_p, index=idx), {
        "ganancia_panel_usdt": realizado, "deals": deals, "deal_mas_largo_dias": max_dias,
        "tiempo_sin_ordenes_pct": velas_sin_balas / N * 100,
        "deal_abierto_al_final_pct": (btc * c[-1] - costo) / capital * 100 if en_deal else 0.0}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2018-01"); ap.add_argument("--hasta", default="2026-09")
    ap.add_argument("--rapido", action="store_true")
    a = ap.parse_args()
    df = velas_5m(a.desde, a.hasta)
    d = diario(df)
    os.makedirs(SALIDA, exist_ok=True)

    # A1
    a1 = una_vez_contra_dca(d)
    a1.to_csv(os.path.join(SALIDA, "dca_una_vez_vs_12_compras.csv"), index=False)
    r12 = a1["una_vez_mes12"] / a1["dca_mes12"]
    b36 = a1.dropna()
    r36 = b36["una_vez_mes36"] / b36["dca_mes36"]
    res_a1 = {"inicios": len(a1), "gana_una_vez_mes12_pct": round((r12 > 1).mean() * 100, 1),
              "mediana_una_vez_sobre_dca_mes12": round(r12.median(), 3),
              "peor_una_vez_mes12": a1["una_vez_mes12"].min(), "peor_dca_mes12": a1["dca_mes12"].min(),
              "inicios_36m": len(b36), "gana_una_vez_mes36_pct": round((r36 > 1).mean() * 100, 1),
              "mediana_una_vez_sobre_dca_mes36": round(r36.median(), 3),
              "peor_una_vez_mes36": b36["una_vez_mes36"].min(), "peor_dca_mes36": b36["dca_mes36"].min()}
    print("\nA1 · US$ 10.000 de una vez contra 12 compras mensuales\n" + pd.Series(res_a1).to_string())

    # A2
    serie, res_a2 = ahorro_semanal(d)
    serie.resample("W").last().round(0).to_csv(os.path.join(SALIDA, "dca_ahorro_semanal_serie.csv"))
    res_a2.to_csv(os.path.join(SALIDA, "dca_ahorro_semanal.csv"))
    print("\nA2 · US$ 100 por semana\n" + res_a2.to_string())

    # B
    hold, trend = comprar_y_mantener(df), regla_tendencia(df)
    bh = metricas(hold)
    tps = [0.02] if a.rapido else [0.01, 0.02, 0.03]
    desvs = [0.01, 0.03] if a.rapido else [0.01, 0.02, 0.04]
    n_sos = [8] if a.rapido else [4, 8]
    escalas = [1.0, 1.5] if a.rapido else [1.0, 1.5]
    corte = pd.Timestamp("2023-01-01")
    filas, anuales = [], {"Comprar y mantener": anual(hold), "Regla de tendencia (media 100 d)": anual(trend)}
    for tp in tps:
        for dv in desvs:
            for n in n_sos:
                for es in escalas:
                    eq, panel, g = simular_dca_bot(df, tp, dv, n, es)
                    m = metricas(eq)
                    dentro, fuera = metricas(eq[eq.index < corte]), metricas(eq[eq.index >= corte])
                    filas.append({"tp_pct": tp * 100, "desv_pct": dv * 100, "ordenes_seguridad": n, "escala_volumen": es,
                                  "cubre_caida_pct": round(dv * n * 100, 0), **{k: round(v, 2) for k, v in m.items()},
                                  "anualizado_2018_2022_pct": round(dentro["anualizado_pct"], 2),
                                  "anualizado_2023_2026_pct": round(fuera["anualizado_pct"], 2),
                                  "ganancia_panel_pct": round(g["ganancia_panel_usdt"] / 100, 2), "deals": g["deals"],
                                  "deal_mas_largo_dias": round(g["deal_mas_largo_dias"], 0),
                                  "tiempo_sin_ordenes_pct": round(g["tiempo_sin_ordenes_pct"], 1),
                                  "deal_abierto_al_final_pct": round(g["deal_abierto_al_final_pct"], 2)})
                    log.info("tp %.0f%% · desv %.0f%% · %d órdenes · escala %.1f → %.1f%% anual · DD %.1f%% · panel %+.0f%% vs real %+.0f%% · deal más largo %d días",
                             tp * 100, dv * 100, n, es, m["anualizado_pct"], m["max_dd_pct"], filas[-1]["ganancia_panel_pct"],
                             m["retorno_pct"], g["deal_mas_largo_dias"])
                    if (tp, dv, n, es) == (0.02, 0.02, 8, 1.5):
                        anuales["DCA bot típico (tp 2 %, 8 órdenes cada 2 %, escala 1,5)"] = anual(eq)
                        pd.DataFrame({"cuenta": eq.round(0), "panel": panel.round(0)}).to_csv(os.path.join(SALIDA, "dca_bot_serie_tipica.csv"))
    tabla = pd.DataFrame(filas)
    tabla.to_csv(os.path.join(SALIDA, "dca_bot.csv"), index=False)
    pd.DataFrame(anuales).round(1).to_csv(os.path.join(SALIDA, "dca_bot_anual.csv"))
    tr = metricas(trend)
    resumen = {
        "combinaciones": len(tabla),
        "mediana_anualizado_pct": round(tabla["anualizado_pct"].median(), 1),
        "mejor_anualizado_pct": round(tabla["anualizado_pct"].max(), 1),
        "peor_anualizado_pct": round(tabla["anualizado_pct"].min(), 1),
        "mediana_max_dd_pct": round(tabla["max_dd_pct"].median(), 1),
        "mediana_ganancia_panel_pct": round(tabla["ganancia_panel_pct"].median(), 1),
        "mediana_retorno_real_pct": round(tabla["retorno_pct"].median(), 1),
        "mediana_deal_mas_largo_dias": round(tabla["deal_mas_largo_dias"].median(), 0),
        "mediana_tiempo_sin_ordenes_pct": round(tabla["tiempo_sin_ordenes_pct"].median(), 1),
        "combinaciones_que_superan_hold": int((tabla["anualizado_pct"] > bh["anualizado_pct"]).sum()),
        "combinaciones_positivas": int((tabla["retorno_pct"] > 0).sum()),
        "hold_anualizado_pct": round(bh["anualizado_pct"], 1), "hold_max_dd_pct": round(bh["max_dd_pct"], 1),
        "tendencia_anualizado_pct": round(tr["anualizado_pct"], 1), "tendencia_max_dd_pct": round(tr["max_dd_pct"], 1),
        "mediana_2018_2022_pct": round(tabla["anualizado_2018_2022_pct"].median(), 1),
        "mediana_2023_2026_pct": round(tabla["anualizado_2023_2026_pct"].median(), 1),
    }
    pd.Series(resumen).to_csv(os.path.join(SALIDA, "dca_bot_resumen.csv"), header=["valor"])
    pd.Series(res_a1).to_csv(os.path.join(SALIDA, "dca_una_vez_vs_12_compras_resumen.csv"), header=["valor"])
    print("\nB · DCA bot\n" + pd.Series(resumen).to_string())
    print(pd.DataFrame(anuales).round(1).to_string())
    print(tabla.sort_values("anualizado_pct", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
