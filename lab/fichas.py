"""
Ficha estándar de cada experimento (Sprint 4, punto 3.7 del plan): qué se probó, con qué datos y costos, contra qué
benchmark, qué dio, cómo se validó, qué limitaciones tiene y en qué etapa está (backtest, demo o real).

Las métricas salen de resultados/comparador.json (lab/comparador.py + lab/metricas.py), así que todas las fichas usan
la misma ventana, la misma comisión y la misma definición de cada métrica. Los textos van en es / pt / en para el sitio.

Uso:  python -m lab.fichas      (después de python -m lab.comparador)
Salida: resultados/fichas/<id>.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess

from lab.grid_bot import CACHE, SALIDA

DESTINO = os.path.join(SALIDA, "fichas")


def huella_datos() -> dict:
    """SHA-256 de las velas usadas (archivos mensuales del archivo público de Binance, en orden)."""
    h, n = hashlib.sha256(), 0
    for nombre in sorted(os.listdir(CACHE)):
        if nombre.endswith(".csv"):
            h.update(nombre.encode())
            with open(os.path.join(CACHE, nombre), "rb") as f:
                h.update(f.read())
            n += 1
    return {"sha256": h.hexdigest(), "archivos_mensuales": n}


def version_codigo() -> dict:
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "--short", "HEAD"), "cambios_sin_commit": bool(git("status", "--porcelain", "--untracked-files=no", "lab")),
            "repositorio": "https://github.com/wiqon/wiqon-lab"}


DATOS = {
    "fuente": "data.binance.vision — velas de 5 minutos BTCUSDT (spot), archivo público mensual",
    "frecuencia_simulacion": "5 minutos (las métricas se miden sobre el cierre diario, UTC)",
    "cobertura": "enero 2018 a septiembre 2026, sin huecos de meses",
}
SUPUESTOS = {
    "capital_inicial_usdt": 10_000,
    "comision_por_orden_pct": 0.1,
    "spread_y_slippage": {"es": "No se suma slippage aparte: el grid y el DCA bot usan órdenes límite; la regla de tendencia ejecuta a la apertura del día siguiente.",
                          "pt": "Não se soma slippage à parte: o grid e o robô DCA usam ordens limitadas; a regra de tendência executa na abertura do dia seguinte.",
                          "en": "No separate slippage is added: the grid and the DCA bot use limit orders; the trend rule executes at the next day's open."},
    "rendimiento_del_usdt": {"es": "El USDT no rinde nada (sin Earn) en esta comparación.", "pt": "O USDT não rende nada (sem Earn) nesta comparação.",
                             "en": "USDT earns nothing (no Earn) in this comparison."},
    "funding_y_swaps": {"es": "No aplica: spot sin apalancamiento.", "pt": "Não se aplica: spot sem alavancagem.", "en": "Not applicable: unleveraged spot."},
}
BENCHMARK = {"id": "hold", "es": "Comprar y mantener BTC, misma ventana y comisión", "pt": "Comprar e manter BTC, mesma janela e comissão",
             "en": "Buy and hold BTC, same window and fee"}

EXPERIMENTOS = {
    "tendencia-btc": {
        "numero": 3, "estrategia": "tendencia",
        "nombre": {"es": "Filtro de tendencia en BTC", "pt": "Filtro de tendência no BTC", "en": "Trend filter on BTC"},
        "hipotesis": {"es": "Estar en BTC solo cuando el cierre diario está sobre la media de 100 días reduce las caídas grandes sin perder la mayor parte de las subas.",
                      "pt": "Ficar em BTC só quando o fechamento diário está acima da média de 100 dias reduz as grandes quedas sem perder a maior parte das altas.",
                      "en": "Holding BTC only while the daily close is above the 100-day average cuts the big drawdowns without giving up most of the upside."},
        "reglas": {"es": ["Señal al cierre diario: ¿cierre > media simple de 100 días?", "Sí: 100 % en BTC. No: 100 % en USDT.", "La orden se ejecuta a la apertura del día siguiente."],
                   "pt": ["Sinal no fechamento diário: fechamento > média simples de 100 dias?", "Sim: 100 % em BTC. Não: 100 % em USDT.", "A ordem é executada na abertura do dia seguinte."],
                   "en": ["Signal at the daily close: close > 100-day simple average?", "Yes: 100% BTC. No: 100% USDT.", "The order executes at the next day's open."]},
        "temporalidad": "1D",
        "validacion": [
            {"tipo": "sensibilidad", "es": "18 combinaciones vecinas de parámetros (media 50/100/150, con y sin ajuste por volatilidad): todas entre 39 % y 57 % anual.",
             "pt": "18 combinações vizinhas de parâmetros (média 50/100/150, com e sem ajuste por volatilidade): todas entre 39 % e 57 % ao ano.",
             "en": "18 neighbouring parameter sets (50/100/150-day average, with and without volatility sizing): all between 39% and 57% a year.", "archivo": "resultados/tendencia_multi.csv"},
            {"tipo": "fuera_de_muestra", "es": "Se mide por separado 2018-2022 (dos bajistas) y 2023-2026.", "pt": "Mede-se separadamente 2018-2022 (dois mercados de baixa) e 2023-2026.",
             "en": "Measured separately for 2018-2022 (two bear markets) and 2023-2026.", "archivo": "resultados/comparador_metricas.csv"},
            {"tipo": "otro_broker", "es": "MetaTrader 5, CFD con swaps reales (Exness demo, 2022-2026): x2,02 contra x1,96 de mantener, caída de 39 % contra 67 %. Los swaps se llevan cerca de un tercio de la ventaja.",
             "pt": "MetaTrader 5, CFD com swaps reais (Exness demo, 2022-2026): x2,02 contra x1,96 de manter, queda de 39 % contra 67 %. Os swaps levam cerca de um terço da vantagem.",
             "en": "MetaTrader 5, CFD with real swaps (Exness demo, 2022-2026): x2.02 vs x1.96 for holding, 39% drawdown vs 67%. Swaps take about a third of the edge.", "archivo": "resultados/mt5/"},
        ],
        "advertencias": [
            {"es": "Rinde menos que mantener en los alcistas (2024-25: +55 % contra +93 %): su ventaja es proteger en los bajistas.",
             "pt": "Rende menos que manter nas altas (2024-25: +55 % contra +93 %): a vantagem é proteger nas baixas.",
             "en": "It lags holding in bull markets (2024-25: +55% vs +93%): its edge is protection in bear markets."},
            {"es": "Pocas operaciones por año: las cifras tienen más incertidumbre de la que sugieren los decimales.",
             "pt": "Poucas operações por ano: os números têm mais incerteza do que sugerem as casas decimais.",
             "en": "Few trades a year: the figures carry more uncertainty than their decimals suggest."},
            {"es": "Con el USDT en Earn el resultado es mayor (sección 3 del laboratorio); acá no se cuenta para comparar en igualdad.",
             "pt": "Com o USDT em Earn o resultado é maior (seção 3 do laboratório); aqui não se conta para comparar em igualdade.",
             "en": "With USDT in Earn the result is higher (lab section 3); it is left out here so all strategies compete on equal terms."},
            {"es": "Un solo activo y una sola historia: el futuro no tiene por qué parecerse.", "pt": "Um só ativo e uma só história: o futuro não precisa se parecer.",
             "en": "One asset and one history: the future need not look alike."},
        ],
        "clasificacion": {"backtest": "hecho", "demo": "hecho (MetaTrader 5, Exness)", "real": "en curso (bot en la Raspberry; sin historial suficiente para publicar)"},
    },
    "grid-bots": {
        "numero": 6, "estrategia": "grid",
        "nombre": {"es": "Grid bot de spot", "pt": "Robô de grid em spot", "en": "Spot grid bot"},
        "hipotesis": {"es": "Un grid bot de spot gana con la volatilidad: compra en cada bajada y vende en cada subida dentro de un rango.",
                      "pt": "Um robô de grid em spot ganha com a volatilidade: compra a cada queda e vende a cada alta dentro de uma faixa.",
                      "en": "A spot grid bot profits from volatility: it buys every dip and sells every rise inside a range."},
        "reglas": {"es": ["Rango de ±10 % alrededor del precio, 20 niveles geométricos.", "Compra al tocar un nivel por debajo; vende al siguiente nivel por encima.", "El rango se reinicia una vez por mes."],
                   "pt": ["Faixa de ±10 % em torno do preço, 20 níveis geométricos.", "Compra ao tocar um nível abaixo; vende no nível seguinte acima.", "A faixa é reiniciada uma vez por mês."],
                   "en": ["±10% range around the price, 20 geometric levels.", "Buys when a lower level is hit; sells at the next level up.", "The range resets once a month."]},
        "temporalidad": "5m",
        "validacion": [
            {"tipo": "sensibilidad", "es": "64 combinaciones (rango ±5/10/20/30 %, 10/20/50/100 niveles, reinicio cada 7/30/90 días o al salir): ninguna superó a mantener; mediana −9 % anual.",
             "pt": "64 combinações (faixa ±5/10/20/30 %, 10/20/50/100 níveis, reinício a cada 7/30/90 dias ou ao sair): nenhuma superou manter; mediana −9 % ao ano.",
             "en": "64 combinations (±5/10/20/30% range, 10/20/50/100 levels, reset every 7/30/90 days or on exit): none beat holding; median −9% a year.", "archivo": "resultados/grid_bot.csv"},
            {"tipo": "fuera_de_muestra", "es": "Mediana de las 64: −21,9 % anual en 2018-2022 y +9,8 % en 2023-2026.", "pt": "Mediana das 64: −21,9 % ao ano em 2018-2022 e +9,8 % em 2023-2026.",
             "en": "Median of the 64: −21.9% a year in 2018-2022 and +9.8% in 2023-2026.", "archivo": "resultados/grid_bot_resumen.csv"},
        ],
        "advertencias": [
            {"es": "La «ganancia del grid» del panel no es el resultado: suma las vueltas cerradas y no resta el BTC comprado en las bajas.",
             "pt": "O «lucro do grid» do painel não é o resultado: soma as voltas fechadas e não desconta o BTC comprado nas quedas.",
             "en": "The panel's «grid profit» is not the result: it adds closed round trips and ignores the BTC bought on the way down."},
            {"es": "Con rangos angostos y muchos niveles la distancia entre niveles es menor que las dos comisiones de cada vuelta.",
             "pt": "Com faixas estreitas e muitos níveis a distância entre níveis é menor que as duas comissões de cada volta.",
             "en": "With narrow ranges and many levels the level spacing is smaller than the two fees each round trip pays."},
            {"es": "La ventana empieza cerca de un máximo (enero de 2018).", "pt": "A janela começa perto de um topo (janeiro de 2018).", "en": "The window starts near a top (January 2018)."},
        ],
        "clasificacion": {"backtest": "hecho", "demo": "no", "real": "no"},
    },
    "dca-bot": {
        "numero": 7, "estrategia": "dca_bot",
        "nombre": {"es": "DCA bot (3Commas, Pionex, Cryptohopper)", "pt": "Robô DCA (3Commas, Pionex, Cryptohopper)", "en": "DCA bot (3Commas, Pionex, Cryptohopper)"},
        "hipotesis": {"es": "Comprar más a medida que baja y vender con una ganancia chica sobre el precio promedio da un ingreso estable.",
                      "pt": "Comprar mais conforme cai e vender com um lucro pequeno sobre o preço médio gera uma renda estável.",
                      "en": "Buying more as price falls and selling at a small profit over the average price yields a steady income."},
        "reglas": {"es": ["Orden base a mercado.", "8 órdenes de seguridad cada 2 % más abajo, cada una 1,5 veces la anterior.", "Toma de ganancia: 2 % sobre el precio promedio. Sin stop loss."],
                   "pt": ["Ordem base a mercado.", "8 ordens de segurança a cada 2 % mais abaixo, cada uma 1,5 vez a anterior.", "Realização de lucro: 2 % sobre o preço médio. Sem stop loss."],
                   "en": ["Market base order.", "8 safety orders every 2% lower, each 1.5× the previous.", "Take profit: 2% over the average price. No stop loss."]},
        "temporalidad": "5m",
        "validacion": [
            {"tipo": "sensibilidad", "es": "36 combinaciones (toma de ganancia 1/2/3 %, órdenes cada 1/2/4 %, 4 u 8 órdenes, escala 1 o 1,5): mediana +19,3 % anual con caída de −79,5 %; 4 superaron a mantener.",
             "pt": "36 combinações (lucro 1/2/3 %, ordens a cada 1/2/4 %, 4 ou 8 ordens, escala 1 ou 1,5): mediana +19,3 % ao ano com queda de −79,5 %; 4 superaram manter.",
             "en": "36 combinations (take profit 1/2/3%, orders every 1/2/4%, 4 or 8 orders, scale 1 or 1.5): median +19.3% a year with a −79.5% drawdown; 4 beat holding.", "archivo": "resultados/dca_bot.csv"},
            {"tipo": "fuera_de_muestra", "es": "Mediana de las 36: −3,6 % anual en 2018-2022 y +58,3 % en 2023-2026.", "pt": "Mediana das 36: −3,6 % ao ano em 2018-2022 e +58,3 % em 2023-2026.",
             "en": "Median of the 36: −3.6% a year in 2018-2022 and +58.3% in 2023-2026.", "archivo": "resultados/dca_bot_resumen.csv"},
        ],
        "advertencias": [
            {"es": "Sin stop loss es BTC comprado con más pasos: en los bajistas queda atrapado (ciclo más largo en la mediana: 1.030 días).",
             "pt": "Sem stop loss é BTC comprado com mais passos: nas baixas fica preso (ciclo mais longo na mediana: 1.030 dias).",
             "en": "Without a stop loss it is BTC bought in more steps: it gets trapped in bear markets (median longest cycle: 1,030 days)."},
            {"es": "El panel nunca muestra pérdida: solo suma los ciclos cerrados, que por construcción siempre ganan.",
             "pt": "O painel nunca mostra perda: só soma os ciclos fechados, que por construção sempre ganham.",
             "en": "The panel never shows a loss: it only adds closed cycles, which by design always win."},
            {"es": "El resultado final coincide con el panel solo porque el último ciclo cerró; en el camino la cuenta valió hasta un 31 % de lo que mostraba el panel.",
             "pt": "O resultado final coincide com o painel só porque o último ciclo fechou; no caminho a conta chegou a valer 31 % do que o painel mostrava.",
             "en": "The final result matches the panel only because the last cycle closed; along the way the account was worth as little as 31% of what the panel showed."},
        ],
        "clasificacion": {"backtest": "hecho", "demo": "no", "real": "no"},
    },
}


def main():
    comp = json.load(open(os.path.join(SALIDA, "comparador.json"), encoding="utf-8"))
    base = {"generado": dt.date.today().isoformat(), "version": version_codigo(), "datos": {**DATOS, "huella": huella_datos()},
            "supuestos": SUPUESTOS, "benchmark": BENCHMARK, "periodo": {"desde": comp["desde"], "hasta": comp["hasta"], "corte": comp["corte_periodos"]},
            "metodologia_metricas": "lab/metricas.py", "comparador": "lab/comparador.py"}
    os.makedirs(DESTINO, exist_ok=True)
    for fid, e in EXPERIMENTOS.items():
        est = comp["estrategias"][e["estrategia"]]
        ficha = {"id": fid, **{k: v for k, v in e.items() if k != "estrategia"}, **base,
                 "metricas": est["metricas"], "anios": est["anios"], "unidad_operaciones": est["unidad_operaciones"],
                 "benchmark_metricas": comp["estrategias"]["hold"]["metricas"],
                 "extra": {k: v for k, v in est.items() if k not in ("nombre", "metricas", "anios", "unidad_operaciones")}}
        with open(os.path.join(DESTINO, f"{fid}.json"), "w", encoding="utf-8") as f:
            json.dump(ficha, f, ensure_ascii=False, indent=1)
        m = est["metricas"]["completo"]
        print(f"#{e['numero']} {fid}: CAGR {m['cagr_pct']} % · caída máx {m['max_drawdown_pct']} % · Sharpe {m['sharpe']} → {DESTINO}/{fid}.json")
    if base["version"]["cambios_sin_commit"]:
        print("OJO: hay cambios sin commitear en lab/; regenerá las fichas después del commit para que el commit citado sea el correcto.")


if __name__ == "__main__":
    main()
