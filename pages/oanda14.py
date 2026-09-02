import streamlit as st
import requests
import math
import time
import datetime
from concurrent.futures import ThreadPoolExecutor

# ============================================================
# KONFIGURASI HALAMAN STREAMLIT
# ============================================================
st.set_page_config(
    page_title="ZUHRI FORMALISM V16.6",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# KONFIGURASI OANDA API
# ============================================================
API_KEY = "49ffdf53849b61ca10ae1390654cd00c-3d998c4d1ef821a794b75b868861eae8"
ACCOUNT_ID = "101-011-17416884-001"

OANDA_URL = "https://api-fxpractice.oanda.com/v3"
PERIOD_P_PURE = 20
TIMEFRAMES = ["H1", "H4", "D", "W", "M"]

SYMBOLS = [
    # Majors
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    # Minors
    "EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_AUD", "GBP_AUD", "CHF_JPY",
    # Commodities & Crypto
    "XAU_USD", "XAG_USD", "BTC_USD"
]

# ============================================================
# FUNGSI COUNTDOWN TIMER
# ============================================================
def get_countdowns():
    now = datetime.datetime.now()
    h1_m, h1_s = 59 - now.minute, 59 - now.second
    h4_h = 3 - (now.hour % 4)
    d1_h = 23 - now.hour

    w1_d = 4 - now.weekday() if now.weekday() <= 4 else 0
    import calendar
    _, last_day = calendar.monthrange(now.year, now.month)
    mn_d = last_day - now.day

    return (
        f"H1: {h1_m:02d}m {h1_s:02d}s",
        f"H4: {h4_h}h {h1_m:02d}m",
        f"D1: {d1_h}h {h1_m:02d}m",
        f"W1: {w1_d}d {d1_h}h",
        f"MN: {mn_d}d {d1_h}h"
    )

# ============================================================
# FUNGSI PERHITUNGAN & API
# ============================================================
def get_candles(symbol, granularity):
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept-Datetime-Format": "UNIX"}
    url = f"{OANDA_URL}/instruments/{symbol}/candles"
    params = {"count": PERIOD_P_PURE, "price": "M", "granularity": granularity}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()['candles']
    except Exception:
        pass
    return None

def calc_sma(data_list):
    return sum(data_list) / len(data_list) if data_list else 0.0

def process_symbol_tf(symbol, tf):
    candles = get_candles(symbol, tf)
    display_tf = "MN" if tf == "M" else tf

    if not candles or len(candles) < PERIOD_P_PURE:
        return display_tf, 0.0, 0.0, "WAIT", 0.0, 0.0, 0.0

    closes = [float(c['mid']['c']) for c in candles]
    opens = [float(c['mid']['o']) for c in candles]
    volumes = [float(c['volume']) for c in candles]

    p_market, p_open, v_now = closes[-1], opens[-1], volumes[-1]
    p_pure, v_avg = calc_sma(closes), calc_sma(volumes)

    d_res = (abs(p_market - p_pure) / p_pure * 100.0) if p_pure > 0 else 0.0
    v_abs = abs(v_now - v_avg)
    vol_ratio = min(v_abs / v_now, 1.0) if v_now > 0 else 0.5
    zf = min(vol_ratio * math.tanh(d_res), 1.0)

    status_str = "WAIT"
    if zf > 0.8:
        status_str = "SHORT"
    elif zf <= 0.45 and d_res < 0.4:
        status_str = "BUY"

    return display_tf, zf, d_res, status_str, vol_ratio, p_market, p_open

def fetch_pair_data(sym):
    results, daily_price, daily_open = [], 0.0, 0.0
    for tf in TIMEFRAMES:
        data = process_symbol_tf(sym, tf)
        results.append(data)
        if tf == "D":
            daily_price, daily_open = data[5], data[6]
    return sym, results, daily_price, daily_open

# ============================================================
# TAMPILAN UTAMA STREAMLIT
# ============================================================
st.title("⚡ ZUHRI FORMALISM V16.6")
st.markdown("**Oanda Deterministik Feed Dashboard**")

# Sidebar untuk Informasi Waktu & Kontrol
st.sidebar.header("🕒 Countdown Timer")
t_h1, t_h4, t_d1, t_w1, t_mn = get_countdowns()
st.sidebar.write(f"- {t_h1}")
st.sidebar.write(f"- {t_h4}")
st.sidebar.write(f"- {t_d1}")
st.sidebar.write(f"- {t_w1}")
st.sidebar.write(f"- {t_mn}")

st.sidebar.markdown("---")
st.sidebar.markdown("💡 **Keterangan Sinyal:**")
st.sidebar.markdown("🟢 **[BUY]** (Hijau)")
st.sidebar.markdown("🔴 **[SHORT]** (Merah)")
st.sidebar.markdown("🟡 **[WAIT]** (Kuning)")

# Tombol Refresh Manual
if st.sidebar.button("🔄 Refresh Data Sekarang"):
    st.rerun()

# Mengambil Data Menggunakan ThreadPool
@st.cache_data(ttl=30)
def load_all_data():
    temp_results, temp_info = {}, {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_pair_data, sym) for sym in SYMBOLS]
        for future in futures:
            sym, tf_data, d_price, d_open = future.result()
            temp_results[sym] = tf_data
            temp_info[sym] = {'price': d_price, 'open': d_open}
    return temp_results, temp_info

with st.spinner("Sedang menarik data dari OANDA API..."):
    all_results, pair_info = load_all_data()

last_update_str = datetime.datetime.now().strftime("%H:%M:%S")
st.caption(f"Terakhir diperbarui: {last_update_str}")

# Layout Grid / Cards untuk Setiap Pair
cols_per_row = 3
symbol_chunks = [SYMBOLS[i:i + cols_per_row] for i in range(0, len(SYMBOLS), cols_per_row)]

for chunk in symbol_chunks:
    row_cols = st.columns(cols_per_row)
    for idx, sym in enumerate(chunk):
        with row_cols[idx]:
            tf_data = all_results.get(sym, [])
            info = pair_info.get(sym, {'price': 0, 'open': 0})
            display_sym = sym.replace('_', '')

            trend_icon = "🟢" if info['price'] >= info['open'] else "🔴"
            
            # Format Harga
            if "BTC" in sym:
                formatted_price = f"{info['price']:,.0f}"
            elif "JPY" in sym or "XAG" in sym:
                formatted_price = f"{info['price']:,.3f}"
            elif "XAU" in sym:
                formatted_price = f"{info['price']:,.2f}"
            else:
                formatted_price = f"{info['price']:,.4f}"

            # Container / Card untuk tiap pair
            with st.container(border=True):
                st.markdown(f"### ⚡ {display_sym} &nbsp; {trend_icon}")
                st.markdown(f"**Harga:** `{formatted_price}`")
                
                # Menampilkan data timeframe dalam bentuk teks terstruktur
                for (tf, zf, d_res, status, vol_ratio, _, _) in tf_data:
                    tf_label = f"{tf}1" if tf in ["D", "W"] else tf
                    
                    if status == "BUY":
                        badge = f":green[[{status}]]"
                    elif status == "SHORT":
                        badge = f":red[[{status}]]"
                    else:
                        badge = f":orange[[{status}]]"
                        
                    st.markdown(f"**{tf_label}**: {zf:.2f} {badge} *(dR: {d_res:.1f}% | Dec: {vol_ratio:.2f})*")

# Auto-refresh halaman setiap 60 detik agar data selalu up-to-date
time.sleep(60)
st.rerun()
