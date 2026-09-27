"""
Backtest de PORTAFOLIO + validación por ventanas de la estrategia que corre
en vivo por el bot (módulo "scanner") (EMA21 Bounce + Cooldown 72h).

Qué cambia respecto de los comparativa_*.py (backtesting.py por símbolo):
- Un solo capital compartido entre todos los símbolos, con el mismo tamaño
  de posición (POSITION_SIZE_PCT) y tope de posiciones simultáneas
  (MAX_CONCURRENT_POSITIONS) que el bot real — no "100% del capital en cada
  símbolo por separado y después promediar".
- Señal evaluada al CIERRE de la vela, entrada en la APERTURA de la vela
  siguiente + slippage. TP/SL como la OCO real (fijos sobre el precio de
  entrada). Si en una misma vela se tocan TP y SL, se asume SL (pesimista:
  con velas de 1h no se sabe cuál ocurrió primero).
- Salida por la MISMA señal de venta que usa el scanner (patrón EMA21
  bajista), además de la OCO. Cooldown solo tras SL, límite de pérdida
  diaria — todo como en el scanner.
- Varios años de historia cortados en ventanas no solapadas: las ventanas
  anteriores al período en que se calibraron los parámetros son fuera de
  muestra de verdad.
- Intervalo de confianza (bootstrap) del resultado medio por operación y
  comparación contra alternativas pasivas (BTC, canasta, Earn).

⚠️ Necesita conexión a Binance la primera vez (después usa el cache en
data/ohlcv_cache/ y solo baja las velas nuevas).

Uso:
    python -m lab.portafolio
    python -m lab.portafolio --days 1095 --ventana-dias 180
    python -m lab.portafolio --comision-pct 0.075 --slippage-pct 0.05
    python -m lab.portafolio --sin-salida-senal
"""
import argparse
import logging
import math
import os

import ccxt
import numpy as np
import pandas as pd

from lab.datos import descargar_ohlcv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Misma lista que el escaneo del bot en vivo
SYMBOLS_SCANNER = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "XRP/USDT",
                   "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "NEAR/USDT"]

CACHE_DIR = os.path.join("data", "ohlcv_cache")
SALIDA_DIR = "resultados_backtest"
DIAS_CALENTAMIENTO = 30  # velas extra antes del inicio para que EMA200/ADX estén estables


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

def _ahora_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def _quitar_vela_abierta(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Binance devuelve la vela en curso como última fila — no está cerrada,
    su close/volumen todavía cambian. Nunca se usa para señales."""
    if df.empty:
        return df
    duracion = pd.Timedelta(seconds=ccxt.Exchange.parse_timeframe(timeframe))
    return df[df.index + duracion <= _ahora_utc()]


def cargar_ohlcv_cacheado(exchange, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    ruta = os.path.join(CACHE_DIR, f"{symbol.replace('/', '_')}_{timeframe}.csv")
    inicio_requerido = _ahora_utc() - pd.Timedelta(days=days)

    df = None
    if os.path.exists(ruta):
        df = pd.read_csv(ruta, index_col="timestamp", parse_dates=["timestamp"])
        if df.empty or df.index.min() > inicio_requerido + pd.Timedelta(days=2):
            df = None  # el cache no cubre el rango pedido -> bajar todo de nuevo

    if df is None:
        df = descargar_ohlcv(exchange, symbol, timeframe, days)
    else:
        dias_faltantes = (_ahora_utc() - df.index.max()).total_seconds() / 86400
        nuevo = descargar_ohlcv(exchange, symbol, timeframe, max(1, math.ceil(dias_faltantes) + 1))
        df = pd.concat([df, nuevo])
        df = df[~df.index.duplicated(keep="last")].sort_index()

    df = _quitar_vela_abierta(df, timeframe)
    df.to_csv(ruta)
    return df[df.index >= inicio_requerido]


# ---------------------------------------------------------------------------
# Señales: réplica vectorizada de la lógica del bot en vivo
# ---------------------------------------------------------------------------

def calcular_senales(df: pd.DataFrame, p) -> pd.DataFrame:
    """Devuelve df con columnas 'compra' y 'venta' (bool) evaluadas al CIERRE
    de cada vela, con exactamente los mismos indicadores y umbrales que
    el escaneo del bot en vivo."""
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + gain / loss))

    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_hist = macd_line - macd_line.ewm(span=9, adjust=False).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    prev_close = close.shift()
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    alpha = 1 / 14
    tr_s = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s)
    minus_di = 100 * (minus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    vol_sma20 = vol.rolling(window=20).mean()

    # Toque de EMA21 en las N velas anteriores (sin contar la actual); la
    # proximidad se mide con la EMA21 ACTUAL, igual que en el scanner.
    proximity = ema21 * (p.ema21_proximity_pct / 100)
    toco_abajo = pd.Series(False, index=df.index)
    toco_arriba = pd.Series(False, index=df.index)
    for k in range(1, p.pullback_max_velas + 1):
        low_k, high_k, ema21_k = low.shift(k), high.shift(k), ema21.shift(k)
        toco_abajo |= ((low_k - ema21_k).abs() <= proximity) | (low_k <= ema21_k)
        toco_arriba |= ((high_k - ema21_k).abs() <= proximity) | (high_k >= ema21_k)

    vol_pullback = vol.shift(1).rolling(p.pullback_max_velas).mean()
    volumen_ok = (vol_pullback > 0) & (vol >= vol_pullback * p.min_vol_rebote_ratio) & (vol >= vol_sma20 * 0.9)

    tendencia_alcista = (ema9 > ema21) & (ema21 > ema50) & (close > ema200) & (adx >= p.adx_minimo)
    tendencia_bajista = (ema9 < ema21) & (ema21 < ema50) & (close < ema200) & (adx >= p.adx_minimo)

    out = df.copy()
    out["compra"] = (tendencia_alcista & toco_abajo & (close > ema21)
                     & (rsi >= p.rsi_min_rebote) & (rsi <= p.rsi_max_rebote)
                     & volumen_ok & (macd_hist > 0))
    out["venta"] = (tendencia_bajista & toco_arriba & (close < ema21)
                    & (rsi >= 100 - p.rsi_max_rebote) & (rsi <= 100 - p.rsi_min_rebote)
                    & volumen_ok & (macd_hist < 0))
    return out


# ---------------------------------------------------------------------------
# Simulación de portafolio
# ---------------------------------------------------------------------------

def simular(datos: dict, inicio: pd.Timestamp, p):
    symbols = list(datos.keys())
    indice = sorted(set().union(*[d.index for d in datos.values()]))
    indice = pd.DatetimeIndex([t for t in indice if t >= inicio])

    arr = {}
    for s, d in datos.items():
        r = d.reindex(indice)
        arr[s] = {
            "o": r["Open"].to_numpy(), "h": r["High"].to_numpy(),
            "l": r["Low"].to_numpy(), "c": r["Close"].to_numpy(),
            "compra": r["compra"].fillna(False).to_numpy(dtype=bool),
            "venta": r["venta"].fillna(False).to_numpy(dtype=bool),
        }

    fee = p.comision_pct / 100
    slip = p.slippage_pct / 100
    cash = p.capital
    posiciones = {}
    pend_entrada = {}
    pend_salida = set()
    cooldown_hasta = {}
    trades = []
    equity_curve = np.empty(len(indice))
    dia_actual, equity_inicio_dia, equity_prev = None, p.capital, p.capital

    def cerrar(sym, precio, ts, motivo):
        nonlocal cash
        pos = posiciones.pop(sym)
        bruto = pos["qty"] * precio
        comision = bruto * fee
        cash += bruto - comision
        pnl = bruto - comision - pos["costo_total"]
        trades.append({
            "symbol": sym, "entrada_ts": pos["ts"], "salida_ts": ts, "motivo": motivo,
            "precio_entrada": pos["precio"], "precio_salida": precio,
            "costo_usdt": pos["costo_total"], "pnl_usdt": pnl,
            "pnl_pct": pnl / pos["costo_total"] * 100,
            "horas": (ts - pos["ts"]).total_seconds() / 3600,
        })
        if motivo == "SL":
            cooldown_hasta[sym] = ts + pd.Timedelta(hours=p.cooldown_horas)

    for i, ts in enumerate(indice):
        # 1) Órdenes a mercado pendientes, a la apertura de esta vela
        for s in symbols:
            o = arr[s]["o"][i]
            if np.isnan(o):
                continue
            if s in pend_salida:
                pend_salida.discard(s)
                if s in posiciones:
                    cerrar(s, o * (1 - slip), ts, "SENAL")
            if s in pend_entrada:
                notional = min(pend_entrada.pop(s), cash / (1 + fee))
                if notional > p.minimo_orden:
                    precio = o * (1 + slip)
                    comision = notional * fee
                    cash -= notional + comision
                    posiciones[s] = {
                        "qty": notional / precio, "precio": precio, "ts": ts,
                        "costo_total": notional + comision, "ultimo": precio,
                        "tp": precio * (1 + p.tp_pct / 100), "sl": precio * (1 - p.sl_pct / 100),
                    }

        # 2) OCO dentro de la vela (SL primero si se tocan ambos)
        for s in list(posiciones):
            h, l, o = arr[s]["h"][i], arr[s]["l"][i], arr[s]["o"][i]
            if np.isnan(h):
                continue
            pos = posiciones[s]
            if l <= pos["sl"]:
                cerrar(s, min(o, pos["sl"]) * (1 - slip), ts, "SL")
            elif h >= pos["tp"]:
                cerrar(s, pos["tp"], ts, "TP")  # orden límite: se asume fill exacto en el TP

        # 3) Valor del portafolio al cierre
        valor_pos = 0.0
        for s, pos in posiciones.items():
            c = arr[s]["c"][i]
            if not np.isnan(c):
                pos["ultimo"] = c
            valor_pos += pos["qty"] * pos["ultimo"]
        equity = cash + valor_pos
        equity_curve[i] = equity

        if ts.date() != dia_actual:
            dia_actual = ts.date()
            equity_inicio_dia = equity_prev
        limite_diario = equity <= equity_inicio_dia * (1 - p.max_perdida_diaria_pct / 100)
        equity_prev = equity

        # 4) Señales al cierre -> órdenes para la apertura siguiente
        ocupadas = len(posiciones) + len(pend_entrada)
        for s in symbols:
            if np.isnan(arr[s]["c"][i]):
                continue
            if s in posiciones:
                if p.salida_senal and arr[s]["venta"][i]:
                    pend_salida.add(s)
                continue
            if (arr[s]["compra"][i] and not limite_diario and ocupadas < p.max_posiciones
                    and ts >= cooldown_hasta.get(s, pd.Timestamp.min)):
                pend_entrada[s] = equity * p.position_size
                ocupadas += 1

    ts_final = indice[-1]
    for s in list(posiciones):
        cerrar(s, posiciones[s]["ultimo"], ts_final, "FIN")

    return pd.Series(equity_curve, index=indice), pd.DataFrame(trades), arr, indice


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def bootstrap_media(x: np.ndarray, n: int = 5000, seed: int = 42):
    if len(x) < 2:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    medias = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return (np.percentile(medias, 2.5), np.percentile(medias, 97.5), float((medias <= 0).mean()))


def metricas(equity: pd.Series, trades: pd.DataFrame, earn_apr_pct: float) -> dict:
    ret = equity.iloc[-1] / equity.iloc[0] - 1
    dias = (equity.index[-1] - equity.index[0]).total_seconds() / 86400
    diario = equity.resample("1D").last().pct_change().dropna()
    sharpe = diario.mean() / diario.std() * math.sqrt(365) if len(diario) > 1 and diario.std() > 0 else np.nan

    m = {
        "desde": equity.index[0].date(), "hasta": equity.index[-1].date(), "dias": round(dias),
        "retorno_pct": ret * 100,
        "anualizado_pct": ((1 + ret) ** (365 / dias) - 1) * 100 if dias > 0 and ret > -1 else np.nan,
        "max_dd_pct": (equity / equity.cummax() - 1).min() * 100,
        "sharpe": sharpe,
        "earn_pct": ((1 + earn_apr_pct / 100) ** (dias / 365) - 1) * 100,
        "trades": len(trades),
    }
    if len(trades):
        pnl = trades["pnl_pct"].to_numpy()
        ganancias = trades.loc[trades["pnl_usdt"] > 0, "pnl_usdt"].sum()
        perdidas = -trades.loc[trades["pnl_usdt"] < 0, "pnl_usdt"].sum()
        lo, hi, p_neg = bootstrap_media(pnl)
        m.update({
            "win_rate_pct": (pnl > 0).mean() * 100,
            "media_trade_pct": pnl.mean(),
            "ic95_bajo": lo, "ic95_alto": hi,
            "prob_media_<=0": p_neg,
            "profit_factor": ganancias / perdidas if perdidas > 0 else np.nan,
        })
    return m


def benchmarks(arr: dict, indice: pd.DatetimeIndex, desde: int, hasta: int) -> dict:
    """Retorno de comprar y mantener entre las velas desde..hasta."""
    def ret_symbol(s):
        c = pd.Series(arr[s]["c"][desde:hasta + 1]).dropna()
        return (c.iloc[-1] / c.iloc[0] - 1) * 100 if len(c) > 1 else np.nan

    rets = {s: ret_symbol(s) for s in arr}
    return {
        "btc_hold_pct": rets.get("BTC/USDT", np.nan),
        "canasta_hold_pct": np.nanmean(list(rets.values())),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _fmt(df: pd.DataFrame) -> str:
    return df.to_string(index=False, float_format=lambda v: f"{v:,.2f}")


def main():
    parser = argparse.ArgumentParser(description="Backtest de portafolio por ventanas — réplica del scanner en vivo")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS_SCANNER)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=1095, help="Días de historia a simular (sin contar calentamiento)")
    parser.add_argument("--ventana-dias", type=int, default=180)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--comision-pct", type=float, default=0.1, help="Por lado. 0.075 con descuento BNB")
    parser.add_argument("--slippage-pct", type=float, default=0.05, help="Por orden a mercado / stop")
    parser.add_argument("--earn-apr-pct", type=float, default=3.0, help="APR supuesto de Earn Flexible USDT (benchmark)")
    # Parámetros de la estrategia — defaults = config validada en producción (ver README)
    parser.add_argument("--tp-pct", type=float, default=3.0)
    parser.add_argument("--sl-pct", type=float, default=1.0)
    parser.add_argument("--position-size", type=float, default=0.20)
    parser.add_argument("--max-posiciones", type=int, default=3)
    parser.add_argument("--max-perdida-diaria-pct", type=float, default=3.0)
    parser.add_argument("--adx-minimo", type=float, default=30)
    parser.add_argument("--min-vol-rebote-ratio", type=float, default=1.05)
    parser.add_argument("--ema21-proximity-pct", type=float, default=0.3)
    parser.add_argument("--rsi-min-rebote", type=float, default=45)
    parser.add_argument("--rsi-max-rebote", type=float, default=62)
    parser.add_argument("--pullback-max-velas", type=int, default=5)
    parser.add_argument("--cooldown-horas", type=float, default=72)
    parser.add_argument("--minimo-orden", type=float, default=5.0)
    parser.add_argument("--sin-salida-senal", dest="salida_senal", action="store_false",
                        help="Salir solo por OCO (TP/SL), sin la señal de venta del scanner")
    parser.add_argument("--etiqueta", default="base", help="Sufijo para los CSV de salida")
    p = parser.parse_args()

    exchange = ccxt.binance({"options": {"defaultType": "spot"}})
    inicio = _ahora_utc() - pd.Timedelta(days=p.days)

    datos = {}
    for s in p.symbols:
        logger.info(f"📥 {s}: cargando {p.days + DIAS_CALENTAMIENTO} días de {p.timeframe}...")
        try:
            df = cargar_ohlcv_cacheado(exchange, s, p.timeframe, p.days + DIAS_CALENTAMIENTO)
        except Exception as e:
            logger.error(f"❌ {s}: {e}")
            continue
        if len(df) < 300:
            logger.warning(f"⚠️ {s}: muy pocas velas ({len(df)}), se salta.")
            continue
        datos[s] = calcular_senales(df, p)

    if not datos:
        logger.error("❌ Sin datos.")
        return

    equity, trades, arr, indice = simular(datos, inicio, p)

    os.makedirs(SALIDA_DIR, exist_ok=True)
    trades.to_csv(os.path.join(SALIDA_DIR, f"portafolio_trades_{p.etiqueta}.csv"), index=False)
    equity.rename("equity").to_csv(os.path.join(SALIDA_DIR, f"portafolio_equity_{p.etiqueta}.csv"))

    # --- Ventanas no solapadas ---
    filas = []
    t0 = indice[0]
    while t0 < indice[-1]:
        t1 = t0 + pd.Timedelta(days=p.ventana_dias)
        mask = (indice >= t0) & (indice < t1)
        pos = np.flatnonzero(mask)
        if len(pos) >= 24 * 30:
            desde, hasta = pos[0], pos[-1]
            base = max(desde - 1, 0)
            eq = equity.iloc[base:hasta + 1]
            tr = trades[(trades["salida_ts"] >= t0) & (trades["salida_ts"] < t1)] if len(trades) else trades
            fila = metricas(eq, tr, p.earn_apr_pct)
            fila.update(benchmarks(arr, indice, base, hasta))
            filas.append(fila)
        t0 = t1

    total = metricas(equity, trades, p.earn_apr_pct)
    total.update(benchmarks(arr, indice, 0, len(indice) - 1))

    cols = ["desde", "hasta", "trades", "retorno_pct", "max_dd_pct", "win_rate_pct", "media_trade_pct",
            "btc_hold_pct", "canasta_hold_pct", "earn_pct"]
    df_ventanas = pd.DataFrame(filas)
    df_ventanas.to_csv(os.path.join(SALIDA_DIR, f"portafolio_ventanas_{p.etiqueta}.csv"), index=False)

    print("\n" + "=" * 100)
    print(f"BACKTEST DE PORTAFOLIO [{p.etiqueta}] — {len(datos)} símbolos, capital {p.capital:,.0f} USDT, "
          f"comisión {p.comision_pct}%/lado, slippage {p.slippage_pct}%")
    print(f"TP {p.tp_pct}% / SL {p.sl_pct}% | {p.position_size*100:.0f}% por posición, máx {p.max_posiciones} | "
          f"salida por señal: {'sí' if p.salida_senal else 'no'}")
    print("=" * 100)
    print("\nPOR VENTANA (cada una es independiente — las previas a la calibración son fuera de muestra):")
    print(_fmt(df_ventanas.reindex(columns=cols)))

    print("\nTOTAL:")
    for k, v in total.items():
        print(f"  {k:>18}: {v:,.3f}" if isinstance(v, (float, np.floating)) else f"  {k:>18}: {v}")

    if len(trades):
        print("\nPOR SÍMBOLO:")
        por_symbol = trades.groupby("symbol").agg(
            trades=("pnl_pct", "size"), win_rate_pct=("pnl_pct", lambda x: (x > 0).mean() * 100),
            media_trade_pct=("pnl_pct", "mean"), pnl_usdt=("pnl_usdt", "sum")).reset_index()
        print(_fmt(por_symbol))
        print("\nPOR MOTIVO DE SALIDA:")
        print(_fmt(trades.groupby("motivo").agg(trades=("pnl_pct", "size"), media_trade_pct=("pnl_pct", "mean")).reset_index()))

    print("\nCómo leerlo:")
    print("  - 'ic95_bajo' > 0 en el TOTAL = hay evidencia de ventaja real por operación. Si el intervalo")
    print("    cruza el 0, el resultado no se distingue del azar.")
    print("  - Una estrategia que sirve debería ser positiva en la mayoría de las ventanas, no solo en la última.")
    print("  - Si no le gana a 'earn_pct' con menos drawdown, no compensa el riesgo operativo.")


if __name__ == "__main__":
    main()
