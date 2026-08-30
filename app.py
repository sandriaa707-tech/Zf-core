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
PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "SUI-USDT", "SHIB-USDT",
]

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
    # Gunakan dictionary untuk menyimpan state yang bisa dibagikan antar thread
    state = {
        "candle_data": {symbol: {tf: {} for tf in TIMEFRAMES} for symbol in PAIRS},
        "ws_status": "Menghubungkan...",
        "last_message_time": time.time(),
        "data_lock": threading.Lock()
    }

    def _try_fetch_candles(symbol, tf, endpoint):
        # NOTE: Jika di Streamlit Cloud OKX diblokir, ganti URL ini dengan Proxy API
        url = f"https://www.okx.com/api/v5/{endpoint}?instId={symbol}&bar={tf}&limit={PERIOD_PPURE + 5}"
        try:
            res = requests.get(url, timeout=10).json()
            if res.get("code") == "0":
                raw = res["data"]
                raw.reverse()
                return raw
        except Exception:
            pass
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
                tf = channel.replace("candle", "")
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
        except Exception:
            pass

    def on_error(ws, error): state["ws_status"] = "Error koneksi..."
    def on_close(ws, code, msg): state["ws_status"] = "Terputus..."
    def on_open(ws):
        state["ws_status"] = "Online (Real-time)"
        args = [{"channel": f"candle{tf}", "instId": symbol} for symbol in PAIRS for tf in TIMEFRAMES]
        ws.send(json.dumps({"op": "subscribe", "args": args}))

    def run_ws():
        while True:
            ws = websocket.WebSocketApp("wss://ws.okx.com:8443/ws/v5/business",
                                        on_open=on_open, on_message=on_message,
                                        on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
            time.sleep(3)

    # Menjalankan fetch data dan websocket di background saat aplikasi pertama kali dibuka
    fetch_initial_candles()
    threading.Thread(target=run_ws, daemon=True).start()
    return state

# Jalankan inisialisasi
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
    volRatio = min(abs(vNow - vAvg) / vNow, 1.0) if vNow > 0 else 0.5
    zf = min(volRatio * math.tanh(dRes), 1.0)

    if zf > 0.8: return zf, dRes, "🔴 SHORT"
    elif zf <= 0.45 and dRes < 0.4: return zf, dRes, "🟢 BUY"
    else: return zf, dRes, "🟡 WAIT"

# ============================================================
# UI STREAMLIT DASHBOARD
# ============================================================
st.title("🤖 ZF-CORE V16.6 Dashboard")

# Header Countdown
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("1H Candle", get_candle_countdown("1H"))
col2.metric("4H Candle", get_candle_countdown("4H"))
col3.metric("1D Candle", get_candle_countdown("1D"))
col4.metric("1W Candle", get_candle_countdown("1W"))
col5.metric("1M Candle", get_candle_countdown("1M"))

st.divider()

# Membangun Data Tabel
table_data = []
with system_state["data_lock"]:
    for symbol in PAIRS:
        row = {"Pair": symbol.replace("-USDT", "")}
        current_price = 0.0
        
        # Cari harga terakhir
        for tf in TIMEFRAMES:
            s_data = system_state["candle_data"][symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) > 0:
                current_price = s_data["closes"][-1]
        
        row["Price"] = f"${current_price:,.4f}"

        # Hitung ZF setiap timeframe
        for tf in TIMEFRAMES:
            s_data = system_state["candle_data"][symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) >= PERIOD_PPURE:
                zf, dRes, status = calculate_zf(s_data["closes"], s_data["volumes"])
                row[tf] = f"{status} (ZF: {zf:.2f})"
            else:
                row[tf] = "Loading..."
        table_data.append(row)

# Tampilkan sebagai Dataframe/Tabel interaktif
df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

# Status Footer
since_last = int(time.time() - system_state["last_message_time"])
status_text = f"📡 WebSocket: {system_state['ws_status']} | Terakhir update: {since_last} detik yang lalu"
if since_last > 30:
    st.error(status_text + " (Data mungkin tertinggal!)")
else:
    st.success(status_text)

# Auto-Refresh halaman setiap 2 detik
time.sleep(2)
st.rerun()
