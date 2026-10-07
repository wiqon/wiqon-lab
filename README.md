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
> survived. Code and results in Spanish.

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

---

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

📩 **Contacto:** wiqon027@gmail.com

### Seguinos

🌐 **[wiqon.github.io](https://wiqon.github.io)** · [YouTube](https://www.youtube.com/@wiqonlab) · [Instagram](https://www.instagram.com/wiqonlab/) · [Facebook](https://www.facebook.com/wiqonlab/) · [X](https://x.com/wiqonlab) · [TikTok](https://www.tiktok.com/@wiqonlab) · [Threads](https://www.threads.net/@wiqonlab) · [LinkedIn](https://www.linkedin.com/company/wiqonlab) · [Telegram](https://t.me/wiqonlab) · [Discord](https://discord.gg/8GDqe8H7R7) · [WhatsApp](https://whatsapp.com/channel/0029Vb8Tk5XCcW4zVk21xx0D) · [GitHub](https://github.com/wiqon)

**Principio WIQON:** crecer por evidencia, no por exageración. Se publican
los resultados favorables y los desfavorables.

---

⚠️ **Aviso:** esto es investigación, no asesoría financiera. Los resultados
pasados no garantizan resultados futuros. Operar cripto implica riesgo de
pérdida total del capital.

Licencia MIT.
