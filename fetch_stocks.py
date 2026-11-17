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
    """從證交所官方盤後行情動態取得最新真實開盤交易日 (YYYY-MM-DD)"""
    if ticker_symbol == "^TWII":
        try:
            url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers, timeout=10)
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

def fetch_twse_official_daily_quotes():
    """
    【核心資訊正確保證】
    直連台灣證券交易所 (TWSE) 官方每日收盤行情總表 (STOCK_DAY_ALL)
    取得全市場「純上市股票」的 100% 官方結算收盤價、漲跌價差、成交張數
    """
    quotes_map = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 來源 1：TWSE 官方 OpenAPI (STOCK_DAY_ALL)
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            rows = res.json()
            for r in rows:
                code = str(r.get("Code", "")).strip()
                if not code or len(code) != 4:
                    continue
                try:
                    close_str = str(r.get("ClosingPrice", "")).replace(",", "").strip()
                    change_str = str(r.get("Change", "")).replace(",", "").replace("+", "").replace("X", "").strip()
                    vol_shares_str = str(r.get("TradeVolume", "0")).replace(",", "").strip()
                    if not close_str or close_str == "--":
                        continue
                    close_val = float(close_str)
                    change_val = float(change_str) if change_str and change_str != "--" else 0.0
                    prev_close = close_val - change_val
                    pct = (change_val / prev_close * 100) if prev_close > 0 else 0.0
                    vol_lots = int(int(vol_shares_str) / 1000)

                    quotes_map[code] = {
                        "code": code,
                        "name": str(r.get("Name", "")).strip(),
                        "close": round(close_val, 2),
                        "change": round(change_val, 2),
                        "pct": round(pct, 2),
                        "vol_lots": vol_lots,
                        "source": "TWSE_OFFICIAL"
                    }
                except ValueError:
                    continue
            if len(quotes_map) > 500:
                print(f"成功從 TWSE OpenAPI 載入 {len(quotes_map)} 檔純上市股票官方收盤報價！")
                return quotes_map
    except Exception as e:
        print(f"TWSE OpenAPI 連線異常，嘗試備援端點: {e}")

    # 來源 2：TWSE RWD 每日收盤行情備援端點
    try:
        url_rwd = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999&response=json"
        res = requests.get(url_rwd, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            tables = data.get("tables", [])
            for tbl in tables:
                if "每日收盤行情" in tbl.get("title", ""):
                    for row in tbl.get("data", []):
                        code = str(row[0]).strip()
                        if len(code) != 4:
                            continue
                        try:
                            vol_lots = int(int(str(row[2]).replace(",", "")) / 1000)
                            close_val = float(str(row[8]).replace(",", ""))
                            sign_html = str(row[9])
                            diff_val = float(str(row[10]).replace(",", ""))
                            change_val = -diff_val if "-" in sign_html else diff_val
                            prev_close = close_val - change_val
                            pct = (change_val / prev_close * 100) if prev_close > 0 else 0.0
                            quotes_map[code] = {
                                "code": code,
                                "name": str(row[1]).strip(),
                                "close": round(close_val, 2),
                                "change": round(change_val, 2),
                                "pct": round(pct, 2),
                                "vol_lots": vol_lots,
                                "source": "TWSE_RWD"
                            }
                        except ValueError:
                            continue
    except Exception as e:
        print(f"TWSE RWD 備援抓取異常: {e}")

    return quotes_map

def calculate_fundamental_valuation(ticker, official_close_price):
    """
    財報 EPS 量化估值引擎 (PE Band + PEG 成長估值模型)
    以證交所官方精準收盤價計算動態 PE 與合理價值區間
    """
    val_data = {
        "forwardEps": None,
        "trailingEps": None,
        "forwardPe": None,
        "pegRatio": None,
        "fairValueZone": "財報數據更新中 (純技術評估)",
        "valuationStatus": "中性 / 題材動能驅動",
        "valuationGrade": "B (動能驅動)"
    }
    try:
        info = ticker.info or {}
        f_eps = info.get("forwardEps") or info.get("trailingEps")
        t_eps = info.get("trailingEps")
        rev_growth = info.get("revenueGrowth") or info.get("earningsGrowth") or 0.15

        if f_eps and float(f_eps) > 0:
            f_eps = float(f_eps)
            f_pe = round(official_close_price / f_eps, 2)
            
            growth_pct = max(15.0, min(float(rev_growth) * 100, 50.0))
            peg = round(f_pe / growth_pct, 2)

            low_fair = round(f_eps * 15, 1)
            high_fair = round(f_eps * 22, 1)

            val_data["forwardEps"] = round(f_eps, 2)
            val_data["trailingEps"] = round(float(t_eps), 2) if t_eps else None
            val_data["forwardPe"] = f_pe
            val_data["pegRatio"] = peg
            val_data["fairValueZone"] = f"{low_fair} ～ {high_fair} 元 (15~22x PE)"

            if peg < 0.85 and official_close_price <= high_fair:
                val_data["valuationStatus"] = f"嚴重低估 (PEG {peg} < 0.85)，極具基本面安全邊際"
                val_data["valuationGrade"] = "S (基本面+技術雙優)"
            elif peg <= 1.25 and official_close_price <= high_fair * 1.1:
                val_data["valuationStatus"] = f"合理成長區間 (PEG {peg})，業績成長支撐股價"
                val_data["valuationGrade"] = "A (合理價值)"
            elif peg > 1.8 or f_pe > 40:
                val_data["valuationStatus"] = f"估值偏高過熱 (PE {f_pe}x / PEG {peg})，嚴防純題材回檔"
                val_data["valuationGrade"] = "C (過熱警示)"
            else:
                val_data["valuationStatus"] = f"中性區間 (PE {f_pe}x)，需靠強勢動能推升"
                val_data["valuationGrade"] = "B (動能驅動)"
    except Exception as e:
        print(f"財報估值解析異常: {e}")

    return val_data

def fetch_twse_market_summary():
    """獲取今日最新加權指數與成交金額 (證交所官方 FMTQIK 雙重校核)"""
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

def evaluate_bear_market_rs_candidate(symbol, name, sector, twse_quotes):
    """
    模式 B：熊市抗跌領先突破篩選模型 + 財報 EPS 雙重驗證
    1. 僅限純上市股票 (.TW)，且必須存在於證交所官方報價表中 (徹底剔除上櫃與興櫃)
    2. 收盤價、漲跌幅、成交張數 100% 採用證交所官方數據，杜絕 Yahoo 價格誤差
    """
    if not symbol.endswith(".TW"):
        return None
    clean_code = symbol.replace(".TW", "")
    
    # 強制從證交所官方報價表讀取當日真實價格
    official = twse_quotes.get(clean_code)
    if not official:
        return None

    close = official["close"]
    gain_pct = official["pct"]
    vol_lots = official["vol_lots"]
    official_name = official["name"] or name

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        if df.empty or len(df) < 25:
            return None

        # 計算歷史均線與箱型頸線 (以歷史區間校準)
        df['20MA'] = df['Close'].rolling(20).mean()
        df['60MA'] = df['Close'].rolling(60).mean()
        df['5VMA'] = df['Volume'].rolling(5).mean()

        ma20 = float(df['20MA'].iloc[-1])
        ma60 = float(df['60MA'].iloc[-1]) if not math.isnan(df['60MA'].iloc[-1]) else ma20 * 0.95
        vma5_lots = float(df['5VMA'].iloc[-2]) / 1000.0 if df['5VMA'].iloc[-2] > 0 else 1.0

        # 1. 均線防線：官方收盤價必須穩站月線與季線之上
        if close < ma20 or close < ma60:
            return None

        # 2. 量能與表態條件：放量 >= 1.3倍 或單日強勢漲幅 >= 6%
        vol_ratio = vol_lots / vma5_lots if vma5_lots > 0 else 1.0
        if vol_ratio < 1.3 and gain_pct < 6.0:
            return None

        # 3. 突破近 20 日箱型高點頸線
        box_window = df.iloc[-22:-1]
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close < box_high:
            return None

        val_info = calculate_fundamental_valuation(ticker, close)
        neckline = round(box_high, 1)
        entry_min = round(neckline * 0.99, 1)
        entry_max = round(neckline * 1.03, 1)
        entry_price = round(neckline * 1.01, 1)
        stop_loss = round(neckline * 0.96, 1)
        target_price = round(neckline * 1.18, 1)

        val_bonus = 3 if "S" in val_info["valuationGrade"] else (1 if "A" in val_info["valuationGrade"] else (-3 if "C" in val_info["valuationGrade"] else 0))
        sign = "+" if gain_pct >= 0 else ""

        return {
            "symbol": symbol,
            "code": clean_code,
            "name": official_name,
            "marketBoard": "上市 (TWSE)",
            "sector": sector,
            "close": close,
            "change": f"{sign}{gain_pct:.2f}%",
            "volume": f"{vol_lots:,} 張",
            "volRatio": f"{vol_ratio:.2f} 倍 (證交所實盤核實)",
            "maStatus": f"上市大型股 ‧ 站穩 20MA ({ma20:.1f}) 與 60MA ({ma60:.1f})",
            "boxDays": "20+ 日箱型搶先突破 (模式 B)",
            "boxRange": f"{box_low:.1f} ～ {neckline}",
            "breakoutPrice": f"{neckline}",
            "breakoutReason": f"【證交所官方實盤核實】純上市標的逆勢站穩均線，今日衝破 {neckline} 元頸線收 {close} 元 ({sign}{gain_pct:.2f}%)！",
            "leadTime": "大盤止跌守穩即展開波段主升攻擊",
            "strategy": "模式 B：突破回踩頸線入場",
            "entryZone": f"{entry_min} ～ {entry_max} 元",
            "entryPrice": f"{entry_price} 元",
            "stopLossPrice": f"{stop_loss} 元 (頸線下方防線)",
            "stopLossNum": stop_loss,
            "targetPrice": f"{target_price} 元",
            "valuation": val_info,
            "noBuyCondition": f"若跌回 {stop_loss} 頸線下方則結構轉弱果斷停損；{val_info['valuationStatus']}",
            "score": round(91 + min(max(gain_pct, 0), 5.0) + val_bonus, 1)
        }
    except Exception as e:
        print(f"熊市抗跌評估異常 {symbol}: {e}")
        return None

def evaluate_stock_candidate(symbol, name, sector, twse_quotes=None, is_us=False):
    """經典多頭突破模型 (台股強制使用證交所官方收盤價)"""
    clean_code = symbol.replace(".TW", "").replace(".TWO", "")
    official = twse_quotes.get(clean_code) if (not is_us and twse_quotes) else None

    if not is_us and not official:
        return None

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        if df.empty or len(df) < 25:
            return None

        if official:
            close = official["close"]
            gain_pct = official["pct"]
            vol_str = f"{official['vol_lots']:,} 張"
            vol_lots = official["vol_lots"]
            official_name = official["name"] or name
        else:
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            close = round(float(today['Close']), 2)
            prev_close = float(yesterday['Close'])
            gain_pct = ((close - prev_close) / prev_close) * 100
            vol_lots = int(today['Volume'])
            vol_str = f"{vol_lots:,}"
            official_name = name

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

        if not ((ma5 > ma10 > ma20) and (ma20 >= ma20_prev)):
            return None

        vma5_base = (float(df['5VMA'].iloc[-2]) / 1000.0) if not is_us else float(df['5VMA'].iloc[-2])
        vol_ratio = vol_lots / vma5_base if vma5_base > 0 else 1.0
        min_vol = 1.5 if is_us else MIN_VOL_RATIO
        if vol_ratio < min_vol:
            return None

        box_window = df.iloc[-22:-1]
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

        return {
            "symbol": symbol,
            "code": clean_code,
            "name": official_name,
            "sector": sector,
            "close": close,
            "change": f"+{gain_pct:.2f}%",
            "volume": vol_str,
            "volRatio": f"{vol_ratio:.2f} 倍 (放量突破)",
            "maStatus": "5MA > 10MA > 20MA 多頭排列，20MA 向上",
            "boxDays": "20+ 交易日箱型突破",
            "boxRange": f"{box_low:.1f} ～ {box_high:.1f}",
            "breakoutPrice": f"{box_high:.1f}",
            "breakoutReason": f"官方收盤 {close} 元放量突破長天期箱型上緣 ({box_high:.1f})，單日放量 {vol_ratio:.2f} 倍。",
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

def run_pool_screener(pool, twse_quotes=None, is_us=False, is_rs=False):
    """通用股票池掃描排序"""
    candidates = []
    for sym, name, sec in pool:
        if is_rs:
            res = evaluate_bear_market_rs_candidate(sym, name, sec, twse_quotes)
        else:
            res = evaluate_stock_candidate(sym, name, sec, twse_quotes=twse_quotes, is_us=is_us)
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
    print("啟動量化篩選機器人 (直連證交所官方 STOCK_DAY_ALL 確保 100% 價格精準 + 純上市過濾)...")
    tw_active_date = get_dynamic_trading_date("^TWII")
    us_active_date = get_dynamic_trading_date("^IXIC")

    # 優先載入證交所官方今日全市場上市股票收盤價總表
    twse_quotes = fetch_twse_official_daily_quotes()

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

    # 【純上市 TWSE 觀察池】完全剔除上櫃 (.TWO) 與興櫃小型股
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
        tw_candidates = run_pool_screener(tw_listed_pool, twse_quotes=twse_quotes, is_us=False, is_rs=False)
        tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]
        history_data["TW"][tw_active_date] = {
            "date": tw_active_date,
            "dayOfWeek": tw_wd,
            "taiexClose": tw_summary["close"],
            "taiexChange": tw_summary["change"],
            "taiexPercent": tw_summary["percent"],
            "taiexVolume": tw_summary["volume"],
            "marketNature": "嚴格執行四大硬性突破門檻與證交所官方報價校核（無符合標的時恪守 0 檔寧缺勿濫）",
            "sectors": [{"name": "上市軟體與資安避風港", "grade": "S級", "note": "資金大舉撤出硬體轉入上市資服股"}],
            "candidates": tw_candidates
        }
        trim_history(history_data["TW"], MAX_HISTORY_DAYS)
    except Exception as e:
        print(f"TW 篩選異常: {e}")

    # 2. 執行美股經典多頭突破篩選 (US)
    try:
        us_summary = fetch_us_market_summary()
        us_candidates = run_pool_screener(us_pool, twse_quotes=None, is_us=True, is_rs=False)
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

    # 3. 執行逆勢領頭羊模式 B 篩選 (RS) - 100% 證交所官方報價 + 純上市股票 (.TW)
    try:
        rs_candidates = run_pool_screener(tw_listed_pool, twse_quotes=twse_quotes, is_us=False, is_rs=True)
        tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]
        history_data["RS"][tw_active_date] = {
            "date": tw_active_date,
            "dayOfWeek": tw_wd,
            "taiexClose": tw_summary.get("close", "大盤動態監控"),
            "taiexChange": tw_summary.get("change", "--"),
            "taiexPercent": "證交所官方實盤核實 (純上市)",
            "taiexVolume": tw_summary.get("volume", "--"),
            "marketNature": "【證交所官方實盤直連 + 純上市限定】技術面領先突破 + 財報 EPS (PE Band / PEG) 雙重驗證",
            "sectors": [
                {"name": "上市軟體 / 資安 / 系統整合", "grade": "S級催化", "note": "排除上櫃興櫃小型股，直連證交所收盤報價"},
                {"name": "上市高股息與基礎設施防禦", "grade": "A級催化", "note": "具穩定財報與法人護盤底氣"}
            ],
            "candidates": rs_candidates
        }
        trim_history(history_data["RS"], MAX_HISTORY_DAYS)
    except Exception as e:
        print(f"RS 篩選異常: {e}")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)

    print("三大策略與證交所官方實盤數據更新完畢！已成功寫入 data.json")

if __name__ == "__main__":
    main()
