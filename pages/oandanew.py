import math
import datetime
import calendar

import requests
import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

# ============================================================
# KONFIGURASI
# ============================================================
st.set_page_config(page_title="ZF-CORE", layout="wide", page_icon="📈")

PERIOD_P_PURE = 20
TIMEFRAMES = ["H1", "H4", "D", "W", "M"]
TF_LABELS = {"H1": "H1", "H4": "H4", "D": "D1", "W": "W1", "M": "MN"}

SYMBOLS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_AUD", "GBP_AUD", "CHF_JPY",
    "NZD_JPY", "CAD_JPY", "EUR_CHF", "GBP_CHF", "AUD_CHF", "NZD_CHF",
    "AUD_CAD", "NZD_CAD", "EUR_CAD", "GBP_CAD", "AUD_NZD", "EUR_NZD", "GBP_NZD",
    "XAU_USD", "XAG_USD",
    "BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD",
]

FETCH_TTL_SECONDS = 60   # match OANDA fetch cycle from the original script
UI_REFRESH_MS = 2000     # redraw every 2s like the original terminal loop


# ============================================================
# SECRETS / API KEY
# ============================================================
def get_credentials():
    """Read from st.secrets first (Streamlit Cloud), fall back to sidebar input."""
    api_key = st.secrets.get("OANDA_API_KEY", "")
    account_id = st.secrets.get("OANDA_ACCOUNT_ID", "")
    env = st.secrets.get("OANDA_ENV", "practice")  # "practice" or "live"

    with st.sidebar:
        st.header("⚙️ OANDA Connection")
        if not api_key:
            api_key = st.text_input("API Key", type="password")
        else:
            st.success("API key loaded from secrets")
        if not account_id:
            account_id = st.text_input("Account ID")
        env = st.selectbox("Environment", ["practice", "live"],
                            index=0 if env == "practice" else 1)
    return api_key, account_id, env


# ============================================================
# DATA FETCH (cached — hits OANDA at most once per FETCH_TTL_SECONDS)
# ============================================================
@st.cache_data(ttl=FETCH_TTL_SECONDS, show_spinner=False)
def get_candles(symbol, granularity, api_key, base_url):
    headers = {"Authorization": f"Bearer {api_key}", "Accept-Datetime-Format": "UNIX"}
    url = f"{base_url}/instruments/{symbol}/candles"
    params = {"count": PERIOD_P_PURE, "price": "M", "granularity": granularity}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("candles"), None
        try:
            err = r.json().get("errorMessage", r.text)
        except ValueError:
            err = r.text
        return None, f"HTTP {r.status_code} [{symbol} {granularity}]: {err[:120]}"
    except requests.exceptions.Timeout:
        return None, f"Timeout [{symbol} {granularity}]"
    except requests.exceptions.ConnectionError:
        return None, f"Koneksi gagal [{symbol} {granularity}]"
    except Exception as e:
        return None, f"Error [{symbol} {granularity}]: {e}"


def calc_sma(values):
    return sum(values) / len(values) if values else 0.0


def process_symbol_tf(symbol, tf, api_key, base_url):
    label = TF_LABELS[tf]
    candles, error = get_candles(symbol, tf, api_key, base_url)

    if not candles or len(candles) < PERIOD_P_PURE:
        return {"tf": label, "status": "WAIT", "price": 0.0, "open": 0.0, "error": error}

    try:
        closes = [float(c["mid"]["c"]) for c in candles]
        opens = [float(c["mid"]["o"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]

        p_market, p_open, v_now = closes[-1], opens[-1], volumes[-1]
        p_pure, v_avg = calc_sma(closes), calc_sma(volumes)

        d_res = (abs(p_market - p_pure) / p_pure * 100.0) if p_pure > 0 else 0.0
        v_abs = abs(v_now - v_avg)
        vol_ratio = min(v_abs / v_now, 1.0) if v_now > 0 else 0.5
        zf = min(vol_ratio * math.tanh(d_res), 1.0)

        status = "WAIT"
        if zf > 0.8:
            status = "SELL"
        elif zf <= 0.45 and d_res < 0.4:
            status = "BUY"

        return {"tf": label, "status": status, "price": p_market, "open": p_open, "error": None}
    except Exception as e:
        return {"tf": label, "status": "WAIT", "price": 0.0, "open": 0.0, "error": f"Exception [{symbol} {tf}]: {e}"}


def fetch_pair_data(symbol, api_key, base_url):
    results = {}
    daily_price, daily_open = 0.0, 0.0
    first_error = None
    for tf in TIMEFRAMES:
        r = process_symbol_tf(symbol, tf, api_key, base_url)
        results[TF_LABELS[tf]] = r
        if r["error"] and not first_error:
            first_error = r["error"]
        if tf == "D":
            daily_price, daily_open = r["price"], r["open"]
    return results, daily_price, daily_open, first_error


def get_full_alignment(tf_results):
    statuses = [v["status"] for v in tf_results.values()]
    if len(statuses) < len(TIMEFRAMES):
        return None
    if all(s == "BUY" for s in statuses):
        return "BUY"
    if all(s == "SELL" for s in statuses):
        return "SELL"
    return None


def format_price(symbol, price):
    if any(c in symbol for c in ("BTC", "ETH", "LTC", "BCH")):
        return f"{price:,.0f}"
    if "JPY" in symbol or "XAG" in symbol:
        return f"{price:.2f}"
    if "XAU" in symbol:
        return f"{price:.1f}"
    return f"{price:.4f}"


def get_countdowns():
    now = datetime.datetime.now()
    h1_m = 59 - now.minute
    h4_h = 3 - (now.hour % 4)
    d1_h = 23 - now.hour
    w1_d = 4 - now.weekday() if now.weekday() <= 4 else 0
    _, last_day = calendar.monthrange(now.year, now.month)
    mn_d = last_day - now.day
    return f"H1: {h1_m:02d}m", f"H4: {h4_h}h", f"D1: {d1_h}h", f"W1: {w1_d}d", f"MN: {mn_d}d"


# ============================================================
# STYLING
# ============================================================
def style_status(val):
    if val == "BUY":
        return "background-color:#1a7a3c;color:white;font-weight:bold;text-align:center"
    if val == "SELL":
        return "background-color:#c0392b;color:white;font-weight:bold;text-align:center"
    return "background-color:#d4ac0d;color:black;font-weight:bold;text-align:center"


# ============================================================
# MAIN APP
# ============================================================
def main():
    st.title("📈 ZF-CORE Dashboard")

    api_key, account_id, env = get_credentials()
    base_url = "https://api-fxpractice.oanda.com/v3" if env == "practice" else "https://api-fxtrade.oanda.com/v3"

    if not api_key:
        st.info("Enter your OANDA API key in the sidebar (or set OANDA_API_KEY in `.streamlit/secrets.toml`) to start.")
        st.stop()

    if HAS_AUTOREFRESH:
        st_autorefresh(interval=UI_REFRESH_MS, key="zfcore_refresh")
    else:
        st.caption("Install `streamlit-autorefresh` for automatic refresh (see requirements.txt).")

    h1, h4, d1, w1, mn = get_countdowns()
    st.caption(f"{datetime.datetime.now().strftime('%H:%M:%S')}  ·  {h1}  {h4}  {d1}  {w1}  {mn}  "
               f"·  data refreshes every {FETCH_TTL_SECONDS}s")

    rows = []
    errors = []
    full_buy, full_sell = [], []

    progress = st.empty()
    for i, sym in enumerate(SYMBOLS):
        progress.progress((i + 1) / len(SYMBOLS), text=f"Fetching {sym}...")
        tf_results, d_price, d_open, err = fetch_pair_data(sym, api_key, base_url)
        if err:
            errors.append(err)

        display_sym = sym.replace("_", "")
        if display_sym == "XAUUSD":
            display_sym = "GOLD"
        elif display_sym == "XAGUSD":
            display_sym = "SILVER"

        alignment = get_full_alignment(tf_results)
        if alignment == "BUY":
            full_buy.append(display_sym)
        elif alignment == "SELL":
            full_sell.append(display_sym)

        arrow = "▲" if d_price >= d_open else "▼"
        row = {
            "PAIR": display_sym,
            "HARGA": f"{format_price(sym, d_price)} {arrow}",
        }
        for label in TF_LABELS.values():
            row[label] = tf_results[label]["status"]
        rows.append(row)
    progress.empty()

    if full_buy or full_sell:
        cols = st.columns(2)
        if full_buy:
            cols[0].success(f"🟢 FULL BUY: {', '.join(full_buy)}")
        if full_sell:
            cols[1].error(f"🔴 FULL SELL: {', '.join(full_sell)}")

    df = pd.DataFrame(rows).set_index("PAIR")
    status_cols = list(TF_LABELS.values())
    styled = df.style.applymap(style_status, subset=status_cols)
    st.dataframe(styled, use_container_width=True, height=len(rows) * 36 + 40)

    if errors:
        with st.expander(f"⚠️ {len(errors)} fetch warning(s)"):
            for e in errors[:20]:
                st.text(e)


if __name__ == "__main__":
    main()
