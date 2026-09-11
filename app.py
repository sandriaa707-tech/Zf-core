import math
import datetime
import threading
import time

import requests
import pandas as pd
import streamlit as st
from concurrent.futures import ThreadPoolExecutor

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

# ============================================================
# KONFIGURASI
# ============================================================
st.set_page_config(page_title="ZF-CORE", layout="wide", page_icon="📈")

PERIOD_P_PURE = 25
TIMEFRAMES = ["H1", "H4", "D", "W", "M"]
TF_LABELS = {"H1": "H1", "H4": "H4", "D": "D1", "W": "W1", "M": "MN"}
MAX_WORKERS = 8

SYMBOLS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_AUD", "GBP_AUD", "CHF_JPY",
    "NZD_JPY", "CAD_JPY", "EUR_CHF", "GBP_CHF", "AUD_CHF", "NZD_CHF",
    "AUD_CAD", "NZD_CAD", "EUR_CAD", "GBP_CAD", "AUD_NZD", "EUR_NZD", "GBP_NZD",
    "XAU_USD", "XAG_USD",
    "BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD",
]


# ============================================================
# SECRETS / API KEY
# ============================================================
def get_credentials():
    api_key = st.secrets.get("OANDA_API_KEY", "")
    account_id = st.secrets.get("OANDA_ACCOUNT_ID", "")
    env = st.secrets.get("OANDA_ENV", "practice")

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
# FETCH (dipanggil HANYA dari thread background, bukan dari render)
# ============================================================
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


# ============================================================
# CANDLE CLOSE COUNTDOWN — berbasis jam device
# ============================================================
def get_candle_close_info():
    now = datetime.datetime.now()

    h1_close = (now.replace(minute=0, second=0, microsecond=0)
                + datetime.timedelta(hours=1))

    h4_block_start = (now.hour // 4) * 4
    h4_close = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                + datetime.timedelta(hours=h4_block_start + 4))

    d1_close = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                + datetime.timedelta(days=1))

    days_to_monday = (7 - now.weekday()) % 7
    if days_to_monday == 0:
        days_to_monday = 7
    w1_close = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                + datetime.timedelta(days=days_to_monday))

    if now.month == 12:
        mn_close = now.replace(year=now.year + 1, month=1, day=1,
                                hour=0, minute=0, second=0, microsecond=0)
    else:
        mn_close = now.replace(month=now.month + 1, day=1,
                                hour=0, minute=0, second=0, microsecond=0)

    def fmt(label, close):
        total_min = int((close - now).total_seconds() // 60)
        d, rem = divmod(total_min, 1440)
        h, m = divmod(rem, 60)
        if d > 0:
            return f"{label}:{d}d"
        if h > 0:
            return f"{label}:{h}h"
        return f"{label}:{m:02d}m"

    return " ".join([
        fmt("H1", h1_close), fmt("H4", h4_close), fmt("D1", d1_close),
        fmt("W1", w1_close), fmt("MN", mn_close),
    ])


# ============================================================
# STYLING
# ============================================================
def style_status(val):
    if val == "BUY":
        return "background-color:#1a7a3c;color:white;font-weight:bold;text-align:center"
    if val == "SELL":
        return "background-color:#c0392b;color:white;font-weight:bold;text-align:center"
    return "background-color:#d4ac0d;color:black;font-weight:bold;text-align:center"


def style_price(val):
    if "▲" in val:
        return "color:#1a7a3c;font-weight:bold"
    if "▼" in val:
        return "color:#c0392b;font-weight:bold"
    return ""


# ============================================================
# BACKGROUND DATA STORE — 1 thread, jalan terus, fetch paralel,
# selaras ke detik ke-00 tiap menit
# ============================================================
class DataStore:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
        self.lock = threading.Lock()
        self.results = {}
        self.pair_info = {}
        self.last_update = None
        self.errors = []
        self.fetching = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _fetch_all(self):
        temp_results, temp_info, errors = {}, {}, []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_pair_data, sym, self.api_key, self.base_url): sym
                for sym in SYMBOLS
            }
            for future in futures:
                sym = futures[future]
                try:
                    tf_results, d_price, d_open, err = future.result()
                except Exception as e:
                    errors.append(f"{sym}: {e}")
                    continue
                temp_results[sym] = tf_results
                temp_info[sym] = {"price": d_price, "open": d_open}
                if err:
                    errors.append(err)

        with self.lock:
            if temp_results:
                self.results = temp_results
                self.pair_info = temp_info
            self.last_update = datetime.datetime.now()
            self.errors = errors
            self.fetching = False

    def _run(self):
        while True:
            try:
                with self.lock:
                    self.fetching = True
                self._fetch_all()
            except Exception as e:
                with self.lock:
                    self.errors = [f"Fatal: {e}"]
                    self.fetching = False

            now = datetime.datetime.now()
            sleep_s = 60 - now.second - now.microsecond / 1_000_000
            if sleep_s <= 0:
                sleep_s += 60
            time.sleep(sleep_s)

    def snapshot(self):
        with self.lock:
            return dict(self.results), dict(self.pair_info), self.last_update, list(self.errors), self.fetching


@st.cache_resource
def get_data_store(api_key, base_url):
    return DataStore(api_key, base_url)


def seconds_to_next_minute():
    now = datetime.datetime.now()
    s = 60 - now.second
    return s if s > 0 else 60


# ============================================================
# MAIN APP
# ============================================================
def main():
    st.title("📈 ZF-CORE Dashboard")

    api_key, account_id, env = get_credentials()
    base_url = "https://api-fxpractice.oanda.com/v3" if env == "practice" else "https://api-fxtrade.oanda.com/v3"

    if not api_key:
        st.info("Enter your OANDA API key in the sidebar (or set OANDA_API_KEY in secrets) to start.")
        st.stop()

    store = get_data_store(api_key, base_url)

    if HAS_AUTOREFRESH:
        wait_ms = seconds_to_next_minute() * 1000
        interval = 2000 if store.last_update is None else wait_ms
        st_autorefresh(interval=interval, key="zfcore_refresh")

    all_results, pair_info, last_update, errors, fetching = store.snapshot()

    status_txt = "🔄 memperbarui..." if fetching else "✅ siap"
    update_str = last_update.strftime("%H:%M:%S") if last_update else "-"
    st.caption(f"{datetime.datetime.now().strftime('%H:%M:%S')}  ·  update terakhir: {update_str} {status_txt}  "
               f"·  refresh tiap pergantian menit")

    st.caption(get_candle_close_info())

    if not all_results:
        st.info("Mengambil data pertama kali (butuh sampai ~1 menit untuk 32 pasangan)...")
        return

    rows = []
    full_buy, full_sell = [], []

    for sym in SYMBOLS:
        tf_results = all_results.get(sym)
        if not tf_results:
            continue
        info = pair_info.get(sym, {"price": 0, "open": 0})

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

        arrow = "▲" if info["price"] >= info["open"] else "▼"
        row = {"PAIR": display_sym, "HARGA": f"{format_price(sym, info['price'])} {arrow}"}
        for label in TF_LABELS.values():
            row[label] = tf_results[label]["status"]
        rows.append(row)

    if full_buy or full_sell:
        cols = st.columns(2)
        if full_buy:
            cols[0].success(f"🟢 FULL BUY: {', '.join(full_buy)}")
        if full_sell:
            cols[1].error(f"🔴 FULL SELL: {', '.join(full_sell)}")

    df = pd.DataFrame(rows).set_index("PAIR")
    status_cols = list(TF_LABELS.values())
    styled = (
        df.style
        .applymap(style_status, subset=status_cols)
        .applymap(style_price, subset=["HARGA"])
    )
    st.dataframe(styled, use_container_width=True, height=len(rows) * 36 + 40)

    if errors:
        with st.expander(f"⚠️ {len(errors)} fetch warning(s)"):
            for e in errors[:20]:
                st.text(e)


if __name__ == "__main__":
    main()
