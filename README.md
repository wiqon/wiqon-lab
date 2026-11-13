<p align="center"><img src="assets/banner.png" alt="WIQON — Market Flow Intelligence" width="100%"></p>

# WIQON Lab

**Market Flow Intelligence · Documentar, no prometer.**

*Observar → Cuantificar → Verificar → Automatizar.*

Probando estrategias de trading cripto con datos reales y sin autoengaños.

La mayoría de las estrategias que "funcionan" en internet no sobreviven a
un backtest honesto. Este laboratorio es el conjunto de herramientas que
usé para evaluar más de 20 ideas sobre Binance: casi todas fallaron, y una
sobrevivió 8 años de datos. Acá están el código, la metodología y los
resultados, incluidos los que salieron mal.

> *English summary:* WIQON Lab — honest crypto backtesting lab — portfolio-level
> simulation, out-of-sample windows, bootstrap confidence intervals,
> survivorship-bias control, and read-only arbitrage monitors. Of 20+
> strategies tested on Binance data, only a simple BTC trend filter
> survived. Spot grid bots (64 settings, 2018-2026) never beat buy and hold,
> and the "grid profit" dashboards show hides the real result. Code and results in Spanish.

---

## Resultados principales

### 1. Una estrategia "validada" que era ruido

Estrategia intradía (EMA21 Bounce + filtros de RSI, ADX, MACD, volumen y
cooldown) que en 180 días daba **+0.25%** y parecía prometedora.
Re-evaluada con un backtest de portafolio a 3 años (2023-2026, 178 operaciones):

| Métrica | Resultado |
|---|---|
| Retorno total en 3 años | **+0.71%** |
| Resultado medio por operación | +0.02%, IC 95% [−0.25%, +0.30%] → **no se distingue de 0** |
| Ventanas de 6 meses por encima de Earn (+1.47%) | 1 de 6 (positivas: 2 de 6, una con +0.13%) |
| USDT en Earn al 3% (mismo período) | +9.3% |

**Lección:** con 28 operaciones y ~20 variantes probadas sobre los mismos
datos, "la mejor" casi siempre es suerte.

### 2. El sesgo de supervivencia infla todo

Rotación semanal de momentum entre altcoins, 5 años:

| Universo | Anualizado (config base) |
|---|---|
| 9 monedas elegidas **hoy** (sabiendo que sobrevivieron) | 33.7% |
| Top 30 de **septiembre 2021** (incluye LUNA, FTT, MATIC, EOS, XMR...) | **1.0%** |

Casi todo el resultado venía de haber elegido las monedas con el diario del
lunes. Detalle técnico: Binance **reutiliza símbolos** (LUNA/USDT fue la LUNA
original hasta 2022-05-13 y LUNA 2.0 desde 2022-05-31); sin cortar la serie,
el backtest ve un +17.000.000% falso.

### 3. Lo que sobrevivió: filtro de tendencia en BTC

Regla: estar en BTC si el cierre diario está sobre la media de 100 días; si
no, en USDT (rindiendo en Earn). Señal al cierre, ejecución a la apertura
siguiente, con comisiones y slippage. 8 años (2018-2026, incluye el crash de
2018, COVID 2020 y el bajista de 2022):

| | Anualizado | Caída máxima | Sharpe |
|---|---|---|---|
| **Filtro de tendencia, revisión diaria** | **56.6%** | **−34.7%** | **1.28** |
| Revisión semanal | 41.7% | −58.3% | 1.01 |
| Comprar y mantener BTC | 37.3% | −76.6% | 0.83 |

- 18 combinaciones vecinas de parámetros: todas entre 39% y 57% anual → no
  depende de una configuración puntual.
- Su ventaja es **proteger en mercados bajistas**; en alcistas rinde menos
  que mantener (2024-25: +55% vs +93%).
- Agregar ETH o ajustar el tamaño por volatilidad **no mejoró** de forma
  consistente (`lab/tendencia_multi.py`, 36 variantes).
- **En MetaTrader 5 (CFD con swaps reales, Exness demo, 2022-2026):** x2,02
  contra x1,96 de mantener, con caída de 39% contra 67%. Los swaps se llevan
  cerca de un tercio de la ventaja del spot. Informe: [`resultados/mt5/`](resultados/mt5/).

### 4. Arbitraje: medido, no supuesto

Monitores de solo lectura (`lab/arbitraje/`):

| Tipo | Resultado |
|---|---|
| Triangular dentro de Binance (120 snapshots) | 0 oportunidades; mejor ciclo −0.14% neto |
| Entre Binance / OKX / Bybit / KuCoin | máx +0.09% neto; techo teórico 0.16 USDT en 10 min |
| Con transferencia (6 exchanges, libro real) | Las diferencias grandes eran monedas distintas con el mismo ticker o retiros suspendidos |

### 5. Arbitraje de funding (cash-and-carry)

Spot largo + perpetuo corto, 6 años de funding real de Binance: 7-10% anual
promedio, pero casi todo de 2021 (30-48%). En 2025-26 ≈ 2-3% sobre el capital,
igual o menos que Earn.

### 6. Grid bots: la "ganancia del grid" no es tu ganancia

Grid de spot geométrico en BTC/USDT, como los de Binance, Pionex o Bitsgap:
compra al bajar a cada nivel y vende al subir al siguiente. Velas de 5
minutos de enero de 2018 a septiembre de 2026, comisión de 0,1% por orden,
64 combinaciones: rango ±5/10/20/30%, 10/20/50/100 niveles, y reinicio cada
7, 30 o 90 días o al salir del rango (`lab/grid_bot.py`):

| | Anualizado | Caída máxima |
|---|---|---|
| Grid, mediana de 64 combinaciones | −9.0% | −78.6% |
| Mejor grid (±30%, 10 niveles, reinicio semanal) | +12.4% | −48.3% |
| Comprar y mantener BTC (misma ventana) | +23.3% | −81.2% |
| Regla de tendencia (misma ventana, USDT sin rendimiento) | +42.7% | −38.5% |

- **0 de 64** combinaciones superaron a comprar y mantener; 17 terminaron en positivo.
- **La "ganancia del grid" que muestran los paneles no es el resultado:** en
  la mediana, las vueltas cerradas sumaron +237% mientras la cuenta terminó
  en −56%. Las vueltas ganan de a poco; el BTC comprado en las bajas pierde
  de golpe, y eso el panel no lo resta.
- **Rangos angostos con muchos niveles son lo peor:** con ±5% y 100 niveles
  la distancia entre niveles (0,1%) es menor que las dos comisiones de cada
  vuelta (0,2%): cada vuelta pierde plata.
- **Depende del mercado, no de la configuración:** el grid típico (±10%, 20
  niveles, reinicio mensual) ganó en años laterales (2019 +21%, 2023 +22%,
  2024 +36%), perdió fuerte en los bajistas (2018 −64%, 2022 −47%) y en el
  alcista de 2020 hizo −6% contra +302% de mantener. Mediana de las 64:
  −21.9% anual en 2018-2022 y +9.8% en 2023-2026.
- La ventana arranca en enero de 2018, cerca de un máximo, y el USDT no rinde:
  por eso mantener y la regla dan distinto que en la sección 3.

Detalle por combinación y por año: `resultados/grid_bot.csv`,
`resultados/grid_bot_anual.csv`, `resultados/grid_bot_resumen.csv`.

### 7. DCA: el de ahorro y el "DCA bot" no son lo mismo

Misma ventana (enero 2018 a septiembre 2026), comisión de 0,1% y USDT sin
rendimiento (`lab/dca.py`).

**DCA de ahorro.** US$ 10.000 de una vez contra 12 compras mensuales, para
cada uno de los 93 meses de inicio posibles:

| | Gana "de una vez" | Peor resultado de una vez | Peor resultado en 12 compras |
|---|---|---|---|
| Al terminar las compras (mes 12) | 61% de los inicios | US$ 2.835 | US$ 4.503 |
| A los 36 meses (69 inicios) | 56% de los inicios | US$ 10.088 | US$ 9.420 |

- Como BTC subió en el período, invertir todo de una vez gana más seguido
  (en la mediana, 13% más al mes 12). Repartir no mejora el promedio: achica
  el peor caso de entrar justo en un máximo: empezando en diciembre de 2021,
  al mes 12 quedaban US$ 2.966 de una vez contra US$ 5.838 repartido.

US$ 100 por semana durante todo el período (US$ 45.700 aportados):

| | Valor final | Múltiplo | Peor momento (valor / aportado) | Caída máxima |
|---|---|---|---|---|
| Comprar todas las semanas | US$ 235.795 | x5,16 | 45% | −74.6% |
| Comprar solo sobre la media de 100 días | US$ 236.906 | x5,18 | 53% | −74.5% |
| **Regla completa (también vende debajo de la media)** | **US$ 366.764** | **x8,03** | **82%** | **−43.8%** |

- Elegir cuándo comprar casi no cambia nada; lo que cambia el resultado es
  salir en los bajistas, igual que en la sección 3.

**DCA bot** (3Commas, Cryptohopper, Pionex): orden base, órdenes de seguridad
cada 1/2/4% más abajo (4 u 8, de igual tamaño o creciendo x1,5), toma de
ganancia de 1/2/3% sobre el precio promedio, sin stop loss, velas de 5
minutos. 36 combinaciones:

| | Anualizado | Caída máxima |
|---|---|---|
| DCA bot, mediana de 36 combinaciones | +19.3% | −79.5% |
| Mejor (tp 3%, 8 órdenes cada 2%, x1,5) | +29.8% | −73.6% |
| Típico (tp 2%, 8 órdenes cada 2%, x1,5) | +23.4% | −69.4% |
| Comprar y mantener BTC | +23.3% | −81.2% |
| Regla de tendencia (USDT sin rendimiento) | +42.7% | −38.5% |

- Las 36 terminaron en positivo y 4 superaron a mantener, pero con caídas
  del mismo tamaño: **un DCA bot sin stop loss es BTC comprado con más
  pasos.** En los bajistas se queda sin órdenes y atrapado: el deal más
  largo duró en la mediana 1.030 días (2,8 años), y el 81% del tiempo el bot
  no tenía más órdenes para poner.
- El típico hizo exactamente lo mismo que mantener en 2022 (−64%) y 2023
  (+156%): esos dos años estuvo atrapado con todo comprado.
- **El panel nunca muestra pérdida:** suma solo los deals cerrados, que por
  construcción siempre ganan. En noviembre de 2022 la cuenta del bot típico
  valía el 31% de lo que mostraba el panel. Al final coinciden porque el
  último deal cerró: la diferencia está en el camino, no en la foto final.
- Mediana de las 36: −3.6% anual en 2018-2022 y +58.3% en 2023-2026.

Detalle: `resultados/dca_una_vez_vs_12_compras.csv`,
`resultados/dca_ahorro_semanal.csv`, `resultados/dca_bot.csv`,
`resultados/dca_bot_anual.csv`, `resultados/dca_bot_resumen.csv`.

---

### 8. Comparador: todas en las mismas condiciones

Mismo activo (BTC/USDT spot), misma ventana (enero 2018 a septiembre 2026),
misma comisión (0,1 % por orden) y USDT sin rendimiento. Cada estrategia usa el
simulador que generó sus números publicados y todas se miden con la misma
definición de métricas, sobre la curva diaria (`lab/metricas.py`). Antes de
escribir resultados, el comparador verifica que reproduce los números de las
secciones 6 y 7 (`resultados/comparador_control.csv`).

| | Anualizado | Caída máxima | Días en caída | Sharpe | Sortino | Peor año | Años positivos |
|---|---|---|---|---|---|---|---|
| Comprar y mantener | 23,3 % | −81,2 % | 1.045 | 0,65 | 0,94 | −72,3 % | 5 de 9 |
| **Filtro de tendencia** | **42,7 %** | **−38,5 %** | **518** | **1,05** | **1,65** | **−27,7 %** | **7 de 9** |
| Grid típico | −13,8 % | −85,0 % | 3.180 | −0,11 | −0,15 | −63,9 % | 4 de 9 |
| DCA bot típico | 23,4 % | −69,4 % | 804 | 0,67 | 0,96 | −64,2 % | 7 de 9 |

Por período (anualizado):

| | 2018-2022 | 2023-2026 |
|---|---|---|
| Comprar y mantener | 4,3 % | 53,9 % |
| DCA bot típico | 4,0 % | 54,9 % |
| Grid típico | −28,0 % | 9,3 % |
| **Filtro de tendencia** | **44,9 %** | **39,7 %** |

- El filtro de tendencia es el único que rinde parecido en los dos períodos;
  el resto depende de que el mercado suba.
- El grid típico pasó 3.180 días por debajo de su máximo: nunca recuperó el
  capital inicial.
- El Sharpe del filtro acá (1,05) es menor que en la sección 3 (1,28) porque
  allá el USDT rinde en Earn; acá no, para comparar en igualdad.

Cada experimento tiene una **ficha estándar** en `resultados/fichas/`
(hipótesis, reglas, datos con su huella SHA-256, costos, benchmark, métricas,
validación, advertencias, etapa backtest/demo/real y el commit que la generó).

## Metodología (lo que la mayoría de los backtests no hace)

- **Portafolio real:** un solo capital compartido, tamaño de posición y tope
  de posiciones simultáneas como en el bot en vivo — no "100% del capital en
  cada símbolo por separado y promediar".
- **Sin mirar el futuro:** señal al cierre de la vela, ejecución en la
  apertura siguiente; la vela en curso nunca se usa.
- **Pesimista donde hay ambigüedad:** si en una vela se tocan TP y SL, se
  asume SL.
- **Costos reales:** comisión por lado + slippage en cada orden a mercado.
- **Fuera de muestra:** varios años cortados en ventanas no solapadas.
- **Intervalo de confianza** (bootstrap) del resultado medio por operación.
- **Robustez:** grillas de parámetros vecinos; se reporta la mediana, no la mejor.
- **Sesgo de supervivencia:** universos definidos con la información
  disponible *al inicio* del período.
- **Benchmarks pasivos:** comprar y mantener, canasta, y rendimiento de Earn.

---

## Cómo usarlo

```bash
pip install -r requirements.txt

# Backtest de portafolio por ventanas (estrategia intradía EMA21)
python -m lab.portafolio --days 1095

# Momentum / filtro de tendencia
python -m lab.momentum_diario --universo top30_2021 --grilla-top 3 5
python -m lab.momentum_diario --symbols BTC/USDT --top 1 --grilla-top 1 --days 3000 --rebalanceo-dias 1

# Variantes multi-activo y tamaño por volatilidad
python -m lab.tendencia_multi

# Grid bot de spot (64 combinaciones, velas de 5 minutos del archivo público de Binance)
python -m lab.grid_bot

# DCA de ahorro y DCA bot (36 combinaciones, mismas velas de 5 minutos)
python -m lab.dca

# Comparador en las mismas condiciones y fichas estándar (después de grid y DCA)
python -m lab.comparador
python -m lab.fichas
python -m pytest tests -q

# Arbitraje de funding
python -m lab.funding_carry

# Monitores de arbitraje (solo lectura, no operan)
python -m lab.arbitraje.monitor triangular --minutos 10
python -m lab.arbitraje.monitor_transferencia --horas 24
```

Solo usa datos públicos de los exchanges: no necesita claves de API. Las
velas se guardan en `data/` (cache) y los resultados en `resultados_backtest/`.

## Estructura

```
lab/
  datos.py             descarga paginada de velas (ccxt)
  portafolio.py        backtest de portafolio + ventanas + bootstrap
  momentum_diario.py   rotación de momentum / filtro de tendencia + robustez
  tendencia_multi.py   multi-activo y tamaño por volatilidad
  funding_carry.py     arbitraje de funding con historial real
  grid_bot.py          grid bot de spot (velas de 5 min) y "ganancia del grid" vs resultado real
  dca.py               DCA de ahorro (de una vez vs repartido, semanal con tendencia) y DCA bot vs panel
  arbitraje/
    monitor.py                 triangular y entre exchanges
    monitor_transferencia.py   con transferencia, libro de órdenes y estado de retiros
resultados/            CSV de las corridas citadas arriba
```

---

## Servicios

¿Tenés una estrategia o un bot y querés saber si realmente funciona antes
de arriesgar plata? WIQON ofrece **auditorías de estrategias** con esta
metodología y **bots a medida para Binance** (órdenes OCO, Earn, alertas
por Telegram, kill switch).

📩 **Contacto:** contacto@wiqonlab.com

### Seguinos

🌐 **[wiqonlab.com](https://wiqonlab.com)** · [YouTube](https://www.youtube.com/@wiqonlab) · [Instagram](https://www.instagram.com/wiqonlab/) · [Facebook](https://www.facebook.com/wiqonlab/) · [X](https://x.com/wiqonlab) · [TikTok](https://www.tiktok.com/@wiqonlab) · [Threads](https://www.threads.net/@wiqonlab) · [LinkedIn](https://www.linkedin.com/company/wiqonlab) · [Telegram](https://t.me/wiqonlab) · [Discord](https://discord.gg/8GDqe8H7R7) · [WhatsApp](https://whatsapp.com/channel/0029Vb8Tk5XCcW4zVk21xx0D) · [GitHub](https://github.com/wiqon)

**Principio WIQON:** crecer por evidencia, no por exageración. Se publican
los resultados favorables y los desfavorables.

---

⚠️ **Aviso:** esto es investigación, no asesoría financiera. Los resultados
pasados no garantizan resultados futuros. Operar cripto implica riesgo de
pérdida total del capital.

Licencia MIT.
