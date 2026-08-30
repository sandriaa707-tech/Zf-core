import streamlit as st
import math
import threading
import time
import requests
import datetime
import calendar
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# KONFIGURASI ZF-CORE V16.6 (MOBILE VERTICAL LAYOUT)
# ============================================================
st.set_page_config(page_title="OANDA ZF-CORE Mobile", layout="centered")

OANDA_URL = "https://api-fxpractice.oanda.com"
PERIOD_PPURE = 20
TIMEFRAMES = ["H1", "H4", "D", "W", "M"]
PAIRS = [
    "XAU_USD", "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD",
    "USD_CAD", "NZD_USD", "EUR_GBP", "GBP_JPY", "EUR_JPY"
]
DRES_SCALE = 5.0  

# ============================================================
# MANAJEMEN API KEY
# ============================================================
api_key = ""
if "OANDA_API_KEY" in st.secrets:
    api_key = st.secrets["OANDA_API_KEY"]
else:
    st.sidebar.warning("⚠️ API Key OANDA tidak ditemukan di Secrets.")
    api_key = st.sidebar.text_input("Masukkan OANDA API Key (Practice):", type="password")

if not api_key:
    st.error("Silakan masukkan OANDA API Key untuk mulai menarik data.")
    st.stop()

# ============================================================
# FUNGSI COUNTDOWN SISA WAKTU CANDLE
# ============================================================
def get_candle_countdown(tf):
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        if tf == "H1":
            next_close = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        elif tf == "H4":
            next_close = (now + datetime.timedelta(hours=4 - now.hour % 4)).replace(minute=0, second=0, microsecond=0)
        elif tf == "D":
            next_close = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif tf == "W":
            days_ahead = 7 - now.weekday()
            next_close = (now + datetime.timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif tf == "M":
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
# INISIALISASI & BACKGROUND TASKS PARALEL
# ============================================================
@st.cache_resource
def init_oanda_system():
    state = {
        "candle_data": {symbol: {tf: {} for tf in TIMEFRAMES} for symbol in PAIRS},
        "api_status_msg": "Memulai...",
        "last_success_time": 0,
        "data_lock": threading.Lock(),
        "active_api_key": ""
    }

    def fetch_single_candle(symbol, tf, current_key):
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Accept-Datetime-Format": "UNIX"
        }
        url = f"{OANDA_URL}/v3/instruments/{symbol}/candles"
        params = {"granularity": tf, "count": PERIOD_PPURE + 5, "price": "M"}
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if "candles" in data:
                    opens = [float(c["mid"]["o"]) for c in data["candles"]]
                    closes = [float(c["mid"]["c"]) for c in data["candles"]]
                    volumes = [float(c["volume"]) for c in data["candles"]]
                    return symbol, tf, opens, closes, volumes, 200
            return symbol, tf, [], [], [], res.status_code
        except Exception:
            return symbol, tf, [], [], [], 500

    def fetch_oanda_data():
        while True:
            current_key = state["active_api_key"]
            if not current_key:
                time.sleep(2)
                continue

            tasks = [(s, t) for s in PAIRS for t in TIMEFRAMES]
            success_count = 0

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(fetch_single_candle, s, t, current_key) for s, t in tasks]
                for future in as_completed(futures):
                    symbol, tf, opens, closes, volumes, status = future.result()
                    if status == 200 and len(closes) > 0:
                        with state["data_lock"]:
                            state["candle_data"][symbol][tf] = {
                                "opens": opens, "closes": closes, "volumes": volumes
                            }
                        success_count += 1

            if success_count > 0:
                state["last_success_time"] = time.time()
                state["api_status_msg"] = "Online (Fast Parallel Mode)"
            else:
                state["api_status_msg"] = "Menunggu sinkronisasi server/Rate limit..."

            time.sleep(3)

    threading.Thread(target=fetch_oanda_data, daemon=True).start()
    return state

system_state = init_oanda_system()
system_state["active_api_key"] = api_key 

# ============================================================
# FORMULASI MATEMATIS DETERMINISTIK (BAB 4)
# ============================================================
def calculate_zf_deterministic(closes, volumes):
    if len(closes) < PERIOD_PPURE:
        return 0.0, 0.0, 0.0, "LOADING"

    pMarket = closes[-1]
    sub_closes = closes[-PERIOD_PPURE:]
    sub_vols = volumes[-PERIOD_PPURE:]
    
    total_vol = sum(sub_vols)
    if total_vol > 0:
        pPure = sum(c * v for c, v in zip(sub_closes, sub_vols)) / total_vol
    else:
        pPure = sum(sub_closes) / PERIOD_PPURE

    dRes = (abs(pMarket - pPure) / pPure) * 100.0 if pPure > 0 else 0.0

    v_mean = total_vol / PERIOD_PPURE if PERIOD_PPURE > 0 else 1.0
    v_abs = abs(volumes[-1] - v_mean)
    lambda_elasticity = v_abs / v_mean if v_mean > 0 else 0.1
    decay_t = lambda_elasticity * dRes

    v_total_book = total_vol * 1.5  
    v_ratio = v_abs / v_total_book if v_total_book > 0 else 0.5
    zf = min(v_ratio * math.tanh(dRes / DRES_SCALE), 1.0)

    if zf > 0.8: 
        status = "🔴 SHORT"
    elif zf <= 0.45 and dRes < 0.4: 
        status = "🟢 BUY"
    else: 
        status = "🟡 WAIT"

    return zf, dRes, decay_t, status

# ============================================================
# UI STREAMLIT DASHBOARD (VERTICAL MOBILE LAYOUT)
# ============================================================
st.title("🤖 ZF-CORE Mobile")

# Countdown ringkas untuk mobile
with st.expander("⏳ Sisa Waktu Candle (UTC)", expanded=False):
    col1, col2, col3 = st.columns(3)
    col1.metric("H1", get_candle_countdown("H1"))
    col2.metric("H4", get_candle_countdown("H4"))
    col3.metric("D1", get_candle_countdown("D"))

st.divider()

# Membangun Tampilan Vertikal (Card / Selectbox per Pair)
with system_state["data_lock"]:
    selected_pair = st.selectbox("Pilih Pair Aset:", [s.replace("_", "/") for s in PAIRS])
    raw_symbol = selected_pair.replace("/", "_")

    # Ambil tren harian
    trend_icon = "⚪"
    s_d = system_state["candle_data"][raw_symbol].get("D", {})
    if "opens" in s_d and "closes" in s_d and len(s_d["opens"]) > 0:
        trend_icon = "🟢" if s_d["closes"][-1] >= s_d["opens"][-1] else "🔴"

    # Harga terakhir H1
    current_price = 0.0
    s_data_h1 = system_state["candle_data"][raw_symbol].get("H1", {})
    if "closes" in s_data_h1 and len(s_data_h1["closes"]) > 0:
        current_price = s_data_h1["closes"][-1]

    price_str = f"${current_price:,.2f}" if "XAU" in raw_symbol else f"{current_price:,.5f}"
    if current_price == 0.0: price_str = "-"

    st.subheader(f"{selected_pair} {trend_icon} — {price_str}")

    vertical_table_data = []
    for tf in TIMEFRAMES:
        col_label = "MN" if tf == "M" else (tf + "1" if tf in ("D", "W") else tf)
        s_data = system_state["candle_data"][raw_symbol].get(tf, {})
        
        if "closes" in s_data and len(s_data["closes"]) >= PERIOD_PPURE:
            zf, dRes, decay_t, status = calculate_zf_deterministic(s_data["closes"], s_data["volumes"])
            vertical_table_data.append({
                "Timeframe": col_label,
                "Status": status,
                "ZF-Score": f"{zf:.2f}",
                "dRes": f"{dRes:.1f}%",
                "Decay": f"{decay_t:.2f}"
            })
        else:
            vertical_table_data.append({
                "Timeframe": col_label,
                "Status": "Loading...",
                "ZF-Score": "-",
                "dRes": "-",
                "Decay": "-"
            })

    df_vertical = pd.DataFrame(vertical_table_data)
    st.dataframe(df_vertical, use_container_width=True, hide_index=True)

st.divider()

if system_state["last_success_time"] == 0:
    st.info("🔄 Menunggu data pertama ditarik dari OANDA...")
else:
    since_last = int(time.time() - system_state["last_success_time"])
    status_text = f"📡 {system_state['api_status_msg']} | Update: {since_last}s lalu"
    if since_last > 30:
        st.error(status_text)
    else:
        st.success(status_text)

time.sleep(3)
st.rerun()
