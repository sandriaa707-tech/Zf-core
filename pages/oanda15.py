import streamlit as st
import requests
import math
import datetime
from concurrent.futures import ThreadPoolExecutor
from streamlit_autorefresh import st_autorefresh

# ============================================================
# KONFIGURASI HALAMAN STREAMLIT
# ============================================================
st.set_page_config(
    page_title="ZUHRI FORMALISM V16.6",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Auto-refresh halaman setiap 60 detik (60000 milidetik)
st_autorefresh(interval=60000, key="oanda_autorefresh")

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

# Broker (OANDA) menutup candle harian/mingguan/bulanan berdasarkan zona waktu
# New York (approx UTC-4/-5 tergantung DST), bukan waktu lokal mesin.
try:
    from zoneinfo import ZoneInfo
    BROKER_TZ = ZoneInfo("America/New_York")
except Exception:
    BROKER_TZ = None  # fallback ke waktu lokal jika zoneinfo/tzdata tidak tersedia

# ============================================================
# FUNGSI COUNTDOWN TIMER
# ============================================================
def get_countdowns():
    now = datetime.datetime.now(BROKER_TZ) if BROKER_TZ else datetime.datetime.now()

    h1_m, h1_s = 59 - now.minute, 59 - now.second
    h4_h = 3 - (now.hour % 4)
    d1_h = 23 - now.hour

    # Candle mingguan OANDA tutup Jumat malam (broker time).
    # Jika sudah Sabtu/Minggu, hitung mundur ke Jumat minggu berikutnya.
    weekday = now.weekday()  # Senin=0 ... Minggu=6
    if weekday <= 4:
        w1_d = 4 - weekday
    else:
        w1_d = (4 - weekday) % 7  # Sabtu -> 6 hari lagi, Minggu -> 5 hari lagi

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
    """
    Mengambil candle dari OANDA. Mengembalikan tuple (candles, error_message).
    Hanya mengambil candle yang sudah CLOSED (complete=True) agar SMA dan
    harga pasar tidak berubah-ubah di tengah periode akibat candle yang
    belum selesai terbentuk.
    """
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept-Datetime-Format": "UNIX"}
    url = f"{OANDA_URL}/instruments/{symbol}/candles"
    params = {
        "count": PERIOD_P_PURE,
        "price": "M",
        "granularity": granularity,
        "includeIncomplete": "false",  # jangan sertakan candle yang belum closed
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            candles = response.json().get("candles", [])
            # Jaga-jaga: filter manual juga, seandainya API tetap mengirim yang belum complete
            candles = [c for c in candles if c.get("complete", True)]
            if len(candles) < PERIOD_P_PURE:
                return None, f"Data tidak cukup ({len(candles)}/{PERIOD_P_PURE} candle closed)"
            return candles, None
        else:
            return None, f"HTTP {response.status_code}"
    except requests.exceptions.Timeout:
        return None, "Timeout"
    except requests.exceptions.RequestException as e:
        return None, f"Error koneksi: {e.__class__.__name__}"
    except Exception as e:
        return None, f"Error: {e.__class__.__name__}"

def calc_sma(data_list):
    return sum(data_list) / len(data_list) if data_list else 0.0

def process_symbol_tf(symbol, tf):
    candles, err = get_candles(symbol, tf)
    display_tf = "MN" if tf == "M" else tf

    if not candles:
        # Status ERROR berbeda dari WAIT, supaya jelas ini kegagalan fetch data
        # bukan sinyal pasar netral.
        return display_tf, 0.0, 0.0, "ERROR", 0.0, 0.0, 0.0, err

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

    return display_tf, zf, d_res, status_str, vol_ratio, p_market, p_open, None

def fetch_pair_data(sym):
    results, daily_price, daily_open = [], 0.0, 0.0
    for tf in TIMEFRAMES:
        data = process_symbol_tf(sym, tf)
        results.append(data)
        if tf == "D":
            daily_price, daily_open = data[5], data[6]
    return sym, results, daily_price, daily_open

# ============================================================
# CACHING DATA (mengurangi beban request ke OANDA)
# ============================================================
# Data di-cache selama 55 detik (sedikit di bawah interval auto-refresh 60s)
# supaya rerun Streamlit yang terjadi di luar siklus autorefresh (mis. saat
# user berinteraksi dengan widget lain) tidak memicu 80 request baru ke API.
@st.cache_data(ttl=55, show_spinner=False)
def load_all_data():
    temp_results, temp_info = {}, {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_pair_data, sym) for sym in SYMBOLS]
        for future in futures:
            sym, tf_data, d_price, d_open = future.result()
            temp_results[sym] = tf_data
            temp_info[sym] = {'price': d_price, 'open': d_open}
    return temp_results, temp_info

# ============================================================
# TAMPILAN UTAMA STREAMLIT
# ============================================================
st.title("⚡ ZUHRI FORMALISM V16.6")
st.markdown("**Oanda Deterministik Feed Dashboard (Auto-Update 60s)**")

# Sidebar untuk Informasi Waktu & Kontrol
st.sidebar.header("🕒 Countdown Timer")
t_h1, t_h4, t_d1, t_w1, t_mn = get_countdowns()
st.sidebar.write(f"- {t_h1}")
st.sidebar.write(f"- {t_h4}")
st.sidebar.write(f"- {t_d1}")
st.sidebar.write(f"- {t_w1}")
st.sidebar.write(f"- {t_mn}")
if BROKER_TZ:
    st.sidebar.caption("Waktu candle mengikuti broker (New York)")
else:
    st.sidebar.caption("⚠️ zoneinfo tidak tersedia, memakai waktu lokal mesin")

st.sidebar.markdown("---")
st.sidebar.markdown("💡 **Keterangan Sinyal:**")
st.sidebar.markdown("🟢 **[BUY]** (Hijau)")
st.sidebar.markdown("🔴 **[SHORT]** (Merah)")
st.sidebar.markdown("🟡 **[WAIT]** (Kuning)")
st.sidebar.markdown("⚫ **[ERROR]** (Gagal ambil data)")

if st.sidebar.button("🔄 Refresh Data Sekarang"):
    load_all_data.clear()  # bersihkan cache supaya benar-benar ambil data baru
    st.rerun()

with st.spinner("Sedang menarik data terbaru dari OANDA API..."):
    all_results, pair_info = load_all_data()

last_update_str = datetime.datetime.now().strftime("%H:%M:%S")
st.caption(f"Terakhir diperbarui secara otomatis: {last_update_str}")

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

                for (tf, zf, d_res, status, vol_ratio, _, _, err) in tf_data:
                    tf_label = f"{tf}1" if tf in ["D", "W"] else tf

                    if status == "BUY":
                        badge = f":green[[{status}]]"
                    elif status == "SHORT":
                        badge = f":red[[{status}]]"
                    elif status == "ERROR":
                        badge = f":gray[[{status}]]"
                    else:
                        badge = f":orange[[{status}]]"

                    if status == "ERROR":
                        st.markdown(f"**{tf_label}**: {badge} *({err})*")
                    else:
                        st.markdown(f"**{tf_label}**: {zf:.2f} {badge} *(dR: {d_res:.1f}% | Dec: {vol_ratio:.2f})*")
