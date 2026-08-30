import streamlit as st
import json
import math
import threading
import time
import requests
import websocket
import datetime
import calendar
import pandas as pd

# ============================================================
# KONFIGURASI
# ============================================================
st.set_page_config(page_title="ZF-CORE Dashboard", layout="wide")

PERIOD_PPURE = 20
TIMEFRAMES = ["1H", "4H", "1D", "1W", "1M"]
# Timeframe yang di-align OKX ke UTC+8 (HK time) secara default dan butuh
# suffix "utc" agar align ke UTC (biar konsisten dengan get_candle_countdown).
UTC_ALIGNED_TF = {"1D", "1W", "1M"}
PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "SUI-USDT", "SHIB-USDT",
]

# Konstanta skala untuk dRes (persen) sebelum masuk tanh().
# dRes dihitung dalam persen (mis. 9.9 = 9.9%). tanh(9.9) sudah ~1.0 sejak
# x > ~3, jadi ZF langsung mentok "SHORT" walau deviasi masih wajar.
# Membaginya dengan skala ini membuat ZF merespons secara gradual.
DRES_SCALE = 5.0  # x% deviasi -> tanh(x/DRES_SCALE); sesuaikan sesuai kalibrasi

# ============================================================
# FUNGSI COUNTDOWN (SISA WAKTU CANDLE)
# ============================================================
def get_candle_countdown(tf):
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        if tf == "1H":
            next_close = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        elif tf == "4H":
            next_close = (now + datetime.timedelta(hours=4 - now.hour % 4)).replace(minute=0, second=0, microsecond=0)
        elif tf == "1D":
            next_close = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif tf == "1W":
            days_ahead = 7 - now.weekday()
            next_close = (now + datetime.timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif tf == "1M":
            _, last_day = calendar.monthrange(now.year, now.month)
            next_close = (now.replace(day=1) + datetime.timedelta(days=last_day)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            return "-"

        diff = next_close - now
        total_seconds = int(diff.total_seconds())
        days, hours = total_seconds // 86400, (total_seconds % 86400) // 3600
        minutes, seconds = (total_seconds % 3600) // 60, total_seconds % 60

        if days > 0: return f"{days}d {hours}h"
        elif hours > 0: return f"{hours}h {minutes}m"
        else: return f"{minutes}m {seconds}s"
    except Exception:
        return "N/A"

# ============================================================
# INISIALISASI & BACKGROUND TASKS (CACHE AGAR TIDAK RESTART)
# ============================================================
@st.cache_resource
def init_system():
    state = {
        "candle_data": {symbol: {tf: {} for tf in TIMEFRAMES} for symbol in PAIRS},
        "ws_status": "Menghubungkan...",
        "last_message_time": time.time(),
        "last_error": None,
        "data_lock": threading.Lock()
    }

    def _bar_param(tf):
        # 1D/1W/1M perlu suffix "utc" agar align dengan UTC, sama seperti
        # perhitungan countdown di get_candle_countdown(). Tanpa ini, OKX
        # menutup candle tersebut di 00:00 waktu HK (UTC+8), beda 8 jam
        # dari yang ditampilkan di UI.
        return f"{tf}utc" if tf in UTC_ALIGNED_TF else tf

    def _try_fetch_candles(symbol, tf, endpoint):
        bar = _bar_param(tf)
        url = f"https://www.okx.com/api/v5/{endpoint}?instId={symbol}&bar={bar}&limit={PERIOD_PPURE + 5}"
        try:
            res = requests.get(url, timeout=10).json()
            if res.get("code") == "0":
                raw = res["data"]
                raw.reverse()
                return raw
            else:
                state["last_error"] = f"{symbol} {tf}: API code={res.get('code')} msg={res.get('msg')}"
        except Exception as e:
            state["last_error"] = f"{symbol} {tf}: {e}"
        return []

    def fetch_initial_candles():
        for symbol in PAIRS:
            for tf in TIMEFRAMES:
                candles_fetched = _try_fetch_candles(symbol, tf, "market/candles")
                if len(candles_fetched) < PERIOD_PPURE:
                    candles_fetched = _try_fetch_candles(symbol, tf, "market/history-candles")

                if candles_fetched:
                    with state["data_lock"]:
                        state["candle_data"][symbol][tf] = {
                            "opens": [float(item[1]) for item in candles_fetched],
                            "closes": [float(item[4]) for item in candles_fetched],
                            "volumes": [float(item[5]) for item in candles_fetched],
                            "last_ts": int(candles_fetched[-1][0]),
                        }
                time.sleep(0.1)

    def on_message(ws, message):
        if message == "pong": return
        try:
            data = json.loads(message)
            if "data" in data and "arg" in data:
                symbol, channel = data["arg"]["instId"], data["arg"]["channel"]
                # Channel WS untuk timeframe UTC-aligned datang sebagai
                # "candle1Dutc" dsb -- strip "utc" agar key kembali cocok
                # dengan TIMEFRAMES ("1D", bukan "1Dutc").
                tf = channel.replace("candle", "")
                if tf.endswith("utc"):
                    tf = tf[:-3]
                c_info = data["data"][0]
                ts, open_p, close_p, vol = int(c_info[0]), float(c_info[1]), float(c_info[4]), float(c_info[5])

                with state["data_lock"]:
                    s_data = state["candle_data"][symbol].get(tf, {})
                    if "last_ts" in s_data and ts > s_data["last_ts"]:
                        s_data["opens"].append(open_p); s_data["closes"].append(close_p); s_data["volumes"].append(vol); s_data["last_ts"] = ts
                        if len(s_data["closes"]) > PERIOD_PPURE * 2:
                            s_data["opens"].pop(0); s_data["closes"].pop(0); s_data["volumes"].pop(0)
                    elif "closes" in s_data and len(s_data["closes"]) > 0:
                        s_data["opens"][-1] = open_p; s_data["closes"][-1] = close_p; s_data["volumes"][-1] = vol
                    else:
                        s_data["opens"] = [open_p]; s_data["closes"] = [close_p]; s_data["volumes"] = [vol]; s_data["last_ts"] = ts

                state["last_message_time"] = time.time()
        except Exception as e:
            state["last_error"] = f"on_message: {e}"

    def on_error(ws, error):
        state["ws_status"] = "Error koneksi..."
        state["last_error"] = f"ws error: {error}"

    def on_close(ws, code, msg): state["ws_status"] = "Terputus..."
    def on_open(ws):
        state["ws_status"] = "Online (Real-time)"
        args = [{"channel": f"candle{_bar_param(tf)}", "instId": symbol} for symbol in PAIRS for tf in TIMEFRAMES]
        ws.send(json.dumps({"op": "subscribe", "args": args}))

    def run_ws():
        while True:
            ws = websocket.WebSocketApp("wss://ws.okx.com:8443/ws/v5/business",
                                        on_open=on_open, on_message=on_message,
                                        on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
            time.sleep(3)

    fetch_initial_candles()
    threading.Thread(target=run_ws, daemon=True).start()
    return state

system_state = init_system()

# ============================================================
# PERHITUNGAN ZF
# ============================================================
def calculate_zf(closes, volumes):
    if len(closes) < PERIOD_PPURE:
        return 0.0, 0.0, "LOADING"

    pMarket, vNow = closes[-1], volumes[-1]
    pPure = sum(closes[-PERIOD_PPURE:]) / PERIOD_PPURE
    vAvg = sum(volumes[-PERIOD_PPURE:]) / PERIOD_PPURE

    dRes = abs(pMarket - pPure) / pPure * 100.0 if pPure > 0 else 0.0
    # Dibandingkan terhadap rata-rata volume (vAvg), bukan volume sekarang
    # (vNow) -- membagi dengan vNow membuat rasio meledak ke 1.0 saat
    # volume sekarang kecil, walau penyimpangan sebenarnya kecil.
    volRatio = min(abs(vNow - vAvg) / vAvg, 1.0) if vAvg > 0 else 0.5
    # dRes diskalakan (DRES_SCALE) sebelum tanh() agar ZF merespons secara
    # gradual, bukan langsung saturasi ke 1.0 begitu deviasi > ~3%.
    zf = min(volRatio * math.tanh(dRes / DRES_SCALE), 1.0)

    if zf > 0.8: return zf, dRes, "🔴 SHORT"
    elif zf <= 0.45 and dRes < 0.4: return zf, dRes, "🟢 BUY"
    else: return zf, dRes, "🟡 WAIT"

# ============================================================
# UI STREAMLIT DASHBOARD
# ============================================================
st.title("🤖 ZF-CORE V16.6 Dashboard")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("1H Candle", get_candle_countdown("1H"))
col2.metric("4H Candle", get_candle_countdown("4H"))
col3.metric("1D Candle", get_candle_countdown("1D"))
col4.metric("1W Candle", get_candle_countdown("1W"))
col5.metric("1M Candle", get_candle_countdown("1M"))

st.divider()

table_data = []
with system_state["data_lock"]:
    for symbol in PAIRS:
        row = {"Pair": symbol.replace("-USDT", "")}
        current_price = 0.0

        for tf in TIMEFRAMES:
            s_data = system_state["candle_data"][symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) > 0:
                current_price = s_data["closes"][-1]

        row["Price"] = f"${current_price:,.4f}"

        for tf in TIMEFRAMES:
            s_data = system_state["candle_data"][symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) >= PERIOD_PPURE:
                zf, dRes, status = calculate_zf(s_data["closes"], s_data["volumes"])
                row[tf] = f"{status} (ZF: {zf:.2f} | dR: {dRes:.1f}%)"
            else:
                row[tf] = "Loading..."
        table_data.append(row)

df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

since_last = int(time.time() - system_state["last_message_time"])
status_text = f"📡 WebSocket: {system_state['ws_status']} | Terakhir update: {since_last} detik yang lalu"
if since_last > 30:
    st.error(status_text + " (Data mungkin tertinggal!)")
else:
    st.success(status_text)

if system_state.get("last_error"):
    with st.expander("⚠️ Error terakhir (debug)"):
        st.code(system_state["last_error"])

time.sleep(2)
st.rerun()
