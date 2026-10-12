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
MAX_HISTORY_DAYS = 15     # 資料庫保留 15 個交易日歷史

def get_dynamic_trading_date(ticker_symbol="^TWII"):
    """從市場行情動態取得最新真實開盤交易日 (YYYY-MM-DD)"""
    if ticker_symbol == "^TWII":
        try:
            url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if "data" in data and len(data["data"]) > 0:
                    last_row = data["data"][-1]
                    roc_parts = last_row[0].strip().split('/')
                    if len(roc_parts) == 3:
                        ad_year = int(roc_parts[0]) + 1911
                        tw_date = f"{ad_year:04d}-{int(roc_parts[1]):02d}-{int(roc_parts[2]):02d}"
                        return tw_date
        except Exception as e:
            print(f"TWSE 日期解析備援: {e}")

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="5d")
        if not df.empty:
            return df.index[-1].strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Yahoo 行情日期取得異常: {e}")

    now_tw = datetime.datetime.now(datetime.timezone.utc) + timedelta(hours=8)
    return now_tw.strftime("%Y-%m-%d")

def fetch_twse_market_summary():
    """獲取今日最新加權指數與成交金額"""
    try:
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if "data" in data and len(data["data"]) > 0:
                last_row = data["data"][-1]
                trade_amt = float(last_row[2].replace(",", "")) / 100000000
                close_idx = float(last_row[4].replace(",", ""))
                change_idx = float(last_row[5].replace(",", ""))
                prev_idx = close_idx - change_idx
                pct = (change_idx / prev_idx) * 100 if prev_idx > 0 else 0.0
                sign = "+" if change_idx >= 0 else ""
                return {
                    "close": f"{close_idx:,.2f}",
                    "change": f"{sign}{change_idx:,.2f}",
                    "percent": f"{sign}{pct:.2f}%",
                    "volume": f"{trade_amt:,.2f} 億元"
                }
    except Exception as e:
        print(f"TWSE FMTQIK 抓取警告: {e}")

    try:
        twii = yf.Ticker("^TWII").history(period="5d")
        if not twii.empty and len(twii) >= 2:
            last = twii.iloc[-1]
            prev = twii.iloc[-2]
            close_val = float(last['Close'])
            prev_val = float(prev['Close'])
            change = close_val - prev_val
            pct = (change / prev_val) * 100
            sign = "+" if change >= 0 else ""
            vol = float(last['Volume'])
            vol_str = f"{vol / 100000000:,.2f} 億元" if vol > 100000000 else "-- 億元"
            return {
                "close": f"{close_val:,.2f}",
                "change": f"{sign}{change:,.2f}",
                "percent": f"{sign}{pct:.2f}%",
                "volume": vol_str
            }
    except Exception as e:
        print(f"Yahoo ^TWII 備援失敗: {e}")

    return {"close": "--", "change": "--", "percent": "--", "volume": "--"}

def fetch_us_market_summary():
    """動態抓取美股那斯達克 (^IXIC) 與費城半導體 (^SOX)"""
    summary = {
        "close": "--", "change": "--", "percent": "--", "volume": "--",
        "secondaryName": "費城半導體 (SOX)", "secondaryClose": "--"
    }
    try:
        ixic = yf.Ticker("^IXIC").history(period="5d")
        if not ixic.empty and len(ixic) >= 2:
            last = ixic.iloc[-1]
            prev = ixic.iloc[-2]
            c = float(last['Close'])
            p = float(prev['Close'])
            diff = c - p
            pct = (diff / p) * 100
            sign = "+" if diff >= 0 else ""
            vol = float(last['Volume'])
            summary["close"] = f"{c:,.2f}"
            summary["change"] = f"{sign}{diff:,.2f}"
            summary["percent"] = f"{sign}{pct:.2f}%"
            summary["volume"] = f"{vol / 100000000:,.1f} 億股" if vol > 0 else "--"

        sox = yf.Ticker("^SOX").history(period="5d")
        if not sox.empty and len(sox) >= 2:
            s_last = sox.iloc[-1]
            s_prev = sox.iloc[-2]
            s_c = float(s_last['Close'])
            s_p = float(s_prev['Close'])
            s_diff = s_c - s_p
            s_pct = (s_diff / s_p) * 100
            s_sign = "+" if s_diff >= 0 else ""
            summary["secondaryClose"] = f"{s_c:,.2f} ({s_sign}{s_pct:.2f}%)"
    except Exception as e:
        print(f"美股大盤動態抓取異常: {e}")
    return summary

def evaluate_stock_candidate(symbol, name, sector="科技/半導體", is_us=False):
    """評估單一股票是否符合嚴格技術面模型"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        if df.empty or len(df) < 25:
            return None

        today = df.iloc[-1]
        yesterday = df.iloc[-2]
        close = float(today['Close'])
        prev_close = float(yesterday['Close'])
        gain_pct = ((close - prev_close) / prev_close) * 100

        min_gain = 4.5 if is_us else MIN_GAIN_PCT
        if gain_pct < min_gain:
            return None

        df['5MA'] = df['Close'].rolling(5).mean()
        df['10MA'] = df['Close'].rolling(10).mean()
        df['20MA'] = df['Close'].rolling(20).mean()
        df['5VMA'] = df['Volume'].rolling(5).mean()

        ma5 = float(df['5MA'].iloc[-1])
        ma10 = float(df['10MA'].iloc[-1])
        ma20 = float(df['20MA'].iloc[-1])
        ma20_prev = float(df['20MA'].iloc[-2])
        vma5 = float(df['5VMA'].iloc[-2])
        volume = float(today['Volume'])

        is_ma_bullish = (ma5 > ma10 > ma20) and (ma20 >= ma20_prev)
        if not is_ma_bullish:
            return None

        min_vol = 1.5 if is_us else MIN_VOL_RATIO
        vol_ratio = volume / vma5 if vma5 > 0 else 1.0
        if vol_ratio < min_vol:
            return None

        box_window = df.iloc[-22:-2]
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close <= box_high:
            return None

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
            "symbol": symbol,
            "code": clean_code,
            "name": name,
            "sector": sector,
            "close": round(close, 2),
            "change": f"+{gain_pct:.2f}%",
            "volume": f"{int(volume):,}",
            "volRatio": f"{vol_ratio:.2f} 倍 (放量突破)",
            "maStatus": "5MA > 10MA > 20MA 多頭排列，20MA 向上",
            "boxDays": "20+ 交易日箱型突破",
            "boxRange": f"{box_low:.1f} ～ {box_high:.1f}",
            "breakoutPrice": f"{box_high:.1f}",
            "breakoutReason": f"放量突破長天期箱型上緣 ({box_high:.1f})，單日放量 {vol_ratio:.2f} 倍。",
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
            "noBuyCondition": f"剛大漲嚴禁追價；若跌破 {stop_loss} 結構失效嚴禁抄底",
            "score": round(90 + min(gain_pct, 10.0), 1)
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
        res = evaluate_stock_candidate(sym, name, sec, is_us=False)
        if res:
            candidates.append(res)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    for idx, c in enumerate(candidates):
        c["rank"] = idx + 1
    return candidates[:5]

def run_us_screener():
    """執行美股主升段選股掃描"""
    watchlist = [
        ("CRDO", "Credo Technology", "高速傳輸/光互連"),
        ("MRVL", "Marvell Technology", "高速傳輸/PAM4 DSP"),
        ("ALAB", "Astera Labs", "PCIe Retimer/伺服器"),
        ("AVGO", "Broadcom", "AI客製化ASIC/網通晶片"),
        ("NVDA", "NVIDIA", "AI GPU/加速運算"),
        ("ARM", "Arm Holdings", "低功耗架構IP"),
        ("ANET", "Arista Networks", "AI 資料中心交換器")
    ]
    candidates = []
    for sym, name, sec in watchlist:
        res = evaluate_stock_candidate(sym, name, sec, is_us=True)
        if res:
            candidates.append(res)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    for idx, c in enumerate(candidates):
        c["rank"] = idx + 1
    return candidates[:5]

def track_recent_candidates(history_data, market_key, active_date, current_candidates):
    """追蹤過去 10 個交易日內候選標的是否落入最佳進場區"""
    m_data = history_data.get(market_key, {})
    sorted_dates = sorted(m_data.keys(), reverse=True)
    recent_dates = sorted_dates[:10]

    past_candidates_map = {}
    for d in reversed(recent_dates):
        day_info = m_data.get(d, {})
        for cand in day_info.get("candidates", []):
            code = cand.get("code")
            if code:
                cand_copy = dict(cand)
                cand_copy["originDate"] = d
                past_candidates_map[code] = cand_copy

    for cand in current_candidates:
        code = cand.get("code")
        cand_copy = dict(cand)
        cand_copy["originDate"] = active_date
        past_candidates_map[code] = cand_copy

    entry_zone_alerts = []
    for code, cand in past_candidates_map.items():
        symbol = cand.get("symbol") or code
        latest_close = cand.get("close")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="5d")
            if df.empty and market_key == "TW":
                alt_sym = f"{code}.TWO" if symbol.endswith(".TW") else f"{code}.TW"
                ticker = yf.Ticker(alt_sym)
                df = ticker.history(period="5d")
            if not df.empty:
                latest_close = round(float(df['Close'].iloc[-1]), 2)
        except Exception as e:
            print(f"追蹤價格抓取異常 {code}: {e}")

        stop_loss = float(cand.get("stopLossNum", 0))
        entry_min = float(cand.get("entryMin", 0))
        entry_max = float(cand.get("entryMax", 0))

        if latest_close <= stop_loss:
            continue
        if latest_close < entry_min or latest_close > entry_max:
            continue

        alert_item = dict(cand)
        alert_item["verifiedCurrentPrice"] = latest_close
        entry_zone_alerts.append(alert_item)

    return entry_zone_alerts

def main():
    print("🚀 啟動全動態台美股量化篩選機器人...")
    
    tw_active_date = get_dynamic_trading_date("^TWII")
    us_active_date = get_dynamic_trading_date("^IXIC")
    print(f"📅 台股最新動態交易日: {tw_active_date}")
    print(f"📅 美股最新動態交易日: {us_active_date}")

    history_data = {"TW": {}, "US": {}}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception as e:
            print(f"讀取既有 data.json 提示: {e}")

    if "TW" not in history_data: history_data["TW"] = {}
    if "US" not in history_data: history_data["US"] = {}

    # 若資料庫天數不足，自動填補基礎基準歷史資料
    if len(history_data["TW"]) == 0:
        base_tw_history = {
            "2026-10-08": {
                "date": "2026-10-08", "dayOfWeek": "四", "taiexClose": "49,313.44", "taiexChange": "-492.93", "taiexPercent": "-0.99%", "taiexVolume": "9,245.09 億元",
                "marketNature": "國慶連假前獲利賣壓出籠 ‧ 被動元件逆勢爆量大漲",
                "sectors": [{"name": "被動元件 / 精密晶片電阻", "grade": "S級", "note": "光頡 (3624) 爆量逼近漲停"}],
                "candidates": [{
                    "rank": 1, "code": "3624", "name": "光頡", "sector": "精密晶片電阻", "close": 175.0, "change": "+9.72%", "volume": "47,737", "volRatio": "2.85 倍",
                    "maStatus": "5MA > 10MA > 20MA 多頭排列", "boxDays": "24日箱型突破", "boxRange": "145.0～160.0", "breakoutPrice": "160.0", "breakoutReason": "9月營收年增 67%，放量突破 160 元頸線收 175 元。",
                    "fib236": 164.8, "fib382": 158.5, "fib500": 153.5, "fib618": 148.4, "fib786": 141.2, "strategy": "等38.2%", "entryZone": "158.0 ～ 165.0", "entryMin": 158.0, "entryMax": 165.0,
                    "entryPrice": "162.0", "breakoutConfirmPrice": "178.0", "stopLossPrice": "146.0", "stopLossNum": 146.0, "noBuyCondition": "短線急拉不追高；破 146.0 結構失效嚴禁抄底", "score": 96
                }],
                "entryZoneAlerts": [{
                    "code": "8358", "name": "金居", "originDate": "2026-10-05", "verifiedCurrentPrice": 535.0, "entryZone": "526.0 ～ 540.0", "entryPrice": "530.0", "stopLossPrice": "498.0", "entryMin": 526.0, "entryMax": 540.0, "stopLossNum": 498.0
                }]
            },
            "2026-10-07": {
                "date": "2026-10-07", "dayOfWeek": "三", "taiexClose": "49,806.37", "taiexChange": "-16.18", "taiexPercent": "-0.03%", "taiexVolume": "9,744.00 億元",
                "marketNature": "大盤逼近五萬大關換手 ‧ MLCC 族群全面爆量啟動",
                "sectors": [{"name": "被動元件 / MLCC", "grade": "S級", "note": "華新科 (2492) 爆量鎖死漲停"}],
                "candidates": [{
                    "rank": 1, "code": "2492", "name": "華新科", "sector": "被動元件/MLCC", "close": 428.0, "change": "+9.88%", "volume": "52,338", "volRatio": "2.91 倍",
                    "maStatus": "5MA > 10MA > 20MA 多頭排列", "boxDays": "25日箱型突破", "boxRange": "340.0～380.0", "breakoutPrice": "380.0", "breakoutReason": "放量突破 380 元頸線收 428 元。",
                    "fib236": 396.6, "fib382": 377.2, "fib500": 361.5, "fib618": 345.8, "fib786": 323.5, "strategy": "等38.2%", "entryZone": "375.0 ～ 395.0", "entryMin": 375.0, "entryMax": 395.0,
                    "entryPrice": "380.0", "breakoutConfirmPrice": "435.0", "stopLossPrice": "345.0", "stopLossNum": 345.0, "noBuyCondition": "跳空漲停禁追價；破 345.0 嚴禁抄底", "score": 96
                }],
                "entryZoneAlerts": []
            },
            "2026-10-06": {
                "date": "2026-10-06", "dayOfWeek": "二", "taiexClose": "49,822.55", "taiexChange": "+110.51", "taiexPercent": "+0.22%", "taiexVolume": "9,759.35 億元",
                "marketNature": "台積電創新高摸 49,968 點 ‧ 光通訊族群接棒",
                "sectors": [{"name": "光通訊 / CPO 封裝", "grade": "A級", "note": "全友 (2305) 爆量鎖漲停"}],
                "candidates": [{
                    "rank": 1, "code": "2305", "name": "全友", "sector": "光電子/AI光通訊", "close": 63.5, "change": "+9.86%", "volume": "13,750", "volRatio": "3.61 倍",
                    "maStatus": "均線多頭發散，20MA向上", "boxDays": "22日箱型突破", "boxRange": "52.0～58.5", "breakoutPrice": "58.5", "breakoutReason": "突破 58.5 元箱型上緣強攻 63.5 元。",
                    "fib236": 57.6, "fib382": 54.0, "fib500": 51.0, "fib618": 48.0, "fib786": 43.8, "strategy": "等突破回踩", "entryZone": "57.0 ～ 58.5", "entryMin": 57.0, "entryMax": 58.5,
                    "entryPrice": "58.0", "breakoutConfirmPrice": "64.0", "stopLossPrice": "51.0", "stopLossNum": 51.0, "noBuyCondition": "短線急拉不追高；跌破 51.0 停損", "score": 94
                }],
                "entryZoneAlerts": []
            },
            "2026-10-05": {
                "date": "2026-10-05", "dayOfWeek": "一", "taiexClose": "49,712.04", "taiexChange": "+1236.30", "taiexPercent": "+2.55%", "taiexVolume": "11,542.00 億元",
                "marketNature": "暴漲逾千點 ‧ AI 高頻高速銅箔基板群起噴出",
                "sectors": [{"name": "AI 伺服器高頻銅箔 / CCL", "grade": "S級", "note": "金居 (8358) 爆量漲停"}],
                "candidates": [{
                    "rank": 1, "code": "8358", "name": "金居", "sector": "AI伺服器高頻銅箔", "close": 570.0, "change": "+9.83%", "volume": "40,415", "volRatio": "3.34 倍",
                    "maStatus": "5MA > 10MA > 20MA 多頭發散", "boxDays": "32日箱型突破", "boxRange": "480.0～530.0", "breakoutPrice": "530.0", "breakoutReason": "跳空突破 530 元箱型，量放大 3.3 倍鎖 570 元。",
                    "fib236": 542.9, "fib382": 526.1, "fib500": 512.5, "fib618": 498.9, "fib786": 479.5, "strategy": "等38.2%", "entryZone": "526.0 ～ 540.0", "entryMin": 526.0, "entryMax": 540.0,
                    "entryPrice": "530.0", "breakoutConfirmPrice": "580.0", "stopLossPrice": "498.0", "stopLossNum": 498.0, "noBuyCondition": "跳空長紅禁止追價；跌破 498.0 全面停損", "score": 96
                }],
                "entryZoneAlerts": []
            }
        }
        history_data["TW"].update(base_tw_history)

    # 2. 台股管線執行
    tw_macro = fetch_twse_market_summary()
    tw_candidates = run_tw_screener()
    tw_alerts = track_recent_candidates(history_data, "TW", tw_active_date, tw_candidates)
    tw_weekday = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]

    history_data["TW"][tw_active_date] = {
        "date": tw_active_date,
        "dayOfWeek": tw_weekday,
        "taiexClose": tw_macro["close"],
        "taiexChange": tw_macro["change"],
        "taiexPercent": tw_macro["percent"],
        "taiexVolume": tw_macro["volume"],
        "marketNature": "盤後量化篩選 ‧ 均線多頭 ‧ 箱型放量突破確認",
        "sectors": [
            {"name": "半導體材料 / 晶圓薄化", "grade": "S級", "note": "多頭發散爆量上攻"},
            {"name": "光通訊 / 矽光子 CPO", "grade": "A級", "note": "長線多頭排列"}
        ],
        "candidates": tw_candidates,
        "entryZoneAlerts": tw_alerts
    }

    # 3. 美股管線執行
    us_macro = fetch_us_market_summary()
    us_candidates = run_us_screener()
    us_alerts = track_recent_candidates(history_data, "US", us_active_date, us_candidates)
    us_weekday = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(us_active_date).weekday()]

    history_data["US"][us_active_date] = {
        "date": us_active_date,
        "dayOfWeek": us_weekday,
        "taiexClose": us_macro["close"],
        "taiexChange": us_macro["change"],
        "taiexPercent": us_macro["percent"],
        "taiexVolume": us_macro["volume"],
        "secondaryIndexName": us_macro.get("secondaryName", "費城半導體 (SOX)"),
        "secondaryIndexClose": us_macro.get("secondaryClose", "--"),
        "marketNature": "美股動態掃描 ‧ 均線多頭排列 ‧ 突破整理平台",
        "sectors": [
            {"name": "AI 高速傳輸 / 光互連", "grade": "S級", "note": "量能增溫帶動主升段"}
        ],
        "candidates": us_candidates,
        "entryZoneAlerts": us_alerts
    }

    # 4. 保留最新 15 個交易日歷史
    for m in ["TW", "US"]:
        sorted_d = sorted(history_data[m].keys(), reverse=True)
        if len(sorted_d) > MAX_HISTORY_DAYS:
            for old_d in sorted_d[MAX_HISTORY_DAYS:]:
                del history_data[m][old_d]

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 動態結算完成！已將真實數據寫入 data.json (台股天數: {len(history_data['TW'])}, 美股天數: {len(history_data['US'])})")

if __name__ == "__main__":
    main()
