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
# KONFIGURASI
# ============================================================
# Gunakan st.set_page_config jika ini adalah file utama (app.py). 
# Jika di folder pages/, Streamlit akan otomatis mengikuti config halaman utama,
# tetapi kita tetap bisa mengatur judul halamannya.
st.set_page_config(page_title="OANDA ZF-CORE", layout="wide")

OANDA_URL = "https://api-fxpractice.oanda.com"
PERIOD_PPURE = 20
TIMEFRAMES = ["H1", "H4", "D", "W", "M"]
PAIRS = [
    "XAU_USD", "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD",
    "USD_CAD", "NZD_USD", "EUR_GBP", "GBP_JPY", "EUR_JPY"
]

# ============================================================
# MANAJEMEN API KEY (STREAMLIT STYLE)
# ============================================================
api_key = ""
# Coba ambil dari Streamlit Secrets terlebih dahulu
if "OANDA_API_KEY" in st.secrets:
    api_key = st.secrets["OANDA_API_KEY"]
else:
    # Jika tidak ada di Secrets, sediakan input manual di Sidebar
    st.sidebar.warning("⚠️ API Key OANDA tidak ditemukan di Secrets.")
    api_key = st.sidebar.text_input("Masukkan OANDA API Key (Practice):", type="password")

if not api_key:
    st.error("Silakan masukkan OANDA API Key untuk mulai menarik data.")
    st.stop() # Hentikan proses jika API Key belum ada

# ============================================================
# FUNGSI COUNTDOWN (SISA WAKTU CANDLE)
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
            # Ambil API key terbaru dari state
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

            time.sleep(3) # Jeda antar request batch

    threading.Thread(target=fetch_oanda_data, daemon=True).start()
    return state

system_state = init_oanda_system()
# Update active API Key in state based on user input / secrets
system_state["active_api_key"] = api_key 

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
st.title("🤖 ZF-CORE V16.6 | OANDA (Forex & Gold)")

# Header Countdown
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("H1 Candle", get_candle_countdown("H1"))
col2.metric("H4 Candle", get_candle_countdown("H4"))
col3.metric("D1 Candle", get_candle_countdown("D"))
col4.metric("W1 Candle", get_candle_countdown("W"))
col5.metric("MN Candle", get_candle_countdown("M"))

st.divider()

# Membangun Data Tabel
table_data = []
with system_state["data_lock"]:
    for symbol in PAIRS:
        # Menentukan trend harian (Daily Open vs Close)
        trend_icon = "⚪"
        s_d = system_state["candle_data"][symbol].get("D", {})
        if "opens" in s_d and "closes" in s_d and len(s_d["opens"]) > 0:
            trend_icon = "🟢" if s_d["closes"][-1] >= s_d["opens"][-1] else "🔴"

        # Mengambil harga terakhir dari H1
        current_price = 0.0
        s_data_h1 = system_state["candle_data"][symbol].get("H1", {})
        if "closes" in s_data_h1 and len(s_data_h1["closes"]) > 0:
            current_price = s_data_h1["closes"][-1]

        # Format harga (XAU lebih sedikit desimal dibanding Forex biasa)
        price_str = f"${current_price:,.2f}" if "XAU" in symbol else f"{current_price:,.5f}"
        if current_price == 0.0: price_str = "-"

        row = {
            "Pair": f"{symbol.replace('_', '/')} {trend_icon}",
            "Price": price_str
        }

        # Hitung ZF setiap timeframe
        for tf in TIMEFRAMES:
            # Ubah label kolom agar lebih familiar (M -> MN, D -> D1)
            col_label = "MN" if tf == "M" else (tf + "1" if tf in ("D", "W") else tf)
            
            s_data = system_state["candle_data"][symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) >= PERIOD_PPURE:
                zf, dRes, status = calculate_zf(s_data["closes"], s_data["volumes"])
                row[col_label] = f"{status} (ZF: {zf:.2f} | dR: {dRes:.1f}%)"
            else:
                row[col_label] = "Loading..."
                
        table_data.append(row)

# Tampilkan DataFrame
df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

# Status Footer
if system_state["last_success_time"] == 0:
    st.info("🔄 Menunggu data pertama ditarik dari OANDA...")
else:
    since_last = int(time.time() - system_state["last_success_time"])
    status_text = f"📡 Status API: {system_state['api_status_msg']} | Terakhir update: {since_last} detik yang lalu"
    if since_last > 30:
        st.error(status_text + " (Data tertinggal, cek koneksi atau Limit API!)")
    else:
        st.success(status_text)

# Rerun loop
time.sleep(3)
st.rerun()
