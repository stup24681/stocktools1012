import os
import json
import datetime
from datetime import timedelta
import urllib3
import requests
import yfinance as yf

# 關閉證交所 SSL 憑證鏈在 Linux 虛擬機上可能產生的警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 全域量化篩選參數設定
# ==========================================
MAX_HISTORY_DAYS = 5
MIN_GAIN_PCT = 8.5
MIN_VOL_RATIO = 1.8
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}


def get_taiwan_today_str():
    """取得台灣時區 (UTC+8) 今日日期字串 YYYY-MM-DD"""
    now_tw = datetime.datetime.now(datetime.timezone.utc) + timedelta(hours=8)
    return now_tw.strftime("%Y-%m-%d")


def fetch_twse_official_daily_quotes_with_fallback():
    """
    【最新交易日自動探測與官方報價引擎】
    不依賴更新較慢的 FMTQIK，直接從台灣今日日期往回探測近 5 天，
    向證交所每日收盤行情總表 (MI_INDEX) 請求數據（15:00 盤後即已結算），
    保證 16:05 執行時第一時間拿到當日最新收盤價！
    """
    now_tw = datetime.datetime.now(datetime.timezone.utc) + timedelta(hours=8)

    for offset in range(6):
        check_dt = now_tw - timedelta(days=offset)
        # 跳過週六(5)、週日(6)
        if check_dt.weekday() >= 5:
            continue

        target_date_str = check_dt.strftime("%Y-%m-%d")
        yyyymmdd = check_dt.strftime("%Y%m%d")

        try:
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date={yyyymmdd}&type=ALLBUT0999"
            res = requests.get(url, headers=HTTP_HEADERS, timeout=25, verify=False)
            if res.status_code != 200:
                continue

            data = res.json()
            if data.get("stat") != "OK":
                print(f"探測 {target_date_str}：證交所尚無盤後資料 (stat={data.get('stat')})")
                continue

            returned_date = str(data.get("date", "")).strip()
            if returned_date and returned_date != yyyymmdd:
                continue

            quotes_map = {}
            taiex_summary = None
            tables = data.get("tables", [])

            for tbl in tables:
                title = tbl.get("title", "")
                # 1. 解析大盤加權指數最新收盤
                if "價格指數" in title or "大盤統計資訊" in title:
                    for row in tbl.get("data", []):
                        if "發行量加權股價指數" in str(row[0]):
                            try:
                                close_idx = float(str(row[1]).replace(",", ""))
                                sign_str = str(row[2])
                                diff_pts = float(str(row[3]).replace(",", ""))
                                change_pts = -diff_pts if "-" in sign_str else diff_pts
                                pct = float(str(row[4]).replace(",", "").replace("%", ""))
                                if "-" in sign_str and pct > 0:
                                    pct = -pct
                                s_char = "+" if change_pts >= 0 else ""
                                taiex_summary = {
                                    "close": f"{close_idx:,.2f} ({s_char}{pct:.2f}%)",
                                    "change": f"{s_char}{change_pts:,.2f}",
                                    "percent": f"{s_char}{pct:.2f}%",
                                    "volume": "證交所盤後結算"
                                }
                            except Exception:
                                pass

                # 2. 解析全市場純上市普通股收盤行情
                if "每日收盤行情" in title:
                    for row in tbl.get("data", []):
                        code = str(row[0]).strip()
                        if len(code) != 4 or not code.isdigit() or code.startswith("00"):
                            continue
                        try:
                            vol_shares = int(str(row[2]).replace(",", ""))
                            vol_lots = int(vol_shares / 1000)
                            close_str = str(row[8]).replace(",", "").strip()
                            if not close_str or close_str == "--":
                                continue
                            close_val = float(close_str)
                            sign_html = str(row[9])
                            diff_val = float(str(row[10]).replace(",", ""))
                            change_val = -diff_val if "-" in sign_html else diff_val
                            prev_close = close_val - change_val
                            pct = (change_val / prev_close * 100.0) if prev_close > 0 else 0.0

                            quotes_map[code] = {
                                "code": code,
                                "name": str(row[1]).strip(),
                                "close": round(close_val, 2),
                                "change": round(change_val, 2),
                                "pct": round(pct, 2),
                                "vol_lots": vol_lots,
                                "date": target_date_str
                            }
                        except (ValueError, IndexError):
                            continue

            if len(quotes_map) > 500:
                if not taiex_summary:
                    taiex_summary = fetch_twse_market_summary_fallback(target_date_str)
                print(f"成功鎖定最新交易日 {target_date_str}，共載入 {len(quotes_map)} 檔純上市官方收盤數據！")
                return target_date_str, quotes_map, taiex_summary

        except Exception as e:
            print(f"探測 {target_date_str} 異常: {e}")

    return get_taiwan_today_str(), {}, {"close": "盤後結算中", "change": "--", "percent": "--", "volume": "--"}


def fetch_twse_market_summary_fallback(target_date_str):
    """大盤指數與成交金額備援查詢"""
    try:
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=15, verify=False)
        if res.status_code == 200:
            data = res.json()
            if "data" in data and len(data["data"]) > 0:
                last_row = data["data"][-1]
                vol_yuan = float(str(last_row[2]).replace(",", ""))
                vol_billion = round(vol_yuan / 100000000.0, 2)
                close_idx = float(str(last_row[4]).replace(",", ""))
                change_pts = float(str(last_row[5]).replace(",", ""))
                prev_idx = close_idx - change_pts
                pct = (change_pts / prev_idx * 100.0) if prev_idx > 0 else 0.0
                sign = "+" if change_pts >= 0 else ""
                return {
                    "close": f"{close_idx:,.2f} ({sign}{pct:.2f}%)",
                    "change": f"{sign}{change_pts:,.2f}",
                    "percent": f"{sign}{pct:.2f}%",
                    "volume": f"成交 {vol_billion:,.2f} 億"
                }
    except Exception:
        pass
    return {"close": f"{target_date_str} 結算", "change": "--", "percent": "--", "volume": "--"}


def get_us_latest_trading_date():
    """取得美股最新交易日"""
    try:
        ticker = yf.Ticker("^IXIC")
        df = ticker.history(period="5d")
        if not df.empty:
            return df.index[-1].strftime("%Y-%m-%d")
    except Exception:
        pass
    return get_taiwan_today_str()


def fetch_us_market_summary():
    """取得美股那斯達克 (^IXIC) 最新收盤資訊"""
    try:
        ticker = yf.Ticker("^IXIC")
        df = ticker.history(period="5d")
        if len(df) >= 2:
            c = float(df["Close"].iloc[-1])
            p = float(df["Close"].iloc[-2])
            vol_b = round(float(df["Volume"].iloc[-1]) / 100000000.0, 1)
            diff = c - p
            pct = (diff / p * 100.0) if p > 0 else 0.0
            sign = "+" if diff >= 0 else ""
            return {
                "close": f"{c:,.2f} ({sign}{pct:.2f}%)",
                "change": f"{sign}{diff:,.2f}",
                "percent": f"{sign}{pct:.2f}%",
                "volume": f"{vol_b} 億股"
            }
    except Exception:
        pass
    return {"close": "盤後結算中", "change": "--", "percent": "--", "volume": "--"}


def calculate_fundamental_valuation(ticker, official_close_price):
    """
    財報 EPS 量化估值引擎 (PE Band × PEG 成長檢驗)
    """
    try:
        info = ticker.info or {}
        fwd_eps = info.get("forwardEps") or info.get("trailingEps")
        peg = info.get("pegRatio")

        if fwd_eps and float(fwd_eps) > 0:
            fwd_eps = round(float(fwd_eps), 2)
            fwd_pe = round(official_close_price / fwd_eps, 1)
            fair_low = round(fwd_eps * 15.0, 1)
            fair_high = round(fwd_eps * 22.0, 1)

            if peg is not None and float(peg) > 0:
                peg_val = round(float(peg), 2)
            else:
                earnings_growth = info.get("earningsGrowth")
                if earnings_growth and float(earnings_growth) > 0.05:
                    peg_val = round(fwd_pe / (float(earnings_growth) * 100.0), 2)
                else:
                    peg_val = round(fwd_pe / 20.0, 2)

            is_value_qualified = (fwd_pe <= 28.0 and peg_val <= 1.25)

            if fwd_pe <= 20.0 and peg_val <= 1.0:
                grade = "S (基本面+技術雙優)"
                status = f"合理價值黃金區 (PE {fwd_pe}x / PEG {peg_val})，具獲利成長安全邊際"
            elif is_value_qualified:
                grade = "A (穩健成長)"
                status = f"合理成長區間 (PE {fwd_pe}x / PEG {peg_val})，符合低估值提前佈局標準"
            else:
                grade = "C (高估值過熱)"
                status = f"動態 PE 偏高 ({fwd_pe}x / PEG {peg_val})，不符合低估值安全邊際"

            return {
                "isQualified": is_value_qualified,
                "forwardEps": fwd_eps,
                "forwardPe": fwd_pe,
                "pegRatio": peg_val,
                "fairValueZone": f"{fair_low} ～ {fair_high} 元 (15~22x PE)",
                "valuationStatus": status,
                "valuationGrade": grade
            }
    except Exception:
        pass

    return {
        "isQualified": False,
        "forwardEps": None,
        "forwardPe": None,
        "pegRatio": None,
        "fairValueZone": "無預估 EPS 數據",
        "valuationStatus": "缺乏明確預估 EPS 數據，不符合基本面價值模型",
        "valuationGrade": "B (無EPS估值)"
    }


def evaluate_bear_market_rs_candidate(symbol, name, sector, twse_quotes, target_date_str):
    """
    【策略二：熊市逆勢領頭羊 (基本面 EPS 價值 + 抗跌突破)】
    與「經典台股突破 (≥8.5% 斐波回踩)」完全獨立、互不重疊：
    1. 僅限純上市股票 (.TW)，排除上櫃與興櫃
    2. 漲幅區間限制在 3.0% ～ 8.4%（專抓「低調穩健抗跌發動」，不追已漲停過熱股，避免與經典突破重疊）
    3. 強制要求具備正向預估 EPS，且動態 PE <= 28倍、PEG <= 1.25（嚴格基本面篩選）
    4. 股價穩站 20MA 与 60MA 之上並突破 20 日箱型高點
    """
    if not symbol.endswith(".TW"):
        return None
    clean_code = symbol.replace(".TW", "")

    official = twse_quotes.get(clean_code)
    if not official or official.get("date") != target_date_str:
        return None

    close = official["close"]
    gain_pct = official["pct"]
    vol_lots = official["vol_lots"]
    official_name = official["name"] or name

    # 策略區隔條件：漲幅 3.0% ~ 8.4%（不追漲停板，專抓穩健抗跌提前發動股）
    if vol_lots < 1500 or not (3.0 <= gain_pct < MIN_GAIN_PCT):
        return None

    try:
        ticker = yf.Ticker(symbol)
        # 優先檢驗財報 EPS 基本面門檻（無預估 EPS 或 PE > 28x 直接淘汰）
        val_info = calculate_fundamental_valuation(ticker, close)
        if not val_info.get("isQualified"):
            return None

        df = ticker.history(period="3mo")
        if df.empty or len(df) < 25:
            return None

        df_hist = df[df.index.strftime("%Y-%m-%d") < target_date_str]
        if len(df_hist) < 20:
            df_hist = df.iloc[:-1]

        hist_closes = list(df_hist['Close'].tail(59)) + [close]
        ma20 = sum(hist_closes[-20:]) / 20.0
        ma60 = sum(hist_closes) / len(hist_closes)
        vma5_lots = float(df_hist['Volume'].tail(5).mean()) / 1000.0

        if close < ma20 or close < ma60:
            return None

        vol_ratio = vol_lots / vma5_lots if vma5_lots > 0 else 1.0
        if vol_ratio < 1.2:
            return None

        box_window = df_hist.tail(20)
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close <= box_high:
            return None

        neckline = round(box_high, 1)
        entry_min = round(neckline * 0.99, 1)
        entry_max = round(neckline * 1.025, 1)
        entry_price = round(neckline * 1.005, 1)
        stop_loss = round(neckline * 0.96, 1)
        target_price = round(val_info["forwardEps"] * 22.0, 1) if val_info.get("forwardEps") else round(neckline * 1.15, 1)

        val_bonus = 4 if "S" in val_info["valuationGrade"] else 2
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
            "volRatio": f"{vol_ratio:.2f} 倍 (證交所 {target_date_str} 實盤核實)",
            "maStatus": f"上市價值領頭羊 ‧ 站穩 20MA ({ma20:.1f}) 與 60MA ({ma60:.1f})",
            "boxDays": "20 日箱型低調突破 (模式 B + EPS 價值雙驗證)",
            "boxRange": f"{box_low:.1f} ～ {neckline}",
            "breakoutPrice": f"{neckline}",
            "breakoutReason": f"【財報估值+抗跌雙驗證】PE 僅 {val_info['forwardPe']}x (PEG {val_info['pegRatio']})，不追漲停板，穩健帶量突破 {neckline} 元頸線！",
            "leadTime": "具備實質獲利保護與低本益比安全邊際，大盤止跌即展開價值重估行情",
            "strategy": "模式 B：EPS合理價值區 + 回踩頸線布局",
            "entryZone": f"{entry_min} ～ {entry_max} 元",
            "entryPrice": f"{entry_price} 元",
            "stopLossPrice": f"{stop_loss} 元 (跌破頸線防線)",
            "stopLossNum": stop_loss,
            "targetPrice": f"{target_price} 元 (22x PE 目標)",
            "valuation": val_info,
            "noBuyCondition": f"若跌回 {stop_loss} 頸線下方則結構轉弱停損；{val_info['valuationStatus']}",
            "score": round(90 + min(gain_pct, 5.0) + val_bonus, 1)
        }
    except Exception as e:
        print(f"熊市抗跌評估異常 {symbol}: {e}")
        return None


def evaluate_stock_candidate(symbol, name, sector, twse_quotes=None, target_date_str=None, is_us=False):
    """
    【策略一：經典多頭動能突破模型 (日漲幅 >= 8.5% + 斐波那契 38.2% 回踩)】
    純技術面強勢爆發動能策略，與「逆勢領頭羊」完全區隔
    """
    clean_code = symbol.replace(".TW", "").replace(".TWO", "")
    official = twse_quotes.get(clean_code) if (not is_us and twse_quotes) else None

    if not is_us and (not official or official.get("date") != target_date_str):
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
            df_hist = df[df.index.strftime("%Y-%m-%d") < target_date_str]
            if len(df_hist) < 20:
                df_hist = df.iloc[:-1]
        else:
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            close = round(float(today['Close']), 2)
            prev_close = float(yesterday['Close'])
            gain_pct = ((close - prev_close) / prev_close) * 100.0
            vol_lots = int(today['Volume'])
            vol_str = f"{vol_lots:,}"
            official_name = name
            df_hist = df.iloc[:-1]

        min_gain = 4.5 if is_us else MIN_GAIN_PCT
        if gain_pct < min_gain:
            return None

        hist_closes = list(df_hist['Close'].tail(25)) + [close]
        ma5 = sum(hist_closes[-5:]) / 5.0
        ma10 = sum(hist_closes[-10:]) / 10.0
        ma20 = sum(hist_closes[-20:]) / 20.0
        ma20_prev = sum(hist_closes[-21:-1]) / 20.0

        if not ((ma5 > ma10 > ma20) and (ma20 >= ma20_prev)):
            return None

        vma5_base = (float(df_hist['Volume'].tail(5).mean()) / 1000.0) if not is_us else float(df_hist['Volume'].tail(5).mean())
        vol_ratio = vol_lots / vma5_base if vma5_base > 0 else 1.0
        min_vol = 1.5 if is_us else MIN_VOL_RATIO
        if vol_ratio < min_vol:
            return None

        box_window = df_hist.tail(20)
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close <= box_high:
            return None

        val_info = calculate_fundamental_valuation(ticker, close)

        wave_low = float(df_hist['Low'].tail(30).min())
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
            "volRatio": f"{vol_ratio:.2f} 倍 (動能放量突破)",
            "maStatus": "5MA > 10MA > 20MA 強勢多頭排列，20MA 向上",
            "boxDays": "20 交易日箱型動能突破",
            "boxRange": f"{box_low:.1f} ～ {box_high:.1f}",
            "breakoutPrice": f"{box_high:.1f}",
            "breakoutReason": f"【動能突破策略】單日強漲 +{gain_pct:.2f}% 並放量 {vol_ratio:.2f} 倍衝破箱型上緣 ({box_high:.1f})，靜待斐波那契 38.2% 回踩。",
            "strategy": "等斐波那契 38.2% 回踩",
            "entryZone": f"{entry_min} ～ {entry_max}",
            "entryPrice": f"{entry_price}",
            "stopLossPrice": f"{stop_loss}",
            "stopLossNum": stop_loss,
            "valuation": val_info,
            "noBuyCondition": f"剛大漲 ≥8.5% 嚴禁追高；若跌破斐波 61.8% ({stop_loss}) 結構失效嚴禁抄底",
            "score": round(90 + min(gain_pct, 10.0), 1)
        }
    except Exception as e:
        print(f"個股分析失敗 {symbol}: {e}")
        return None


def run_pool_screener(pool, twse_quotes=None, target_date_str=None, is_us=False, is_rs=False):
    """通用股票池掃描排序"""
    candidates = []
    for sym, name, sec in pool:
        if is_rs:
            res = evaluate_bear_market_rs_candidate(sym, name, sec, twse_quotes, target_date_str)
        else:
            res = evaluate_stock_candidate(sym, name, sec, twse_quotes=twse_quotes, target_date_str=target_date_str, is_us=is_us)
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


def build_split_tw_pools(twse_quotes):
    """
    分別建構兩套互不重疊的純上市動態掃描池：
    1. momentum_pool (經典台股突破)：日漲幅 >= 8.5%、成交量 >= 1,500 張
    2. value_rs_pool (逆勢領頭羊)：日漲幅 3.0% ~ 8.4%、成交量 >= 1,500 張
    """
    momentum_pool = []
    value_rs_pool = []

    strong_momentum = sorted(
        [q for q in twse_quotes.values() if q["vol_lots"] >= 1500 and q["pct"] >= MIN_GAIN_PCT],
        key=lambda x: x["pct"],
        reverse=True
    )[:30]

    steady_rs = sorted(
        [q for q in twse_quotes.values() if q["vol_lots"] >= 1500 and (3.0 <= q["pct"] < MIN_GAIN_PCT)],
        key=lambda x: x["pct"],
        reverse=True
    )[:40]

    for q in strong_momentum:
        momentum_pool.append((f"{q['code']}.TW", q["name"], "上市 (TWSE) ‧ 強勢動能突破股"))

    for q in steady_rs:
        value_rs_pool.append((f"{q['code']}.TW", q["name"], "上市 (TWSE) ‧ 穩健抗跌價值股"))

    return momentum_pool, value_rs_pool


def main():
    try:
        print("啟動量化篩選機器人 (雙策略物理隔離 + MI_INDEX 即時日期探測版)...")

        # 直接探測 MI_INDEX 取得最新已結算交易日與全市場報價
        tw_active_date, twse_quotes, tw_summary = fetch_twse_official_daily_quotes_with_fallback()
        us_active_date = get_us_latest_trading_date()

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

        us_pool = [
            ("CRDO", "Credo Technology", "高速傳輸/AEC主動電纜"),
            ("NVDA", "NVIDIA", "AI GPU 算力龍頭"),
            ("AVGO", "Broadcom", "AI 客製化晶片/網通"),
            ("MRVL", "Marvell", "光互連與資料中心晶片"),
            ("ALAB", "Astera Labs", "AI 連接晶片"),
            ("TSM", "TSMC ADR", "晶圓代工"),
            ("PLTR", "Palantir", "AI 企業軟體平台")
        ]

        if len(twse_quotes) > 500:
            momentum_pool, value_rs_pool = build_split_tw_pools(twse_quotes)
            print(f"[{tw_active_date}] 動能突破觀察池: {len(momentum_pool)} 檔 | 逆勢價值領頭羊觀察池: {len(value_rs_pool)} 檔")

            tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]

            # 1. 經典台股突破 (日漲幅 >= 8.5% + 斐波回踩)
            try:
                tw_candidates = run_pool_screener(momentum_pool, twse_quotes=twse_quotes, target_date_str=tw_active_date, is_us=False, is_rs=False)
                history_data["TW"][tw_active_date] = {
                    "date": tw_active_date,
                    "dayOfWeek": tw_wd,
                    "taiexClose": tw_summary["close"],
                    "taiexChange": tw_summary["change"],
                    "taiexPercent": tw_summary["percent"],
                    "taiexVolume": tw_summary["volume"],
                    "marketNature": f"【策略一：動能爆發 + 斐波那契 38.2% 回踩】證交所 {tw_active_date} 官方結算（漲幅 ≥ 8.5% 且放量 ≥ 1.8 倍）",
                    "sectors": [{"name": "強勢動能突破掃描", "grade": "S級", "note": f"已核對 {tw_active_date} 官方收盤價"}],
                    "candidates": tw_candidates
                }
                trim_history(history_data["TW"], MAX_HISTORY_DAYS)
            except Exception as e:
                print(f"TW 篩選異常: {e}")

            # 2. 逆勢領頭羊 (日漲幅 3.0~8.4% + 財報 PE<=28 / PEG<=1.25 雙重驗證)
            try:
                rs_candidates = run_pool_screener(value_rs_pool, twse_quotes=twse_quotes, target_date_str=tw_active_date, is_us=False, is_rs=True)
                history_data["RS"][tw_active_date] = {
                    "date": tw_active_date,
                    "dayOfWeek": tw_wd,
                    "taiexClose": tw_summary["close"],
                    "taiexChange": tw_summary["change"],
                    "taiexPercent": f"證交所 {tw_active_date} 實盤核實",
                    "taiexVolume": tw_summary["volume"],
                    "marketNature": "【策略二：純上市價值領頭羊】排除漲停過熱股，專挑漲幅 3~8.4% 且動態 PE ≤ 28x、PEG ≤ 1.25 之低估值基本面優選股",
                    "sectors": [
                        {"name": "純上市低估值領頭羊 (排除上櫃興櫃)", "grade": "S級催化", "note": f"證交所 {tw_active_date} MI_INDEX 核實"},
                        {"name": "財報 EPS 嚴格過濾 (PE ≤ 28x)", "grade": "A級催化", "note": "杜絕高本益比純題材炒作"}
                    ],
                    "candidates": rs_candidates
                }
                trim_history(history_data["RS"], MAX_HISTORY_DAYS)
            except Exception as e:
                print(f"RS 篩選異常: {e}")
        else:
            print(f"警告：無法取得證交所盤後報價，保留既有資料。")

        # 3. 美股篩選
        try:
            us_summary = fetch_us_market_summary()
            us_candidates = run_pool_screener(us_pool, twse_quotes=None, target_date_str=us_active_date, is_us=True, is_rs=False)
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

        with open("data.json", "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)

        print("執行順利完成！已安全寫入 data.json (Exit Code 0)")
    except Exception as fatal_err:
        print(f"全域安全防護捕獲異常: {fatal_err}")
        if not os.path.exists("data.json"):
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump({"TW": {}, "US": {}, "RS": {}}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
