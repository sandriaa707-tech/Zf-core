import streamlit as st
import requests
import math
import datetime
import time

# ============================================================
# KONFIGURASI OANDA API & STREAMLIT
# ============================================================
st.set_page_config(
    page_title="Zuhri Formalism Forex Dashboard",
    page_icon="⚡",
    layout="wide"
)

# Konfigurasi OANDA API (Ganti dengan Token & Account ID Anda)
API_KEY = "49ffdf53849b61ca10ae1390654cd00c-3d998c4d1ef821a794b75b868861eae8"
ACCOUNT_ID = "101-011-17416884-001"
OANDA_URL = "https://api-fxpractice.oanda.com/v3"

PERIOD_P_PURE = 20
TIMEFRAMES = ["H1", "H4", "D", "W", "M"]

SYMBOLS = [
    "XAU_USD", "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD",
    "USD_CAD", "NZD_USD", "EUR_GBP", "GBP_JPY", "AUD_JPY"
]

# ============================================================
# FUNGSI PENDUKUNG & FORMATTER
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

def format_price(p, symbol):
    if "JPY" in symbol:
        return f"{p:,.3f}"
    return f"{p:,.2f}"

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
        return display_tf, 0.0, 0.0, "WAIT", "yellow", 0.0, 0.0, 0.0

    closes = [float(c['mid']['c']) for c in candles]
    opens = [float(c['mid']['o']) for c in candles]
    volumes = [float(c['volume']) for c in candles]

    p_market, p_open, v_now = closes[-1], opens[-1], volumes[-1]
    p_pure, v_avg = calc_sma(closes), calc_sma(volumes)

    d_res = (abs(p_market - p_pure) / p_pure * 100.0) if p_pure > 0 else 0.0
    v_abs = abs(v_now - v_avg)
    vol_ratio = min(v_abs / v_now, 1.0) if v_now > 0 else 0.5
    zf = min(vol_ratio * math.tanh(d_res), 1.0)

    status_str, color = "WAIT", "orange"
    if zf > 0.8:
        status_str, color = "SHORT", "red"
    elif zf <= 0.45 and d_res < 0.4:
        status_str, color = "BUY", "green"

    return display_tf, zf, d_res, status_str, color, vol_ratio, p_market, p_open

# ============================================================
# TAMPILAN UTAMA STREAMLIT
# ============================================================
st.title("⚡ ZF-CORE V16.6 | OANDA Deterministic Feed")
st.markdown("Dashboard analitik pasar finansial dan forex real-time menggunakan **OANDA Practice API**.")

# Baris Countdown
t_h1, t_h4, t_d1, t_w1, t_mn = get_countdowns()
cols_cd = st.columns(5)
cols_cd[0].metric("🕒 H1 Countdown", t_h1)
cols_cd[1].metric("🕒 H4 Countdown", t_h4)
cols_cd[2].metric("📅 D1 Countdown", t_d1)
cols_cd[3].metric("📊 W1 Countdown", t_w1)
cols_cd[4].metric("📈 MN Countdown", t_mn)

st.divider()

# Tombol Refresh Manual
if st.button("🔄 Refresh Data Sekarang"):
    st.rerun()

# Layout Utama Berdasarkan Simbol
for sym in SYMBOLS:
    display_sym = sym.replace("_", "")
    
    with st.container():
        tf_results = []
        daily_price, daily_open = 0.0, 0.0
        
        for tf in TIMEFRAMES:
            res = process_symbol_tf(sym, tf)
            tf_results.append(res)
            if tf == "D":
                daily_price, daily_open = res[6], res[7]

        trend_icon = "🟢" if daily_price >= daily_open else "🔴"
        formatted_p = format_price(daily_price, sym)
        
        st.subheader(f"{trend_icon} {display_sym} — {formatted_p}")
        
        # Kolom Timeframe
        cols = st.columns(len(TIMEFRAMES))
        for idx, (tf, zf, d_res, status, color, vol_ratio, _, _) in enumerate(tf_results):
            tf_label = f"{tf}1" if tf in ["D", "W"] else tf
            with cols[idx]:
                st.markdown(f"**{tf_label}**")
                st.markdown(f"ZF: `{zf:.2f}`")
                st.markdown(f"Status: :{color}[**{status}**]")
                st.caption(f"dR: {d_res:.1f}% | Dec: {vol_ratio:.2f}")
                
        st.markdown("---")

# Catatan Kaki
st.markdown("💡 **Panduan Warna:** `[BUY]` = Hijau | `[SHORT]` = Merah | `[WAIT]` = Kuning")
st.markdown("🚀 *Deploy aplikasi ini dengan mudah di **Streamlit Community Cloud** dengan mengunggah file ini sebagai `app.py` dan menambahkan file `requirements.txt`.*")
