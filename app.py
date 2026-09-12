import json
import math
import threading
import time
import datetime

import requests
import pandas as pd
import streamlit as st
import websocket  # pip install websocket-client
from concurrent.futures import ThreadPoolExecutor

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

st.set_page_config(page_title="ZF-CORE Dashboard", layout="wide", page_icon="📈")


# ============================================================
# STYLING BERSAMA
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


def get_candle_close_info():
    now = datetime.datetime.now()
    h1_close = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    h4_start = (now.hour // 4) * 4
    h4_close = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=h4_start + 4)
    d1_close = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    days_to_mon = (7 - now.weekday()) % 7 or 7
    w1_close = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_to_mon)
    if now.month == 12:
        mn_close = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        mn_close = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

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
# ============  BAGIAN OANDA (FOREX/METAL)  ==================
# ============================================================
OANDA_PERIOD = 20
OANDA_TIMEFRAMES = ["H1", "H4", "D", "W", "M"]
OANDA_TF_LABELS = {"H1": "H1", "H4": "H4", "D": "D1", "W": "W1", "M": "MN"}
OANDA_MAX_WORKERS = 8

OANDA_SYMBOLS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_AUD", "GBP_AUD", "CHF_JPY",
    "NZD_JPY", "CAD_JPY", "EUR_CHF", "GBP_CHF", "AUD_CHF", "NZD_CHF",
    "AUD_CAD", "NZD_CAD", "EUR_CAD", "GBP_CAD", "AUD_NZD", "EUR_NZD", "GBP_NZD",
    "XAU_USD", "XAG_USD",
    "BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD",
]


def oanda_get_credentials():
    api_key = st.secrets.get("OANDA_API_KEY", "")
    account_id = st.secrets.get("OANDA_ACCOUNT_ID", "")
    env = st.secrets.get("OANDA_ENV", "practice")

    with st.sidebar:
        st.header("⚙️ OANDA Connection")
        if not api_key:
            api_key = st.text_input("API Key", type="password", key="oanda_key_input")
        else:
            st.success("API key loaded from secrets")
        if not account_id:
            account_id = st.text_input("Account ID", key="oanda_acc_input")
        env = st.selectbox("Environment", ["practice", "live"],
                            index=0 if env == "practice" else 1, key="oanda_env_input")
    return api_key, account_id, env


def oanda_get_candles(symbol, granularity, api_key, base_url):
    headers = {"Authorization": f"Bearer {api_key}", "Accept-Datetime-Format": "UNIX"}
    url = f"{base_url}/instruments/{symbol}/candles"
    params = {"count": OANDA_PERIOD, "price": "M", "granularity": granularity}
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


def oanda_calc_sma(values):
    return sum(values) / len(values) if values else 0.0


def oanda_process_symbol_tf(symbol, tf, api_key, base_url):
    label = OANDA_TF_LABELS[tf]
    candles, error = oanda_get_candles(symbol, tf, api_key, base_url)

    if not candles or len(candles) < OANDA_PERIOD:
        return {"tf": label, "status": "WAIT", "price": 0.0, "open": 0.0, "error": error}

    try:
        closes = [float(c["mid"]["c"]) for c in candles]
        opens = [float(c["mid"]["o"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]

        p_market, p_open, v_now = closes[-1], opens[-1], volumes[-1]
        p_pure, v_avg = oanda_calc_sma(closes), oanda_calc_sma(volumes)

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


def oanda_fetch_pair_data(symbol, api_key, base_url):
    results = {}
    daily_price, daily_open = 0.0, 0.0
    first_error = None
    for tf in OANDA_TIMEFRAMES:
        r = oanda_process_symbol_tf(symbol, tf, api_key, base_url)
        results[OANDA_TF_LABELS[tf]] = r
        if r["error"] and not first_error:
            first_error = r["error"]
        if tf == "D":
            daily_price, daily_open = r["price"], r["open"]
    return results, daily_price, daily_open, first_error


def oanda_get_full_alignment(tf_results):
    statuses = [v["status"] for v in tf_results.values()]
    if len(statuses) < len(OANDA_TIMEFRAMES):
        return None
    if all(s == "BUY" for s in statuses):
        return "BUY"
    if all(s == "SELL" for s in statuses):
        return "SELL"
    return None


def oanda_format_price(symbol, price):
    if any(c in symbol for c in ("BTC", "ETH", "LTC", "BCH")):
        return f"{price:,.0f}"
    if "JPY" in symbol or "XAG" in symbol:
        return f"{price:.2f}"
    if "XAU" in symbol:
        return f"{price:.1f}"
    return f"{price:.4f}"


class OandaDataStore:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
        self.lock = threading.Lock()
        self.results = {}
        self.pair_info = {}
        self.last_update = None
        self.errors = []
        self.fetching = True
        threading.Thread(target=self._run, daemon=True).start()

    def _fetch_all(self):
        temp_results, temp_info, errors = {}, {}, []
        with ThreadPoolExecutor(max_workers=OANDA_MAX_WORKERS) as executor:
            futures = {
                executor.submit(oanda_fetch_pair_data, sym, self.api_key, self.base_url): sym
                for sym in OANDA_SYMBOLS
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
def get_oanda_store(api_key, base_url):
    return OandaDataStore(api_key, base_url)


def render_oanda_tab():
    api_key, account_id, env = oanda_get_credentials()
    base_url = "https://api-fxpractice.oanda.com/v3" if env == "practice" else "https://api-fxtrade.oanda.com/v3"

    if not api_key:
        st.info("Masukkan OANDA API key di sidebar (atau set OANDA_API_KEY di secrets).")
        return

    store = get_oanda_store(api_key, base_url)
    all_results, pair_info, last_update, errors, fetching = store.snapshot()

    status_txt = "🔄 memperbarui..." if fetching else "✅ siap"
    update_str = last_update.strftime("%H:%M:%S") if last_update else "-"
    st.caption(f"Update terakhir: {update_str} {status_txt}  ·  refresh tiap pergantian menit")
    st.caption(get_candle_close_info())

    if not all_results:
        st.info("Mengambil data pertama kali (butuh sampai ~1 menit untuk 32 pasangan)...")
        return

    rows = []
    full_buy, full_sell = [], []
    for sym in OANDA_SYMBOLS:
        tf_results = all_results.get(sym)
        if not tf_results:
            continue
        info = pair_info.get(sym, {"price": 0, "open": 0})

        display_sym = sym.replace("_", "")
        if display_sym == "XAUUSD":
            display_sym = "GOLD"
        elif display_sym == "XAGUSD":
            display_sym = "SILVER"

        alignment = oanda_get_full_alignment(tf_results)
        if alignment == "BUY":
            full_buy.append(display_sym)
        elif alignment == "SELL":
            full_sell.append(display_sym)

        arrow = "▲" if info["price"] >= info["open"] else "▼"
        row = {"PAIR": display_sym, "HARGA": f"{oanda_format_price(sym, info['price'])} {arrow}"}
        for label in OANDA_TF_LABELS.values():
            row[label] = tf_results[label]["status"]
        rows.append(row)

    if full_buy or full_sell:
        cols = st.columns(2)
        if full_buy:
            cols[0].success(f"🟢 FULL BUY: {', '.join(full_buy)}")
        if full_sell:
            cols[1].error(f"🔴 FULL SELL: {', '.join(full_sell)}")

    df = pd.DataFrame(rows).set_index("PAIR")
    status_cols = list(OANDA_TF_LABELS.values())
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


# ============================================================
# ============  BAGIAN OKX (KRIPTO)  ==========================
# ============================================================
OKX_REST_BASE = "https://www.okx.com"
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/business"

OKX_PERIOD = 20
OKX_TIMEFRAMES = ["H1", "H4", "D", "W", "M"]
OKX_BAR_MAP = {"H1": "1H", "H4": "4H", "D": "1D", "W": "1W", "M": "1M"}
OKX_CHANNEL_MAP = {tf: f"candle{bar}" for tf, bar in OKX_BAR_MAP.items()}

OKX_SYMBOLS = [
    "BTC-USDT", "ATOM-USDT", "GRT-USDT", "DOT-USDT", "SOL-USDT",
    "ETH-USDT", "TRX-USDT", "ADA-USDT", "BNB-USDT", "USDC-USDT",
    "DAI-USDT", "USDE-USDT", "AXS-USDT", "DASH-USDT", "CORE-USDT",
    "LUNC-USDT", "AAVE-USDT", "XAUT-USDT", "XRP-USDT", "DOGE-USDT",
]


def okx_fetch_history(inst_id, tf, limit=OKX_PERIOD + 1):
    url = f"{OKX_REST_BASE}/api/v5/market/candles"
    params = {"instId": inst_id, "bar": OKX_BAR_MAP[tf], "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json().get("data", [])
    data.reverse()
    candles = []
    for row in data:
        ts, o, h, l, c, vol = row[0], row[1], row[2], row[3], row[4], row[5]
        confirm = row[8] if len(row) > 8 else "1"
        candles.append([ts, float(o), float(h), float(l), float(c), float(vol), confirm])
    return candles


def okx_calc_sma(values):
    return sum(values) / len(values) if values else 0.0


def okx_compute_status(candles):
    if len(candles) < OKX_PERIOD:
        return {"status": "WAIT", "price": 0.0, "open": 0.0}

    last_n = candles[-OKX_PERIOD:]
    closes = [c[4] for c in last_n]
    volumes = [c[5] for c in last_n]

    p_market, p_open, v_now = closes[-1], last_n[-1][1], volumes[-1]
    p_pure, v_avg = okx_calc_sma(closes), okx_calc_sma(volumes)

    d_res = (abs(p_market - p_pure) / p_pure * 100.0) if p_pure > 0 else 0.0
    v_abs = abs(v_now - v_avg)
    vol_ratio = min(v_abs / v_now, 1.0) if v_now > 0 else 0.5
    zf = min(vol_ratio * math.tanh(d_res), 1.0)

    status = "WAIT"
    if zf > 0.8:
        status = "SELL"
    elif zf <= 0.45 and d_res < 0.4:
        status = "BUY"

    return {"status": status, "price": p_market, "open": p_open}


def okx_get_full_alignment(tf_results):
    if len(tf_results) < len(OKX_TIMEFRAMES):
        return None
    statuses = [tf_results[tf]["status"] for tf in OKX_TIMEFRAMES]
    if all(s == "BUY" for s in statuses):
        return "BUY"
    if all(s == "SELL" for s in statuses):
        return "SELL"
    return None


def okx_format_price(price):
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}"


class OkxLiveStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.candle_buf = {}
        self.tf_result = {}
        self.pair_info = {}
        self.last_update = None
        self.ws_connected = False
        self._seed_history()
        threading.Thread(target=self._websocket_loop, daemon=True).start()

    def _seed_history(self):
        new_buf, new_result, new_info = {}, {}, {}
        for sym in OKX_SYMBOLS:
            new_buf[sym] = {}
            new_result[sym] = {}
            for tf in OKX_TIMEFRAMES:
                try:
                    candles = okx_fetch_history(sym, tf)
                except Exception:
                    candles = []
                new_buf[sym][tf] = candles
                new_result[sym][tf] = okx_compute_status(candles)
            d = new_result[sym]["D"]
            new_info[sym] = {"price": d["price"], "open": d["open"]}

        with self.lock:
            self.candle_buf = new_buf
            self.tf_result = new_result
            self.pair_info = new_info
            self.last_update = datetime.datetime.now()

    def _build_subscribe_args(self):
        args = []
        for sym in OKX_SYMBOLS:
            for tf in OKX_TIMEFRAMES:
                args.append({"channel": OKX_CHANNEL_MAP[tf], "instId": sym})
        return args

    def _on_open(self, ws):
        self.ws_connected = True
        args = self._build_subscribe_args()
        for i in range(0, len(args), 20):
            ws.send(json.dumps({"op": "subscribe", "args": args[i:i + 20]}))
            time.sleep(0.2)

    def _on_message(self, ws, message):
        if message == "pong":
            return
        try:
            payload = json.loads(message)
        except Exception:
            return
        if "event" in payload:
            return

        arg = payload.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        if not channel.startswith("candle") or inst_id not in OKX_SYMBOLS:
            return

        tf = next((label for label, ch in OKX_CHANNEL_MAP.items() if ch == channel), None)
        if tf is None:
            return

        rows = payload.get("data", [])
        if not rows:
            return

        with self.lock:
            buf = self.candle_buf.setdefault(inst_id, {}).setdefault(tf, [])
            for row in rows:
                ts, o, h, l, c, vol = row[0], row[1], row[2], row[3], row[4], row[5]
                confirm = row[8] if len(row) > 8 else "0"
                new_candle = [ts, float(o), float(h), float(l), float(c), float(vol), confirm]
                if buf and buf[-1][0] == ts:
                    buf[-1] = new_candle
                else:
                    buf.append(new_candle)
                    if len(buf) > OKX_PERIOD + 5:
                        del buf[0]

            self.tf_result.setdefault(inst_id, {})[tf] = okx_compute_status(buf)
            if tf == "D":
                d = self.tf_result[inst_id]["D"]
                self.pair_info[inst_id] = {"price": d["price"], "open": d["open"]}
            self.last_update = datetime.datetime.now()

    def _on_error(self, ws, error):
        pass

    def _on_close(self, ws, code, msg):
        self.ws_connected = False

    def _ping_loop(self, ws_app):
        while True:
            time.sleep(20)
            try:
                if ws_app.sock and ws_app.sock.connected:
                    ws_app.send("ping")
            except Exception:
                pass

    def _websocket_loop(self):
        while True:
            ws_app = websocket.WebSocketApp(
                OKX_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            threading.Thread(target=self._ping_loop, args=(ws_app,), daemon=True).start()
            ws_app.run_forever(ping_interval=0)
            time.sleep(3)

    def snapshot(self):
        with self.lock:
            return (dict(self.tf_result), dict(self.pair_info),
                    self.last_update, self.ws_connected)


@st.cache_resource
def get_okx_store():
    return OkxLiveStore()


def render_okx_tab():
    store = get_okx_store()
    tf_result, pair_info, last_update, ws_connected = store.snapshot()

    conn_txt = "🟢 live" if ws_connected else "🟡 menyambung ulang..."
    update_str = last_update.strftime("%H:%M:%S") if last_update else "-"
    st.caption(f"Update terakhir: {update_str}  ·  {conn_txt}")
    st.caption(get_candle_close_info())

    rows = []
    full_buy, full_sell = [], []
    for sym in OKX_SYMBOLS:
        results = tf_result.get(sym)
        if not results or len(results) < len(OKX_TIMEFRAMES):
            continue
        info = pair_info.get(sym, {"price": 0, "open": 0})

        alignment = okx_get_full_alignment(results)
        display_sym = sym.replace("-", "")
        if alignment == "BUY":
            full_buy.append(display_sym)
        elif alignment == "SELL":
            full_sell.append(display_sym)

        arrow = "▲" if info["price"] >= info["open"] else "▼"
        row = {"PAIR": display_sym, "HARGA": f"{okx_format_price(info['price'])} {arrow}"}
        for tf in OKX_TIMEFRAMES:
            row[tf] = results[tf]["status"]
        rows.append(row)

    if not rows:
        st.info("Mengambil histori candle awal, mohon tunggu...")
        return

    if full_buy or full_sell:
        cols = st.columns(2)
        if full_buy:
            cols[0].success(f"🟢 FULL BUY: {', '.join(full_buy)}")
        if full_sell:
            cols[1].error(f"🔴 FULL SELL: {', '.join(full_sell)}")

    df = pd.DataFrame(rows).set_index("PAIR")
    styled = (
        df.style
        .applymap(style_status, subset=OKX_TIMEFRAMES)
        .applymap(style_price, subset=["HARGA"])
    )
    st.dataframe(styled, use_container_width=True, height=len(rows) * 36 + 40)


# ============================================================
# MAIN — satu halaman, dua tab
# ============================================================
def main():
    st.title("📈 ZF-CORE Dashboard")

    if HAS_AUTOREFRESH:
        st_autorefresh(interval=2000, key="zfcore_refresh")

    tab_oanda, tab_okx = st.tabs(["💱 OANDA (Forex/Metal)", "🪙 OKX (Kripto)"])

    with tab_oanda:
        render_oanda_tab()

    with tab_okx:
        render_okx_tab()


if __name__ == "__main__":
    main()
