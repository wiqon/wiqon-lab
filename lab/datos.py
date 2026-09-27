"""Descarga de velas históricas desde exchanges vía ccxt (paginada)."""
import pandas as pd


def descargar_ohlcv(exchange, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Descarga velas paginando hasta llegar al presente. Devuelve un
    DataFrame con columnas Open/High/Low/Close/Volume e índice datetime (UTC).

    El corte es 'llegamos al presente' y no 'el lote vino incompleto': algunos
    exchanges (p. ej. Bybit) devuelven menos velas que el límite en TODAS las
    páginas. Salvaguarda extra si el exchange no avanza (evita loop infinito)."""
    limit_por_llamada = 1000
    ms_por_vela = exchange.parse_timeframe(timeframe) * 1000
    ahora = exchange.milliseconds()
    desde = ahora - days * 24 * 60 * 60 * 1000

    velas = []
    ultimo_timestamp_visto = None
    while desde < ahora:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=desde, limit=limit_por_llamada)
        if not batch:
            break
        if ultimo_timestamp_visto is not None and batch[-1][0] <= ultimo_timestamp_visto:
            break
        velas += batch
        ultimo_timestamp_visto = batch[-1][0]
        desde = batch[-1][0] + ms_por_vela

    if not velas:
        return pd.DataFrame()

    df = pd.DataFrame(velas, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.drop_duplicates(subset='timestamp', inplace=True)
    df.set_index('timestamp', inplace=True)
    return df
