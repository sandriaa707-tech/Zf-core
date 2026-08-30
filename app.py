import json
import math
import threading
import time
import requests
import datetime
import calendar
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# KONFIGURASI HALAMAN WEB
# ============================================================
st.set_page_config(
    page_title="ZF-Core V16.6 Omni Terminal Web",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #c9d1d9; font-family: 'Courier New', Courier, monospace; }
    h1, h2, h3 { color: #58a6ff; font-family: 'Courier New', Courier, monospace; }
    </style>
""", unsafe_allow_html=True)

PERIOD_PPURE = 20
TIMEFRAMES = ["1H", "1D", "1W", "1M"] 

PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "SUI-USDT", "SHIB-USDT",
]

if 'candle_data' not in st.session_state:
    st.session_state.candle_data = {symbol: {tf: {"opens": [], "closes": [], "volumes": [], "last_ts": 0} for tf in TIMEFRAMES} for symbol in PAIRS}

data_lock = threading.Lock()

# ============================================================
# HITUNG TARGET WAKTU UTC (TIMESTAMP) UNTUK JAVASCRIPT
# ============================================================
def get_next_close_timestamps():
    now = datetime.datetime.utcnow()
    try:
        next_1h = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        next_1d = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        days_ahead = 7 - now.weekday()
        next_1w = (now + datetime.timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        _, last_day = calendar.monthrange(now.year, now.month)
        next_1m = (now.replace(day=1) + datetime.timedelta(days=last_day)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        return int(next_1h.timestamp()*1000), int(next_1d.timestamp()*1000), int(next_1w.timestamp()*1000), int(next_1m.timestamp()*1000)
    except:
        return 0, 0, 0, 0

ts_1h, ts_1d, ts_1w, ts_1m = get_next_close_timestamps()

# ============================================================
# FUNGSI PENARIK DATA REAL-TIME (MULTI-THREADING FAST FETCH)
# ============================================================
def fetch_single_pair(symbol):
    pair_data = {}
    for tf in TIMEFRAMES:
        url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={tf}&limit={PERIOD_PPURE + 5}"
        try:
            res = requests.get(url, timeout=5).json()
            if res.get("code") == "0":
                raw = res["data"]
                raw.reverse()
                pair_data[tf] = {
                    "opens": [float(item[1]) for item in raw],
                    "closes": [float(item[4]) for item in raw],
                    "volumes": [float(item[5]) for item in raw],
                    "last_ts": int(raw[-1][0]),
                }
        except: pass
    return symbol, pair_data

def update_all_market_data():
    threads = []
    results = {}
    
    def worker(sym):
        s_name, data = fetch_single_pair(sym)
        results[s_name] = data

    for symbol in PAIRS:
        t = threading.Thread(target=worker, args=(symbol,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    with data_lock:
        for sym, data in results.items():
            if data:
                st.session_state.candle_data[sym] = data

# Jalankan penarikan data terbaru setiap kali halaman di-render
update_all_market_data()

# ============================================================
# FUNGSI METODE ZF-SCORE (FORMULA V16.6)
# ============================================================
def calculate_zf(closes, volumes):
  if len(closes) < PERIOD_PPURE: return 0.0, 0.0, "WAIT", "#d29922"

  pMarket, vNow = closes[-1], volumes[-1]
  pPure = sum(closes[-PERIOD_PPURE:]) / PERIOD_PPURE
  vAvg = sum(volumes[-PERIOD_PPURE:]) / PERIOD_PPURE

  dRes = abs(pMarket - pPure) / pPure * 100.0 if pPure > 0 else 0.0
  volRatio = min(abs(vNow - vAvg) / vNow, 1.0) if vNow > 0 else 0.5
  zf = min(volRatio * math.tanh(dRes), 1.0)

  if zf > 0.8: return zf, dRes, "SHORT", "#ff7b72"
  elif zf <= 0.45 and dRes < 0.4: return zf, dRes, "BUY  ", "#3fb950"
  else: return zf, dRes, "WAIT ", "#d29922"

# ============================================================
# TAMPILAN UTAMA WEB
# ============================================================
st.title("🤖 ZF-CORE V16.6 | OMNI WEB TERMINAL")
st.markdown("Command Center Deterministic Protocol — Multi-Timeframe Analysis (1H, 1D, 1W, 1M).")

# Live Countdown Widget via JS
countdown_html = f"""
<div style="display: flex; gap: 10px; justify-content: space-between; background: #161b22; padding: 12px; border-radius: 8px; border: 1px solid #30363d; font-family: monospace; color: #c9d1d9; margin-bottom: 20px;">
    <div>⏱️ <b>1H:</b> <span id="cd-1h">--</span></div>
    <div>📅 <b>1D:</b> <span id="cd-1d">--</span></div>
    <div>📊 <b>1W:</b> <span id="cd-1w">--</span></div>
    <div>🗓️ <b>1M:</b> <span id="cd-1m">--</span></div>
</div>

<script>
const t1H = {ts_1h}, t1D = {ts_1d}, t1W = {ts_1w}, t1M = {ts_1m};
function fmt(ms) {{
    let s = Math.floor(ms / 1000);
    if (s < 0) return "Closed";
    let d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return d > 0 ? `${{d}}d ${{h}}h` : h > 0 ? `${{h}}h ${{m}}m` : `${{m}}m ${{sec}}s`;
}}
function upd() {{
    let now = new Date().getTime();
    document.getElementById("cd-1h").innerText = fmt(t1H - now);
    document.getElementById("cd-1d").innerText = fmt(t1D - now);
    document.getElementById("cd-1w").innerText = fmt(t1W - now);
    document.getElementById("cd-1m").innerText = fmt(t1M - now);
}}
setInterval(upd, 1000); upd();
</script>
"""
components.html(countdown_html, height=60)

# Render Kartu Koin Menggunakan Container Streamlit
cols = st.columns(2)

with data_lock:
    for idx, symbol in enumerate(PAIRS):
        display_sym = symbol.replace("-USDT", "")
        current_price = 0.0
        
        for tf in TIMEFRAMES:
            s_data = st.session_state.candle_data[symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) > 0:
                current_price = s_data["closes"][-1]
                
        price_str = f"{current_price:,.2f}" if current_price >= 1 else f"{current_price:.6f}"
        if current_price == 0.0: price_str = "-"
        
        trend_emoji = "⚪"
        s_1d = st.session_state.candle_data[symbol].get("1D", {})
        if "opens" in s_1d and "closes" in s_1d and len(s_1d["opens"]) > 0:
            trend_emoji = "🟢" if s_1d["closes"][-1] >= s_1d["opens"][-1] else "🔴"

        with cols[idx % 2]:
            with st.container(border=True):
                st.markdown(f"<div style='font-size: 16px; font-weight: bold; color: #f0883e; margin-bottom: 8px;'>⚡ [ {display_sym} ] — ${price_str} {trend_emoji}</div>", unsafe_allow_html=True)
                
                tf_icons = {"1H": "⏱️", "1D": "📅", "1W": "📊", "1M": "🗓️"}
                
                for tf in TIMEFRAMES:
                    s_data = st.session_state.candle_data[symbol].get(tf, {})
                    if "closes" in s_data and len(s_data["closes"]) > 0:
                        zf, dRes, status_text, color = calculate_zf(s_data["closes"], s_data["volumes"])
                        st.markdown(f"<div style='font-family: monospace; font-size: 14px; color: {color}; margin: 4px 0;'>&nbsp;&nbsp;<b>{tf_icons.get(tf, '🔹')} {tf}</b> : {zf:4.2f} [{status_text}] (dR: {dRes:4.1f}%)</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='font-family: monospace; font-size: 14px; color: #8b949e; margin: 4px 0;'>&nbsp;&nbsp;<b>{tf_icons.get(tf, '🔹')} {tf}</b> : Loading...</div>", unsafe_allow_html=True)

if st.button("🔄 Refresh Data Manual"):
    st.rerun()

# Auto-refresh otomatis setiap 15 detik
time.sleep(15)
st.rerun()
