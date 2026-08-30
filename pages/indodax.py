import streamlit as st
import math
import threading
import time
import requests
import datetime
import calendar
import pandas as pd

# ============================================================
# KONFIGURASI 
# ============================================================
st.set_page_config(page_title="ZF-CORE Omni Dashboard", layout="wide")

PERIOD_PPURE = 20
TIMEFRAMES = ["1H", "4H", "1D", "1W"]

# Daftar pair disesuaikan dengan gambar Favorite Anda (format Indodax/Triv)
PAIRS = [
    "btc_idr", "eth_idr", "usdt_idr", "dot_idr",
    "btc_usdt", "xaut_idr", "usdc_idr", "sol_idr",
    "bnb_idr", "trx_idr"
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
# INISIALISASI & BACKGROUND POLLING TASKS (STABIL & ANTI-PUTUS)
# ============================================================
@st.cache_resource
def init_system():
    state = {
        "candle_data": {symbol: {tf: {} for tf in TIMEFRAMES} for symbol in PAIRS},
        "connection_status": "Online (Live Sync)",
        "last_update_time": time.time(),
        "data_lock": threading.Lock()
    }

    def background_sync():
        while True:
            for symbol in PAIRS:
                url = f"https://indodax.com/api/v2/ticker/{symbol}"
                try:
                    res = requests.get(url, timeout=5).json()
                    if "ticker" in res:
                        ticker = res["ticker"]
                        last_p = float(ticker["last"])
                        vol_p = float(ticker.get("vol_idr" if "idr" in symbol else "vol_usdt", 0))
                        
                        with state["data_lock"]:
                            for tf in TIMEFRAMES:
                                s_data = state["candle_data"][symbol].get(tf, {})
                                if "closes" in s_data and len(s_data["closes"]) > 0:
                                    # Update harga real-time pada candle terakhir
                                    s_data["closes"][-1] = last_p
                                    s_data["volumes"][-1] = vol_p
                                else:
                                    # Inisialisasi awal jika data kosong
                                    state["candle_data"][symbol][tf] = {
                                        "opens": [last_p] * PERIOD_PPURE,
                                        "closes": [last_p] * PERIOD_PPURE,
                                        "volumes": [max(vol_p / PERIOD_PPURE, 1.0)] * PERIOD_PPURE,
                                        "last_ts": int(time.time()),
                                    }
                        state["last_update_time"] = time.time()
                        state["connection_status"] = "Online (Live Sync)"
                except Exception:
                    state["connection_status"] = "Menghubungkan ulang..."
                
                time.sleep(0.2) # Jeda antar request agar tidak kena rate limit
            time.sleep(3) # Jeda perulangan siklus market

    threading.Thread(target=background_sync, daemon=True).start()
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

    if zf > 0.8: return zf, dRes, "🔴 SHORT"
    elif zf <= 0.45 and dRes < 0.4: return zf, dRes, "🟢 BUY"
    else: return zf, dRes, "🟡 WAIT"

# ============================================================
# UI STREAMLIT DASHBOARD
# ============================================================
st.title("🤖 ZF-CORE V16.6 Omni Dashboard")

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
        is_usdt_pair = "usdt" in symbol
        
        row = {"Pair": display_name}
        current_price = 0.0
        
        for tf in TIMEFRAMES:
            s_data = system_state["candle_data"][symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) > 0:
                current_price = s_data["closes"][-1]
        
        if is_usdt_pair:
            row["Price"] = f"${current_price:,.2f}"
        else:
            row["Price"] = f"Rp {current_price:,.0f}"

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

since_last = int(time.time() - system_state["last_update_time"])
status_text = f"📡 Status: {system_state['connection_status']} | Terakhir update: {since_last} detik yang lalu"
if since_last > 30:
    st.error(status_text + " (Koneksi bermasalah!)")
else:
    st.success(status_text)

time.sleep(2)
st.rerun()
