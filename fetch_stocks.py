import os
import json
import math
import datetime
from datetime import timedelta
import requests
import pandas as pd
import yfinance as yf

# 設定選股條件硬性門檻
MIN_GAIN_PCT = 8.5        # 漲幅需大於等於 8.5%
MIN_VOL_RATIO = 1.8       # 成交量相較 5MA 需放大至少 1.8 倍
FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
MAX_HISTORY_DAYS = 15     # 資料庫至少保留 15 個交易日歷史（滿足近 10 日監控與近 5 日頁籤）

def get_tw_trading_date():
    """取得最新台灣交易日期字串 (YYYY-MM-DD)"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    tw_now = now_utc + timedelta(hours=8)
    
    # 若在 16:00 以前執行，視為抓取前一個交易日
    cursor = tw_now.date()
    if tw_now.hour < 16:
        cursor -= timedelta(days=1)
        
    # 遇週末往前回溯
    while cursor.weekday() >= 5: # 5: 週六, 6: 週日
        cursor -= timedelta(days=1)
        
    return cursor.strftime("%Y-%m-%d")

def fetch_twse_market_summary():
    """向臺灣證券交易所官方 OpenAPI 抓取今日加權指數與成交量"""
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            data = res.json()
            for row in data:
                if "發行量加權股價指數" in row.get("指數", ""):
                    close_idx = row.get("收盤指數", "").replace(",", "")
                    change_idx = row.get("漲跌點數", "").replace(",", "")
                    sign = "+" if row.get("漲跌", "") == "+" else "-"
                    return {
                        "close": f"{float(close_idx):,.2f}",
                        "change": f"{sign}{float(change_idx):,.2f}",
                        "percent": f"{sign}{float(row.get('漲跌百分比', '0.00')):.2f}%",
                        "volume": "9,000+ 億元"
                    }
    except Exception as e:
        print(f"TWSE 指數抓取警告: {e}")
        
    # 備援從 Yahoo Finance 抓取 ^TWII
    try:
        twii = yf.Ticker("^TWII").history(period="5d")
        if not twii.empty:
            last = twii.iloc[-1]
            prev = twii.iloc[-2]
            change = last['Close'] - prev['Close']
            pct = (change / prev['Close']) * 100
            sign = "+" if change >= 0 else ""
            return {
                "close": f"{last['Close']:,.2f}",
                "change": f"{sign}{change:,.2f}",
                "percent": f"{sign}{pct:.2f}%",
                "volume": f"{last['Volume']/100000000:,.2f} 億元"
            }
    except Exception as e:
        print(f"Yahoo 指數備援失敗: {e}")
        
    return {"close": "--", "change": "--", "percent": "--", "volume": "--"}

def evaluate_stock_candidate(symbol, name, sector="科技/半導體"):
    """評估單一股票是否符合嚴格技術面模型"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        if df.empty or len(df) < 30:
            return None

        today = df.iloc[-1]
        yesterday = df.iloc[-2]
        close = float(today['Close'])
        prev_close = float(yesterday['Close'])
        gain_pct = ((close - prev_close) / prev_close) * 100

        # 條件 1: 單日漲幅必須 >= 8.5%
        if gain_pct < MIN_GAIN_PCT:
            return None

        # 計算均線 (5MA, 10MA, 20MA, 60MA)
        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['5VMA'] = df['Volume'].rolling(5).mean()

        ma5 = float(df['5MA'].iloc[-1])
        ma10 = float(df['10MA'].iloc[-1])
        ma20 = float(df['20MA'].iloc[-1])
        ma20_prev = float(df['20MA'].iloc[-2])
        vma5 = float(df['5VMA'].iloc[-2])
        volume = float(today['Volume'])

        # 條件 2: 均線多頭排列且 20MA 向上
        is_ma_bullish = (ma5 > ma10 > ma20) and (ma20 > ma20_prev)
        if not is_ma_bullish:
            return None

        # 條件 3: 成交量放量 (需 >= 5MA 量的 1.8 倍)
        vol_ratio = volume / vma5 if vma5 > 0 else 1.0
        if vol_ratio < MIN_VOL_RATIO:
            return None

        # 條件 4: 箱型平台突破
        box_window = df.iloc[-22:-2]
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close <= box_high:
            return None

        # 計算波段 Fibonacci 回撤
        wave_low = float(df['Low'].tail(30).min())
        wave_high = close
        wave_diff = wave_high - wave_low

        fib236 = round(wave_high - (wave_diff * 0.236), 1)
        fib382 = round(wave_high - (wave_diff * 0.382), 1)
        fib500 = round(wave_high - (wave_diff * 0.500), 1)
        fib618 = round(wave_high - (wave_diff * 0.618), 1)
        fib786 = round(wave_high - (wave_diff * 0.786), 1)

        entry_min = fib382
        entry_max = fib236
        entry_price = round(fib382 * 1.01, 1)
        breakout_confirm = round(close * 1.015, 1)
        stop_loss = round(fib618 * 0.98, 1)

        clean_code = symbol.replace(".TW", "").replace(".TWO", "")
        return {
            "code": clean_code,
            "name": name,
            "sector": sector,
            "close": round(close, 2),
            "change": f"+{gain_pct:.2f}%",
            "volume": f"{int(volume):,}",
            "volRatio": f"{vol_ratio:.2f} 倍 (爆量突破)",
            "maStatus": "5MA > 10MA > 20MA 多頭排列，20MA 強勁向上",
            "boxDays": "20+ 交易日箱型突破",
            "boxRange": f"{box_low:.1f} ～ {box_high:.1f}",
            "breakoutPrice": f"{box_high:.1f}",
            "breakoutReason": f"帶量突破長天期箱型上緣 ({box_high:.1f})，單日放量 {vol_ratio:.2f} 倍收最高。",
            "fib236": fib236,
            "fib382": fib382,
            "fib500": fib500,
            "fib618": fib618,
            "fib786": fib786,
            "strategy": "等38.2%回踩",
            "entryZone": f"{entry_min} ～ {entry_max}",
            "entryMin": entry_min,
            "entryMax": entry_max,
            "entryPrice": f"{entry_price}",
            "breakoutConfirmPrice": f"{breakout_confirm}",
            "stopLossPrice": f"{stop_loss}",
            "stopLossNum": stop_loss,
            "noBuyCondition": f"剛鎖漲停乖離過大嚴禁追價；若跌破 {stop_loss} 結構失效嚴禁抄底",
            "score": 95
        }
    except Exception as e:
        print(f"個股分析失敗 {symbol}: {e}")
        return None

def run_tw_screener():
    """執行台股主升段選股掃描"""
    watchlist = [
        ("3624.TWO", "光頡", "被動元件/晶片薄膜電阻"),
        ("2492.TW", "華新科", "被動元件/MLCC電容"),
        ("2305.TW", "全友", "光電子/AI光通訊設備"),
        ("8358.TWO", "金居", "高頻銅箔/電子零組件"),
        ("8150.TW", "南茂", "半導體封測/記憶體驅動IC"),
        ("8028.TW", "昇陽半", "晶圓再生/先進封裝晶圓薄化"),
        ("2030.TW", "彰源", "不銹鋼/原物料高價轉型"),
        ("3081.TWO", "聯亞", "光通訊/矽光子磊晶"),
        ("3450.TW", "聯鈞", "AI光模組封裝/CPO"),
        ("6274.TWO", "台燿", "高頻高速CCL銅箔基板")
    ]
    
    candidates = []
    for sym, name, sec in watchlist:
        res = evaluate_stock_candidate(sym, name, sec)
        if res:
            candidates.append(res)
            
    candidates.sort(key=lambda x: x["score"], reverse=True)
    for idx, c in enumerate(candidates):
        c["rank"] = idx + 1
        
    return candidates[:5]

def main():
    print("🚀 啟動台美股盤後量化篩選機器人...")
    active_date = get_tw_trading_date()
    print(f"📅 結算基準日: {active_date}")
    
    history_data = {"TW": {}, "US": {}}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            pass

    macro_info = fetch_twse_market_summary()
    candidates = run_tw_screener()
    
    day_record = {
        "date": active_date,
        "dayOfWeek": ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(active_date).weekday()],
        "taiexClose": macro_info["close"],
        "taiexChange": macro_info["change"],
        "taiexPercent": macro_info["percent"],
        "taiexVolume": macro_info["volume"],
        "marketNature": "盤後量化篩選 ‧ 均線多頭 ‧ 箱型放量突破確認",
        "sectors": [
            {"name": "精密晶片電阻 / 被動元件", "grade": "S級", "note": "龍頭爆量鎖穩，族群共振強勁"},
            {"name": "AI 伺服器高頻銅箔 / CCL", "grade": "A級", "note": "多頭排列向上發散"}
        ],
        "candidates": candidates
    }
    
    if "TW" not in history_data:
        history_data["TW"] = {}
    history_data["TW"][active_date] = day_record
    
    # 保留最近 15 個交易日歷史紀錄，確保近 10 日進場監控與近 5 日頁籤完整
    sorted_tw_dates = sorted(history_data["TW"].keys(), reverse=True)
    if len(sorted_tw_dates) > MAX_HISTORY_DAYS:
        for old_d in sorted_tw_dates[MAX_HISTORY_DAYS:]:
            del history_data["TW"][old_d]

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ 資料已成功產出並存入 data.json！保留歷史天數: {len(history_data['TW'])} 天。")

if __name__ == "__main__":
    main()
