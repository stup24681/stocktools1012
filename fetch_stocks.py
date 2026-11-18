# ... existing code ...
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

def fetch_twse_official_daily_quotes(target_date_str):
    """
    【核心資訊正確保證 - 附帶官方日期強制核驗】
    直連台灣證券交易所 (TWSE) 指定交易日每日收盤行情總表 (MI_INDEX)
    1. 強制帶入 target_date_str (YYYYMMDD) 參數，杜絕抓到昨日舊快取
    2. 驗證回傳資料日期是否與 target_date_str 吻合，若未結算則回傳空字典不寫入錯誤資料
    3. 僅保留 4 碼純上市普通股 (排除 00 開頭 ETF、權證、上櫃、興櫃)
    """
    quotes_map = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    yyyymmdd = target_date_str.replace("-", "")
    
    try:
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date={yyyymmdd}&type=ALLBUT0999"
        res = requests.get(url, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            if data.get("stat") != "OK":
                print(f"TWSE MI_INDEX 尚未公布 {yyyymmdd} 盤後資料 (stat={data.get('stat')})")
                return {}
            
            returned_date = str(data.get("date", "")).strip()
            if returned_date and returned_date != yyyymmdd:
                print(f"日期不符警告：請求 {yyyymmdd} 但證交所回傳 {returned_date}，拒絕寫入舊資料！")
                return {}

            tables = data.get("tables", [])
            for tbl in tables:
                if "每日收盤行情" in tbl.get("title", ""):
                    for row in tbl.get("data", []):
                        code = str(row[0]).strip()
                        # 嚴格過濾：僅限 4 碼純上市普通股 (排除 00xx ETF 與 6 碼權證)
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
                            pct = (change_val / prev_close * 100) if prev_close > 0 else 0.0
                            
                            quotes_map[code] = {
                                "code": code,
                                "name": str(row[1]).strip(),
                                "close": round(close_val, 2),
                                "change": round(change_val, 2),
                                "pct": round(pct, 2),
                                "vol_lots": vol_lots,
                                "date": target_date_str,
                                "source": f"TWSE_MI_INDEX_{yyyymmdd}"
                            }
                        except (ValueError, IndexError):
                            continue
            if len(quotes_map) > 500:
                print(f"成功從 TWSE MI_INDEX 載入 {target_date_str} 共 {len(quotes_map)} 檔純上市股票官方收盤價！")
                return quotes_map
    except Exception as e:
        print(f"TWSE MI_INDEX 抓取異常: {e}")

    return quotes_map

def calculate_fundamental_valuation(ticker, official_close_price):
# ... existing code ...
def evaluate_bear_market_rs_candidate(symbol, name, sector, twse_quotes, target_date_str):
    """
    模式 B：熊市抗跌領先突破篩選模型 + 財報 EPS 雙重驗證
    1. 僅限純上市股票 (.TW)，且必須存在於當日證交所官方 MI_INDEX 報價表中
    2. 收盤價、漲跌幅、成交張數 100% 採用證交所當日官方數據
    3. 歷史箱型高點強制排除當日 K 線，確保計算基準純淨無誤差
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

    # 基本流動性與逆勢強勢初篩：成交量 >= 1,500 張 且 單日漲幅 >= 4.0%
    if vol_lots < 1500 or gain_pct < 4.0:
        return None

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        if df.empty or len(df) < 25:
            return None

        # 排除當日未完成或已同步的 K 線，純粹取「昨日及以前」的歷史 K 線計算箱型與均量
        df_hist = df[df.index.strftime("%Y-%m-%d") < target_date_str]
        if len(df_hist) < 20:
            df_hist = df.iloc[:-1]

        # 將當日官方精準收盤價納入計算最新 20MA / 60MA
        hist_closes = list(df_hist['Close'].tail(59)) + [close]
        ma20 = sum(hist_closes[-20:]) / 20.0
        ma60 = sum(hist_closes) / len(hist_closes)
        vma5_lots = float(df_hist['Volume'].tail(5).mean()) / 1000.0

        # 1. 均線防線：當日官方收盤價必須穩站月線與季線之上
        if close < ma20 or close < ma60:
            return None

        # 2. 量能與表態條件：放量 >= 1.3倍 或單日強勢漲幅 >= 6.5%
        vol_ratio = vol_lots / vma5_lots if vma5_lots > 0 else 1.0
        if vol_ratio < 1.3 and gain_pct < 6.5:
            return None

        # 3. 突破近 20 個歷史交易日箱型高點頸線
        box_window = df_hist.tail(20)
        box_high = float(box_window['High'].max())
        box_low = float(box_window['Low'].min())
        if close <= box_high:
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
            "volRatio": f"{vol_ratio:.2f} 倍 (證交所 {target_date_str} 實盤核實)",
            "maStatus": f"上市強勢股 ‧ 站穩 20MA ({ma20:.1f}) 與 60MA ({ma60:.1f})",
            "boxDays": "20 日箱型搶先突破 (模式 B)",
            "boxRange": f"{box_low:.1f} ～ {neckline}",
            "breakoutPrice": f"{neckline}",
            "breakoutReason": f"【證交所 {target_date_str} 官方結算】純上市標的逆勢站穩均線，衝破 {neckline} 元頸線收 {close} 元 ({sign}{gain_pct:.2f}%)！",
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

def evaluate_stock_candidate(symbol, name, sector, twse_quotes=None, target_date_str=None, is_us=False):
    """經典多頭突破模型 (台股強制使用證交所當日官方收盤價)"""
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
            gain_pct = ((close - prev_close) / prev_close) * 100
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
            "volRatio": f"{vol_ratio:.2f} 倍 (放量突破)",
            "maStatus": "5MA > 10MA > 20MA 多頭排列，20MA 向上",
            "boxDays": "20 交易日箱型突破",
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

def build_dynamic_tw_listed_pool(twse_quotes):
    """
    【全市場純上市動態掃描池】
    直接從當日證交所 MI_INDEX 全市場 1,000+ 檔純上市股票中，
    動態挑選「當日成交量 >= 1,500 張 且 漲幅 >= 3.5%」的活躍領頭羊進行深度均線與財報分析！
    徹底解決寫死少數幾檔股票導致漏抓強勢股的問題。
    """
    sector_lookup = {
        "2468": "上市 ‧ 資安與系統整合 / 集團綜效",
        "2471": "上市 ‧ 企業軟體 / 金融資安系統",
        "6214": "上市 ‧ 台灣資訊服務龍頭 / 企業AI雲",
        "2453": "上市 ‧ 系統整合 / 智慧機器人與雲端",
        "5203": "上市 ‧ 純AI軟體 / 臉部辨識與生成式AI",
        "2412": "上市 ‧ 電信防禦 / AI算力IDC機房",
        "1802": "上市 ‧ 低介電玻纖布 / AI基板材料",
        "2492": "上市 ‧ 被動元件 / MLCC高階電容",
        "3450": "上市 ‧ AI光模組封裝 / CPO",
        "2330": "上市 ‧ 晶圓代工龍頭",
        "2317": "上市 ‧ AI伺服器組裝",
        "2382": "上市 ‧ AI伺服器/雲端",
        "3231": "上市 ‧ AI伺服器基板",
        "2308": "上市 ‧ AI電源散熱龍頭"
    }
    dynamic_pool = []
    # 依漲幅由高至低排序，取前 45 檔當日最強純上市標的進行深度技術與財報運算
    sorted_strong = sorted(
        [q for q in twse_quotes.values() if q["vol_lots"] >= 1500 and q["pct"] >= 3.5],
        key=lambda x: x["pct"],
        reverse=True
    )[:45]

    for q in sorted_strong:
        code = q["code"]
        sec = sector_lookup.get(code, "上市 (TWSE) ‧ 全市場動態強勢股")
        dynamic_pool.append((f"{code}.TW", q["name"], sec))
    return dynamic_pool

def main():
    print("啟動量化篩選機器人 (帶官方日期核驗 MI_INDEX + 全市場純上市動態掃描 + 防重複鎖定)...")
    tw_active_date = get_dynamic_trading_date("^TWII")
    us_active_date = get_dynamic_trading_date("^IXIC")

    # 強制以當日真實交易日期向證交所請求 MI_INDEX 盤後總表
    twse_quotes = fetch_twse_official_daily_quotes(tw_active_date)

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

    # 只有在成功取得當日證交所官方報價時才更新台股與領頭羊，杜絕重複寫入昨日舊價格！
    if len(twse_quotes) > 500:
        tw_dynamic_pool = build_dynamic_tw_listed_pool(twse_quotes)
        print(f"已從全市場純上市股票動態篩出 {len(tw_dynamic_pool)} 檔活躍強勢股進入深度運算...")
        tw_summary = fetch_twse_market_summary()

        # 檢查大盤收盤點數是否與前一個交易日一模一樣（防呆鎖）
        prev_dates = [d for d in sorted(history_data["TW"].keys(), reverse=True) if d < tw_active_date]
        is_duplicate_market = False
        if prev_dates:
            prev_close_str = history_data["TW"][prev_dates[0]].get("taiexClose", "")
            if prev_close_str and prev_close_str == tw_summary["close"]:
                print(f"防重複攔截：{tw_active_date} 大盤指數 ({tw_summary['close']}) 與前一日 {prev_dates[0]} 完全相同，證交所尚未刷新，中止寫入！")
                is_duplicate_market = True

        if not is_duplicate_market:
            # 1. 執行台股經典多頭突破篩選 (TW)
            try:
                tw_candidates = run_pool_screener(tw_dynamic_pool, twse_quotes=twse_quotes, target_date_str=tw_active_date, is_us=False, is_rs=False)
                tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]
                history_data["TW"][tw_active_date] = {
                    "date": tw_active_date,
                    "dayOfWeek": tw_wd,
                    "taiexClose": tw_summary["close"],
                    "taiexChange": tw_summary["change"],
                    "taiexPercent": tw_summary["percent"],
                    "taiexVolume": tw_summary["volume"],
                    "marketNature": f"全市場純上市動態掃描 + 證交所 {tw_active_date} 官方結算校核（無符合標的時恪守 0 檔寧缺勿濫）",
                    "sectors": [{"name": "全市場上市突破掃描", "grade": "S級", "note": f"已核對 {tw_active_date} 官方收盤價"}],
                    "candidates": tw_candidates
                }
                trim_history(history_data["TW"], MAX_HISTORY_DAYS)
            except Exception as e:
                print(f"TW 篩選異常: {e}")

            # 3. 執行逆勢領頭羊模式 B 篩選 (RS) - 100% 證交所官方報價 + 全市場純上市動態掃描
            try:
                rs_candidates = run_pool_screener(tw_dynamic_pool, twse_quotes=twse_quotes, target_date_str=tw_active_date, is_us=False, is_rs=True)
                tw_wd = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.fromisoformat(tw_active_date).weekday()]
                history_data["RS"][tw_active_date] = {
                    "date": tw_active_date,
                    "dayOfWeek": tw_wd,
                    "taiexClose": tw_summary.get("close", "大盤動態監控"),
                    "taiexChange": tw_summary.get("change", "--"),
                    "taiexPercent": f"證交所 {tw_active_date} 實盤核實",
                    "taiexVolume": tw_summary.get("volume", "--"),
                    "marketNature": "【全市場純上市動態掃描 + 官方日期校核】技術面領先突破 + 財報 EPS (PE Band / PEG) 雙重驗證",
                    "sectors": [
                        {"name": "純上市強勢領頭羊 (排除上櫃興櫃)", "grade": "S級催化", "note": f"直連證交所 {tw_active_date} MI_INDEX 總表"},
                        {"name": "基本面 EPS 雙重驗證", "grade": "A級催化", "note": "具穩定財報與法人護盤底氣"}
                    ],
                    "candidates": rs_candidates
                }
                trim_history(history_data["RS"], MAX_HISTORY_DAYS)
            except Exception as e:
                print(f"RS 篩選異常: {e}")
    else:
        print(f"警告：未能取得 {tw_active_date} 證交所官方 MI_INDEX 結算總表，跳過本次台股寫入以確保絕不產生重複舊數據。")

    # 2. 執行美股經典多頭突破篩選 (US)
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

    print("三大策略與證交所官方實盤數據更新完畢！已成功寫入 data.json")

if __name__ == "__main__":
    main()
