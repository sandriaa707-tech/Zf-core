import json
import math
import threading
import time
import requests
import websocket
import datetime
import calendar
import streamlit as st

# ============================================================
# KONFIGURASI HALAMAN WEB
# ============================================================
st.set_page_config(
    page_title="ZF-Core V16.6 Omni Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #c9d1d9; }
    .stTable, div[data-testid="stTable"] { font-family: 'Courier New', Courier, monospace; }
    h1, h2, h3 { color: #58a6ff; font-family: 'Courier New', Courier, monospace; }
    </style>
""", unsafe_allow_html=True)

PERIOD_PPURE = 20
TIMEFRAMES = ["1D", "1W", "1M"] 

PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "SUI-USDT", "SHIB-USDT",
]

if 'candle_data' not in st.session_state:
    st.session_state.candle_data = {symbol: {tf: {"closes": [], "volumes": [], "last_ts": 0} for tf in TIMEFRAMES} for symbol in PAIRS}

data_lock = threading.Lock()

# ============================================================
# FUNGSI COUNTDOWN (SISA WAKTU CANDLE) - BASIS UTC
# ============================================================
def get_candle_countdown(tf):
    now = datetime.datetime.utcnow()
    try:
        if tf == "1D":
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
    except: return "N/A"

# ============================================================
# INSIALISASI HISTORI (REST API OKX)
# ============================================================
def fetch_initial_candles():
  for symbol in PAIRS:
      for tf in TIMEFRAMES:
          url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={tf}&limit={PERIOD_PPURE + 5}"
          try:
            res = requests.get(url, timeout=10).json()
            if res.get("code") == "0":
              raw = res["data"]
              raw.reverse()
              with data_lock:
                st.session_state.candle_data[symbol][tf] = {
                    "closes": [float(item[4]) for item in raw],
                    "volumes": [float(item[5]) for item in raw],
                    "last_ts": int(raw[-1][0]),
                }
          except: pass
          time.sleep(0.05)

if 'initialized' not in st.session_state:
    with st.spinner("Menarik data historis multi-timeframe dari OKX Server..."):
        fetch_initial_candles()
    st.session_state.initialized = True

# ============================================================
# FUNGSI METODE ZF-SCORE (FORMULA V16.6)
# ============================================================
def calculate_zf(closes, volumes):
  if len(closes) < PERIOD_PPURE: return 0.0, 0.0, "WAIT", "🟡"

  pMarket, vNow = closes[-1], volumes[-1]
  pPure = sum(closes[-PERIOD_PPURE:]) / PERIOD_PPURE
  vAvg = sum(volumes[-PERIOD_PPURE:]) / PERIOD_PPURE

  dRes = abs(pMarket - pPure) / pPure * 100.0 if pPure > 0 else 0.0
  volRatio = min(abs(vNow - vAvg) / vNow, 1.0) if vNow > 0 else 0.5
  zf = min(volRatio * math.tanh(dRes), 1.0)

  if zf > 0.8: return zf, dRes, "SHORT", "🔴"
  elif zf <= 0.45 and dRes < 0.4: return zf, dRes, "BUY", "🟢"
  else: return zf, dRes, "WAIT", "🟡"

# ============================================================
# TAMPILAN UTAMA WEB (DASHBOARD)
# ============================================================
st.title("⚡ ZUHRI FORMALISM V16.6 | OMNI WEB DASHBOARD")
st.markdown("Command Center Deterministic Protocol — Pemantauan Multi-Timeframe Resonansi Pasar.")

col_c1, col_c2, col_c3 = st.columns(3)
col_c1.metric("T-Close (1D)", get_candle_countdown("1D"))
col_c2.metric("T-Close (1W)", get_candle_countdown("1W"))
col_c3.metric("T-Close (1M)", get_candle_countdown("1M"))

st.markdown("---")
st.subheader("Matriks Pemantauan Pasar (Real-Time)")

# Tabel Data Koin
table_data = []
with data_lock:
    for symbol in PAIRS:
        display_sym = symbol.replace("-USDT", "")
        current_price = 0.0
        row = {"Coin": display_sym}
        
        for tf in TIMEFRAMES:
            s_data = st.session_state.candle_data[symbol].get(tf, {})
            if "closes" in s_data and len(s_data["closes"]) > 0:
                current_price = s_data["closes"][-1]
                zf, dRes, status, icon = calculate_zf(s_data["closes"], s_data["volumes"])
                row[tf] = f"{icon} {zf:.2f} | {dRes:.1f}% [{status}]"
            else:
                row[tf] = "Loading..."
                
        price_str = f"{current_price:,.2f}" if current_price >= 1 else f"{current_price:.6f}"
        row["Price"] = f"${price_str}" if current_price > 0 else "-"
        table_data.append(row)

st.table(table_data)

if st.button("🔄 Refresh Data"):
    st.rerun()

time.sleep(5)
st.rerun()
