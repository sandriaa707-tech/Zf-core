import streamlit as st
import math
import threading
import time
import json
import datetime
import collections
import requests
import pandas as pd
import websocket  # pip install websocket-client

# ============================================================
# KONFIGURASI
# ============================================================
st.set_page_config(page_title="ZF-CORE Omni Dashboard", layout="wide")

PERIOD_PPURE = 20
TIMEFRAME_SECONDS = {
    "1H": 3600,
    "4H": 4 * 3600,
    "1D": 24 * 3600,
    "1W": 7 * 24 * 3600,
}
TIMEFRAMES = list(TIMEFRAME_SECONDS.keys())

# Indodax's /tradingview/history_v2 timeframe codes for each of our timeframes.
TF_API_CODE = {
    "1H": "60",
    "4H": "240",
    "1D": "1D",
    "1W": "1W",
}

PAIRS = [
    "btc_idr", "eth_idr", "usdt_idr", "dot_idr",
    "btc_usdt", "xaut_idr", "usdc_idr", "sol_idr",
    "bnb_idr", "trx_idr"
]

STALE_THRESHOLD = 30  # seconds before we flag the connection as stuck

# ---- Indodax Market Data WebSocket (official docs) ----
WS_URL = "wss://ws3.indodax.com/ws/"
WS_STATIC_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJleHAiOjE5NDY2MTg0MTV9."
    "UR1lBM6Eqh0yWz-PVirw1uPCxe60FdchR8eNVdsskeo"
)
SUMMARY_CHANNEL = "market:summary-24h"

# ---- Indodax OHLC history REST endpoint (used only to seed startup data) ----
HISTORY_URL = "https://indodax.com/tradingview/history_v2"


# ============================================================
# HELPERS
# ============================================================
def bucket_start(now_ts: float, tf: str) -> int:
    """Return the epoch timestamp (UTC) marking the start of the candle
    that `now_ts` currently falls into, aligned to UTC boundaries."""
    span = TIMEFRAME_SECONDS[tf]
    if tf == "1W":
        now_dt = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc)
        monday = (now_dt - datetime.timedelta(days=now_dt.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(monday.timestamp())
    return int(now_ts // span) * span


def get_candle_countdown(tf: str) -> str:
    now = time.time()
    span = TIMEFRAME_SECONDS[tf]
    start = bucket_start(now, tf)
    remaining = max(0, int(start + span - now))
    days, rem = divmod(remaining, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m {seconds}s"


def symbol_to_pair(symbol: str) -> str:
    """'btc_idr' -> 'btcidr' (Indodax WS channel/pair format)."""
    return symbol.replace("_", "")


def symbol_to_api_symbol(symbol: str) -> str:
    """'btc_idr' -> 'BTCIDR' (Indodax /tradingview/history_v2 symbol format)."""
    return symbol.replace("_", "").upper()


PAIR_TO_SYMBOL = {symbol_to_pair(s): s for s in PAIRS}


def fetch_history(symbol: str, tf: str, limit: int = PERIOD_PPURE):
    """Fetch the last `limit` real OHLC candles for symbol/tf from Indodax's
    official history endpoint, returned oldest -> newest as candle dicts."""
    span = TIMEFRAME_SECONDS[tf]
    now = int(time.time())
    # Ask for a bit more than we need to absorb gaps / holes in the data.
    from_ts = now - span * (limit + 5)

    params = {
        "symbol": symbol_to_api_symbol(symbol),
        "tf": TF_API_CODE[tf],
        "from": from_ts,
        "to": now,
    }
    try:
        resp = requests.get(HISTORY_URL, params=params, timeout=6)
        data = resp.json()
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    by_bucket = {}
    for item in data:
        try:
            ts = bucket_start(float(item["Time"]), tf)
            candle = {
                "ts": ts,
                "open": float(item["Open"]),
                "high": float(item["High"]),
                "low": float(item["Low"]),
                "close": float(item["Close"]),
                "volume": float(item["Volume"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        by_bucket[ts] = candle  # de-dupe by bucket, keep the latest entry

    ordered = [by_bucket[k] for k in sorted(by_bucket.keys())]
    return ordered[-limit:]


# ============================================================
# INISIALISASI: SEED HISTORIS + WEBSOCKET CLIENT
# ============================================================
@st.cache_resource
def init_system():
    state = {
        # candle_data[symbol][tf] -> deque of dicts: {ts, open, high, low, close, volume}
        "candle_data": {
            symbol: {tf: collections.deque(maxlen=PERIOD_PPURE) for tf in TIMEFRAMES}
            for symbol in PAIRS
        },
        "connection_status": "Memuat data historis...",
        "last_update_time": time.time(),
        "data_lock": threading.Lock(),
        "ws_app": None,
    }

    # --------------------------------------------------------
    # 1) Seed each symbol/timeframe with REAL historical candles
    #    so the table doesn't need to wait hours/weeks for the
    #    buffer to fill purely from live ticks.
    # --------------------------------------------------------
    def seed_historical():
        for symbol in PAIRS:
            for tf in TIMEFRAMES:
                candles = fetch_history(symbol, tf, PERIOD_PPURE)
                if candles:
                    with state["data_lock"]:
                        dq = state["candle_data"][symbol][tf]
                        dq.clear()
                        dq.extend(candles)
        with state["data_lock"]:
            state["connection_status"] = "Menghubungkan WebSocket..."

    seed_historical()  # runs once, synchronously, before the page first renders

    # --------------------------------------------------------
    # 2) Live updates via WebSocket keep the last candle of each
    #    timeframe moving, and roll a new candle whenever the
    #    bucket changes — same logic as the historical seed uses.
    # --------------------------------------------------------
    def apply_tick(symbol: str, price: float, volume: float):
        now = time.time()
        with state["data_lock"]:
            for tf in TIMEFRAMES:
                dq = state["candle_data"][symbol][tf]
                start = bucket_start(now, tf)

                if dq and dq[-1]["ts"] == start:
                    c = dq[-1]
                    c["close"] = price
                    c["high"] = max(c["high"], price)
                    c["low"] = min(c["low"], price)
                    c["volume"] = volume
                else:
                    open_price = dq[-1]["close"] if dq else price
                    dq.append({
                        "ts": start,
                        "open": open_price,
                        "high": max(open_price, price),
                        "low": min(open_price, price),
                        "close": price,
                        "volume": volume,
                    })
            state["last_update_time"] = now
            state["connection_status"] = "Online (WebSocket Live)"

    def on_open(ws):
        auth_req = {"params": {"token": WS_STATIC_TOKEN}, "id": 1}
        ws.send(json.dumps(auth_req))

    def on_message(ws, message):
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return

        if msg.get("id") == 1 and "result" in msg:
            sub_req = {
                "method": 1,
                "params": {"channel": SUMMARY_CHANNEL},
                "id": 2,
            }
            ws.send(json.dumps(sub_req))
            return

        result = msg.get("result", {})
        if result.get("channel") != SUMMARY_CHANNEL:
            return

        rows = result.get("data", {}).get("data", [])
        for row in rows:
            # [pair, epoch, last, low24h, high24h, price24hAgo, idr_volume24h, base_volume24h]
            try:
                pair, _epoch, last_price = row[0], row[1], row[2]
                base_volume = row[7]
            except (IndexError, TypeError):
                continue

            symbol = PAIR_TO_SYMBOL.get(pair)
            if symbol is None:
                continue

            try:
                price = float(last_price)
                vol = float(base_volume)
            except (TypeError, ValueError):
                continue

            apply_tick(symbol, price, vol)

    def on_error(ws, error):
        with state["data_lock"]:
            state["connection_status"] = f"Error: {error}"

    def on_close(ws, close_status_code, close_msg):
        with state["data_lock"]:
            state["connection_status"] = "Terputus, menghubungkan ulang..."
        time.sleep(2)
        start_websocket()

    def start_websocket():
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        state["ws_app"] = ws
        threading.Thread(
            target=lambda: ws.run_forever(ping_interval=20, ping_timeout=10),
            daemon=True,
        ).start()

    start_websocket()
    return state


system_state = init_system()


# ============================================================
# PERHITUNGAN ZF (ZUHRI FORMALISM)
# ============================================================
def calculate_zf(closes, volumes):
    if len(closes) < PERIOD_PPURE:
        return 0.0, 0.0, "LOADING"

    pMarket, vNow = closes[-1], volumes[-1]
    pPure = sum(closes[-PERIOD_PPURE:]) / PERIOD_PPURE
    vAvg = sum(volumes[-PERIOD_PPURE:]) / PERIOD_PPURE

    dRes = abs(pMarket - pPure) / pPure * 100.0 if pPure > 0 else 0.0
    volRatio = min(abs(vNow - vAvg) / vNow, 1.0) if vNow > 0 else 0.5
    zf = min(volRatio * math.tanh(dRes), 1.0)

    if zf > 0.8:
        return zf, dRes, "🔴 SHORT"
    elif zf <= 0.45 and dRes < 0.4:
        return zf, dRes, "🟢 BUY"
    else:
        return zf, dRes, "🟡 WAIT"


# ============================================================
# UI STREAMLIT DASHBOARD
# ============================================================
st.title("🤖 ZF-CORE V16.6 Omni Dashboard (WebSocket)")

col1, col2, col3, col4 = st.columns(4)
col1.metric("1H Candle", get_candle_countdown("1H"))
col2.metric("4H Candle", get_candle_countdown("4H"))
col3.metric("1D Candle", get_candle_countdown("1D"))
col4.metric("1W Candle", get_candle_countdown("1W"))

st.divider()

table_data = []
with system_state["data_lock"]:
    for symbol in PAIRS:
        display_name = symbol.replace("_", "/").upper()
        is_usdt_pair = symbol.endswith("usdt")

        row = {"Pair": display_name}
        current_price = 0.0

        for tf in TIMEFRAMES:
            dq = system_state["candle_data"][symbol][tf]
            if dq:
                current_price = dq[-1]["close"]
                break

        row["Price"] = f"${current_price:,.2f}" if is_usdt_pair else f"Rp {current_price:,.0f}"

        for tf in TIMEFRAMES:
            dq = system_state["candle_data"][symbol][tf]
            if len(dq) >= PERIOD_PPURE:
                closes = [c["close"] for c in dq]
                volumes = [c["volume"] for c in dq]
                zf, dRes, status = calculate_zf(closes, volumes)
                row[tf] = f"{status} (ZF: {zf:.2f} | dR: {dRes:.1f}%)"
            else:
                row[tf] = f"Loading... ({len(dq)}/{PERIOD_PPURE})"
        table_data.append(row)

df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

since_last = int(time.time() - system_state["last_update_time"])
status_text = f"📡 Status: {system_state['connection_status']} | Terakhir update: {since_last} detik yang lalu"
if since_last > STALE_THRESHOLD:
    st.error(status_text + " (Koneksi bermasalah!)")
else:
    st.success(status_text)

time.sleep(2)
st.rerun()
