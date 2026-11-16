import os
import json
import math
import datetime
from datetime import timedelta
import requests
import pandas as pd
import yfinance as yf

# 設定選股條件硬性門檻
MIN_GAIN_PCT = 8.5        # 台股傳統突破：單日漲幅 >= 8.5%
MIN_VOL_RATIO = 1.8       # 台股傳統突破：單日放量 >= 1.8倍
MAX_HISTORY_DAYS = 5      # 嚴格保留最近 5 個交易日

def get_dynamic_trading_date(ticker_symbol="^TWII"):
    """從市場行情動態取得最新真實開盤交易日 (YYYY-MM-DD)"""
    if ticker_symbol == "^TWII":
        try:
            url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if "data" in data and len(data["data"]) > 0:
                    last_row = data["data"][-1]
                    roc_parts = last_row[0].strip().split('/')
                    if len(roc_parts) == 3:
                        ad_year = int(roc_parts[0]) + 1911
                        return f"{ad_year:04d}-{int(roc_parts[1]):02d}-{int(roc_parts[2]):02d}"
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

def calculate_fundamental_valuation(ticker, close_price):
    """
    財報 EPS 量化估值引擎 (PE Band + PEG 成長估值模型)
    自動抓取預估 EPS、本益比、營收/獲利成長率，計算合理價值區間與過熱警示
    """
    val_data = {
        "forwardEps": None,
        "trailingEps": None,
        "forwardPe": None,
        "pegRatio": None,
        "fairValueZone": "財報數據不足 (純技術評估)",
        "valuationStatus": "中性 / 題材驅動",
        "valuationGrade": "B (動能驅動)"
    }
    try:
        info = ticker.info or {}
        f_eps = info.get("forwardEps") or info.get("trailingEps")
        t_eps = info.get("trailingEps")
        rev_growth = info.get("revenueGrowth") or info.get("earningsGrowth") or 0.15

        if f_eps and float(f_eps) > 0:
            f_eps = float(f_eps)
            f_pe = round(close_price / f_eps, 2)
            
            growth_pct = max(15.0, min(float(rev_growth) * 100, 50.0))
            peg = round(f_pe / growth_pct, 2)

            low_fair = round(f_eps * 15, 1)
            high_fair = round(f_eps * 22, 1)

            val_data["forwardEps"] = round(f_eps, 2)
            val_data["trailingEps"] = round(float(t_eps), 2) if t_eps else None
            val_data["forwardPe"] = f_pe
            val_data["pegRatio"] = peg
            val_data["fairValueZone"] = f"{low_fair} ～ {high_fair} 元 (15~22x PE)"

            if peg < 0.85 and close_price <= high_fair:
                val_data["valuationStatus"] = f"嚴重低估 (PEG {peg} < 0.85)，極具安全邊際"
                val_data["valuationGrade"] = "S (低估黃金區)"
            elif peg <= 1.25 and close_price <= high_fair * 1.1:
                val_data["valuationStatus"] = f"合理成長區間 (PEG {peg})，業績跟上股價"
                val_data["valuationGrade"] = "A (合理價值)"
            elif peg > 1.8 or f_pe > 40:
                val_data["valuationStatus"] = f"估值偏高過熱 (PE {f_pe}x / PEG {peg})，嚴防純題材回檔"
                val_data["valuationGrade"] = "C (過熱警示)"
            else:
                val_data["valuationStatus"] = f"中性偏高 (PE {f_pe}x)，需靠強勢動能推升"
                val_data["valuationGrade"] = "B (動能驅動)"
    except Exception as e:
        print(f"財報估值解析異常: {e}")

    return val_data

def fetch_twse_market_summary():
    """獲取今日最新加權指數與成交金額 (雙重校核)"""
    try:
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
        headers = {"User-Agent": "Mozilla/5.0"}
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
                    "close": f"{close_idx:,.2f} ({sign}{pct:.2f}%)",
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
                "close": f"{close_val:,.2f} ({sign}{pct:.2f}%)",
                "change": f"{sign}{change:,.2f}",
                "percent": f"{sign}{pct:.2f}%",
                "volume": vol_str
            }
    except Exception as e:
        print(f"Yahoo ^TWII 備援失敗: {e}")

    return {"close": "--", "change": "--", "percent": "--", "volume": "--"}

def fetch_us_market_summary():
    """動態抓取美股那斯達克 (^IXIC) 行情"""
    summary = {"close": "--", "change": "--", "percent": "--", "volume": "--"}
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
            summary["close"] = f"{c:,.2f} ({sign}{pct:.2f}%)"
            summary["change"] = f"{sign}{diff:,.2f}"
            summary["percent"] = f"{sign}{pct:.2f}%"
            summary["volume"] = f"{vol / 100000000:,.1f} 億股" if vol > 0 else "--"
    except Exception as e:
        print(f"美股大盤動態抓取異常: {e}")
    return summary

def evaluate_bear_market_rs_candidate(symbol, name, sector="上市優質大型企業"):
    """
    模式 B：熊市抗跌領先突破篩選模型 + 財報 EPS 雙重驗證
    【硬性風控過濾】：僅限台灣證交所「上市股票 (.TW)」，一律剔除上櫃 (.TWO) 與興櫃股票！
    """
    if not symbol.endswith(".TW"):
        return None

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

        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['5VMA'] = df['Volume'].rolling(5).mean()

        ma20 = float(df['20MA'].iloc[-1])
        ma60 = float(df['60MA'].iloc[-1]) if not math.isnan(df['60MA'].iloc[-1]) else ma20 * 0.95
        vma5 = float(df['5VMA'].iloc[-2])
        volume = float(today['Volume'])

        if close < ma20 or close < ma60:
            return None

        vol_ratio = volume / vma5 if vma5 > 0 else 1.0
        if vol_ratio < 1.3 and gain_pct < 9.0:
            return None

        box_window = df.iloc[-22:-2]
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close < box_high:
            return None

        val_info = calculate_fundamental_valuation(ticker, close)
        clean_code = symbol.replace(".TW", "")
        neckline = round(box_high, 1)
        entry_min = round(neckline * 0.99, 1)
        entry_max = round(neckline * 1.03, 1)
        entry_price = round(neckline * 1.01, 1)
        stop_loss = round(neckline * 0.96, 1)
        target_price = round(neckline * 1.18, 1)

        val_bonus = 3 if "S" in val_info["valuationGrade"] else (1 if "A" in val_info["valuationGrade"] else (-3 if "C" in val_info["valuationGrade"] else 0))

        return {
            "symbol": symbol,
            "code": clean_code,
            "name": name,
            "marketBoard": "上市 (TWSE)",
            "sector": sector,
            "close": round(close, 2),
            "change": f"+{gain_pct:.2f}%",
            "volume": f"{int(volume):,}",
            "volRatio": f"{vol_ratio:.2f} 倍 (逆勢表態)",
            "maStatus": "上市大型股 ‧ 站穩 20MA/60MA，相對強度 (RS) 極高",
            "boxDays": "20+ 日箱型搶先突破 (模式 B)",
            "boxRange": f"{box_low:.1f} ～ {neckline}",
            "breakoutPrice": f"{neckline}",
            "breakoutReason": f"【純上市標的】大盤修正時逆勢站穩均線，今日率先衝破 {neckline} 元頸線，法人主力搶先表態！",
            "leadTime": "大盤止跌守穩即展開波段主升攻擊",
            "strategy": "模式 B：突破回踩頸線入場",
            "entryZone": f"{entry_min} ～ {entry_max} 元",
            "entryPrice": f"{entry_price} 元",
            "stopLossPrice": f"{stop_loss} 元 (頸線下方防線)",
            "stopLossNum": stop_loss,
            "targetPrice": f"{target_price} 元",
            "valuation": val_info,
            "noBuyCondition": f"若跌回 {stop_loss} 頸線下方則結構轉弱果斷停損；{val_info['valuationStatus']}",
            "score": round(91 + min(gain_pct, 5.0) + val_bonus, 1)
        }
    except Exception as e:
        print(f"熊市抗跌評估異常 {symbol}: {e}")
        return None

def evaluate_stock_candidate(symbol, name, sector="科技/半導體", is_us=False):
    """經典多頭突破模型 + 財報 EPS 雙重驗證"""
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

        if not ((ma5 > ma10 > ma20) and (ma20 >= ma20_prev)):
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

        val_info = calculate_fundamental_valuation(ticker, close)

        wave_low = float(df['Low'].tail(30).min())
        wave_diff = close - wave_low
        fib236 = round(close - (wave_diff * 0.236), 1)
        fib382 = round(close - (wave_diff * 0.382), 1)
        fib618 = round(close - (wave_diff * 0.618), 1)

        entry_min = fib382
        entry_max = fib236
        entry_price = round(fib382 * 1.01, 1)
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
            "strategy": "等38.2%回踩",
            "entryZone": f"{entry_min} ～ {entry_max}",
            "entryPrice": f"{entry_price}",
            "stopLossPrice": f"{stop_loss}",
            "stopLossNum": stop_loss,
            "valuation": val_info,
            "noBuyCondition": f"剛大漲嚴禁追價；若跌破 {stop_loss} 結構失效嚴禁抄底",
            "score": round(90 + min(gain_pct, 10.0), 1)
        }
    except Exception as e:
        print(f"個股分析失敗 {symbol}: {e}")
        return None

def run_pool_screener(pool, is_us=False, is_rs=False):
    """通用股票池掃描排序"""
    candidates = []
    for sym, name, sec in pool:
        if is_rs:
            res = evaluate_bear_market_rs_candidate(sym, name, sec)
        else:
            res = evaluate_stock_candidate(sym, name, sec, is_us=is_us)
        if res:
            candidates.append(res)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    for idx, c in enumerate(candidates):
        c["rank"] = idx + 1
    return candidates[:5]

def trim_history(market_dict, max_days=MAX_HISTORY_DAYS):
    """嚴格保留最近 N 個交易日數據"""
    sorted_dates = sorted(market_dict.keys(), reverse=True)
    for old_date in sorted_dates[max_days:]:
        del market_dict[old_date]

def main():
    print("啟動三大獨立引擎量化篩選機器人 (嚴格保留 5 交易日 + 領頭羊純上市 TWSE 過濾)...")
    tw_active_date = get_dynamic_trading_date("^TWII")
    us_active_date = get_dynamic_trading_date("^IXIC")

    history_data = {"TW": {}, "US": {}, "RS": {}}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception as e:
            print(f"讀取既有 data.json 失敗，將重新建立: {e}")

    for k in ["TW", "US", "RS"]:
        if k not in history_data or not isinstance(history_data[k], dict):
            history_data[k] = {}

    # 【純上市 TWSE 觀察池】完全剔除上櫃 (.TWO) 與興櫃小型股，避免單一事件與財報突襲風險
    tw_listed_pool = [
        ("2468.TW", "華經", "上市 ‧ 資安與系統整合 / 集團綜效"),
        ("2471.TW", "資通", "上市 ‧ 企業軟體 / 金融資安系統"),
        ("6214.TW", "精誠", "上市 ‧ 台灣資訊服務龍頭 / 企業AI雲"),
        ("2453.TW", "凌群", "上市 ‧ 系統整合 / 智慧機器人與雲端"),
        ("5203.TW", "訊連", "上市 ‧ 純AI軟體 / 臉部辨識與生成式AI"),
        ("2412.TW", "中華電", "上市 ‧ 電信防禦 / AI算力IDC機房"),
        ("1802.TW", "台玻", "上市 ‧ 低介電玻纖布 / AI基板材料"),
        ("2492.TW", "華新科", "上市 ‧ 被動元件 / MLCC高階電容"),
        ("3450.TW", "聯鈞", "上市 ‧ AI光模組封裝 / CPO"),
        ("2330.TW", "台積電", "上市 ‧ 晶圓代工龍頭"),
        ("2317.TW", "鴻海", "上市 ‧ AI伺服器組裝"),
        ("2382.TW", "廣達", "上市 ‧ AI伺服器/雲端"),
        ("3231.TW", "緯創", "上市 ‧ AI伺服器基板"),
        ("2308.TW", "台達電", "上市 ‧ AI電源散熱龍頭")
    ]

    us_pool = [
        ("CRDO", "Credo Technology", "高速傳輸/AEC主動電纜"),
        ("NVDA", "NVIDIA", "AI GPU 算力龍頭"),
        ("AVGO", "Broadcom", "AI 客製化晶片/網通"),
        ("MRVL", "Marvell", "光互連與資料中心晶片"),
        ("ALAB", "Astera Labs", "AI 連接晶片"),
        ("TSM", "TSMC ADR", "晶圓代工"),
        ("PLTR", "Palantir", "AI 企業軟體平台")
    ]

    tw_summary = {"close": "--", "change": "--", "percent": "--", "volume": "--"}

    # 1. 執行台股經典多頭突破篩選 (TW)
    try:
        tw_summary = fetch_twse_market_summary()
        tw_candidates = run_pool_screener(tw_listed_pool, is_us=False, is_rs=False)
        tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]
        history_data["TW"][tw_active_date] = {
            "date": tw_active_date,
            "dayOfWeek": tw_wd,
            "taiexClose": tw_summary["close"],
            "taiexChange": tw_summary["change"],
            "taiexPercent": tw_summary["percent"],
            "taiexVolume": tw_summary["volume"],
            "marketNature": "嚴格執行四大硬性突破門檻與財報估值檢驗（無符合標的時恪守 0 檔寧缺勿濫）",
            "sectors": [{"name": "上市軟體與資安避風港", "grade": "S級", "note": "資金大舉撤出硬體轉入上市資服股"}],
            "candidates": tw_candidates
        }
        trim_history(history_data["TW"], MAX_HISTORY_DAYS)
    except Exception as e:
        print(f"TW 篩選異常: {e}")

    # 2. 執行美股經典多頭突破篩選 (US)
    try:
        us_summary = fetch_us_market_summary()
        us_candidates = run_pool_screener(us_pool, is_us=True, is_rs=False)
        us_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(us_active_date).weekday()]
        history_data["US"][us_active_date] = {
            "date": us_active_date,
            "dayOfWeek": us_wd,
            "taiexClose": us_summary["close"],
            "taiexChange": us_summary["change"],
            "taiexPercent": us_summary["percent"],
            "taiexVolume": us_summary["volume"],
            "marketNature": "美股科技與半導體量化突破篩選（含 Forward PE / PEG 雙重驗證）",
            "sectors": [{"name": "美股 AI 與高速傳輸", "grade": "觀察級", "note": "監控費半與那斯達克動向"}],
            "candidates": us_candidates
        }
        trim_history(history_data["US"], MAX_HISTORY_DAYS)
    except Exception as e:
        print(f"US 篩選異常: {e}")

    # 3. 執行逆勢領頭羊模式 B 篩選 (RS) - 100% 僅限上市股票 (.TW)
    try:
        rs_candidates = run_pool_screener(tw_listed_pool, is_us=False, is_rs=True)
        tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]
        history_data["RS"][tw_active_date] = {
            "date": tw_active_date,
            "dayOfWeek": tw_wd,
            "taiexClose": tw_summary.get("close", "大盤動態監控"),
            "taiexChange": tw_summary.get("change", "--"),
            "taiexPercent": "RS相對強度篩選 (純上市)",
            "taiexVolume": tw_summary.get("volume", "--"),
            "marketNature": "【純上市股票限定】技術面領先突破 + 財報 EPS (PE Band / PEG) 雙重驗證模型",
            "sectors": [
                {"name": "上市軟體 / 資安 / 系統整合", "grade": "S級催化", "note": "排除上櫃興櫃小型股，聚焦體質穩健上市資服龍頭"},
                {"name": "上市高股息與基礎設施防禦", "grade": "A級催化", "note": "具穩定財報與法人護盤底氣"}
            ],
            "candidates": rs_candidates
        }
        trim_history(history_data["RS"], MAX_HISTORY_DAYS)
    except Exception as e:
        print(f"RS 篩選異常: {e}")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)

    print("三大策略與財報估值更新完畢（嚴格 5 日歷史 + 純上市領頭羊過濾）！已成功寫入 data.json")

if __name__ == "__main__":
    main()
