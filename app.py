import json
import math
import os
import sys
import threading
import time
import requests
import websocket
import datetime
import calendar

# ============================================================
# KONFIGURASI
# ============================================================
PERIOD_PPURE = 20
TIMEFRAMES = ["1H", "1D", "1W", "1M"] 

PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "SUI-USDT", "SHIB-USDT",
]

COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_CYAN = "\033[96m"
COLOR_WHITE = "\033[97m"
COLOR_RESET = "\033[0m"

candle_data = {symbol: {tf: {} for tf in TIMEFRAMES} for symbol in PAIRS}
data_lock = threading.Lock()

# ============================================================
# FUNGSI COUNTDOWN (SISA WAKTU CANDLE) - BASIS UTC
# ============================================================
def get_candle_countdown(tf):
    now = datetime.datetime.utcnow()
    try:
        if tf == "1H":
            next_close = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        elif tf == "1D":
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
def fetch_initial_candles(symbol):
  for tf in TIMEFRAMES:
      url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={tf}&limit={PERIOD_PPURE + 5}"
      try:
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0":
          raw = res["data"]
          raw.reverse()
          with data_lock:
            candle_data[symbol][tf] = {
                "opens": [float(item[1]) for item in raw],
                "closes": [float(item[4]) for item in raw],
                "volumes": [float(item[5]) for item in raw],
                "last_ts": int(raw[-1][0]),
            }
      except: pass
      time.sleep(0.1) 

# ============================================================
# FUNGSI METODE ZF-SCORE (FORMULA V16.6)
# ============================================================
def calculate_zf(closes, volumes):
  if len(closes) < PERIOD_PPURE: return 0.0, 0.0, "W", COLOR_WHITE

  pMarket, vNow = closes[-1], volumes[-1]
  pPure = sum(closes[-PERIOD_PPURE:]) / PERIOD_PPURE
  vAvg = sum(volumes[-PERIOD_PPURE:]) / PERIOD_PPURE

  dRes = abs(pMarket - pPure) / pPure * 100.0 if pPure > 0 else 0.0
  volRatio = min(abs(vNow - vAvg) / vNow, 1.0) if vNow > 0 else 0.5
  zf = min(volRatio * math.tanh(dRes), 1.0)

  if zf > 0.8: return zf, dRes, "S", COLOR_RED
  elif zf <= 0.45 and dRes < 0.4: return zf, dRes, "B", COLOR_GREEN
  else: return zf, dRes, "W", COLOR_YELLOW

# ============================================================
# RENDER DASHBOARD (DUAL ROW HORIZONTAL MATRIX)
# ============================================================
def render_dashboard():
  print("\033[?25l\033[H", end="") 
  
  cd_1h = get_candle_countdown("1H")
  cd_1d = get_candle_countdown("1D")
  cd_1w = get_candle_countdown("1W")
  cd_1m = get_candle_countdown("1M")
  
  print(f"{COLOR_CYAN}=== ZF V16.6 | DUAL-ROW MATRIX ==={COLOR_RESET}\033[K")
  print(f"⏳ 1H({cd_1h})|1D({cd_1d})|1W({cd_1w})|1M({cd_1m})\033[K")
  print("=" * 55 + "\033[K")
  print(f"{'COIN/TF':<8} | {'PRICE':<9} | {'1H':<7} | {'1D':<7} | {'1W':<7} | {'1M':<7}\033[K")
  print("=" * 55 + "\033[K")

  with data_lock:
    for symbol in PAIRS:
      current_price = 0.0
      zf_blocks, dres_blocks = [], []

      for tf in TIMEFRAMES:
          s_data = candle_data[symbol].get(tf, {})
          if "closes" in s_data and len(s_data["closes"]) > 0:
            current_price = s_data["closes"][-1]
            zf, dRes, status, color = calculate_zf(s_data["closes"], s_data["volumes"])
            
            # Baris 1: ZF-Score & Status
            zf_str = f"{zf:3.2f}[{status}]"
            zf_blocks.append(f"{color}{zf_str:<7}{COLOR_RESET}")
            
            # Baris 2: dRes (Resonansi %)
            dres_str = f"{dRes:5.1f}%"
            dres_blocks.append(f"{color}{dres_str:<7}{COLOR_RESET}")
          else:
            zf_blocks.append(f"{'--':<7}")
            dres_blocks.append(f"{'--':<7}")
            
      price_str = f"{current_price:.6f}" if current_price < 1 else f"{current_price:.2f}"
      if current_price == 0.0: price_str = "-"
      
      # Pewarnaan harga berdasarkan 1D
      price_color = COLOR_WHITE
      s_1d = candle_data[symbol].get("1D", {})
      if "opens" in s_1d and "closes" in s_1d and len(s_1d["opens"]) > 0:
          if s_1d["closes"][-1] >= s_1d["opens"][-1]:
              price_color = COLOR_GREEN
          else:
              price_color = COLOR_RED

      display_sym = symbol.replace("-USDT", "")
      price_padded = f"{price_str:<9}"
      
      # Cetak Baris 1 (Nama Koin, Harga, dan ZF Score tiap TF)
      print(f"{display_sym:<8} | {price_color}{price_padded}{COLOR_RESET} | {zf_blocks[0]} | {zf_blocks[1]} | {zf_blocks[2]} | {zf_blocks[3]}\033[K")
      # Cetak Baris 2 (Label dRes di bawahnya)
      print(f"{' └ dR':<8} | {'':<9} | {dres_blocks[0]} | {dres_blocks[1]} | {dres_blocks[2]} | {dres_blocks[3]}\033[K")
      print("-" * 55 + "\033[K")
            
  print("S=SHORT(Merah) | B=BUY(Hijau) | W=WAIT(Kuning)\033[K")
  print("Ctrl+C = Keluar\033[K")
  print("\033[J", end="")

# ============================================================
# TREAD KHUSUS DISPLAY (UI THROTTLING 1 DETIK)
# ============================================================
def display_loop():
    while True:
        render_dashboard()
        time.sleep(1)

# ============================================================
# WEBSOCKET HANDLERS
# ============================================================
def on_message(ws, message):
  if message == "pong": return
  data = json.loads(message)
  if "event" in data and data["event"] == "error": return 

  if "data" in data and "arg" in data:
    symbol, channel = data["arg"]["instId"], data["arg"]["channel"]
    tf = channel.replace("candle", "") 
    c_info = data["data"][0]
    
    ts, open_p, close_p, vol = int(c_info[0]), float(c_info[1]), float(c_info[4]), float(c_info[5])

    with data_lock:
      if tf not in candle_data[symbol]: candle_data[symbol][tf] = {"opens": [], "closes": [], "volumes": [], "last_ts": ts}
      s_data = candle_data[symbol][tf]

      if "last_ts" in s_data and ts > s_data["last_ts"]:
        s_data["opens"].append(open_p); s_data["closes"].append(close_p); s_data["volumes"].append(vol); s_data["last_ts"] = ts
        if len(s_data["closes"]) > PERIOD_PPURE * 2: 
            s_data["opens"].pop(0); s_data["closes"].pop(0); s_data["volumes"].pop(0)
      elif "closes" in s_data and len(s_data["closes"]) > 0:
        s_data["opens"][-1] = open_p; s_data["closes"][-1] = close_p; s_data["volumes"][-1] = vol
      else:
        s_data["opens"] = [open_p]; s_data["closes"] = [close_p]; s_data["volumes"] = [vol]; s_data["last_ts"] = ts

def on_error(ws, error): pass
def on_close(ws, close_status_code, close_msg): pass

def on_open(ws):
  args = [{"channel": f"candle{tf}", "instId": symbol} for symbol in PAIRS for tf in TIMEFRAMES]
  ws.send(json.dumps({"op": "subscribe", "args": args}))

def run_ws():
  ws_url = "wss://ws.okx.com:8443/ws/v5/business"
  ws = websocket.WebSocketApp(ws_url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
  ws.run_forever(ping_interval=20, ping_timeout=10)

# ============================================================
# MAIN PROGRAM
# ============================================================
if __name__ == "__main__":
  os.system("clear" if os.name != "nt" else "cls")
  print(f"{COLOR_YELLOW}Menarik histori TF 1H, 1D, 1W, dan 1M... (Pastikan VPN aktif){COLOR_RESET}")
  
  threads = [threading.Thread(target=fetch_initial_candles, args=(p,)) for p in PAIRS]
  for t in threads: t.start()
  for t in threads: t.join()

  os.system("clear" if os.name != "nt" else "cls")
  
  render_thread = threading.Thread(target=display_loop, daemon=True)
  render_thread.start()
  
  try: run_ws()
  except KeyboardInterrupt:
    print("\033[?25h\033[H\033[J", end="")
    print(f"{COLOR_CYAN}Sistem ZF-Core dihentikan. Kursor dikembalikan.{COLOR_RESET}")
    sys.exit(0)
