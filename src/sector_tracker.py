"""
板块跟踪器 V2 — 周维度板块热度分析
混合方案：申万二级行业 + 概念板块
5 级情绪：燃烧 / 发热 / 常温 / 退潮 / 冰点

使用方法：
    python3 sector_tracker.py              # 输出本周跟踪表格
    python3 sector_tracker.py --weeks 4    # 输出最近4周的对比
    python3 sector_tracker.py --list       # 显示当前跟踪板块
    python3 sector_tracker.py --search 存储 # 搜索板块
"""

import sys
import json
import os
import time
from datetime import datetime, timedelta

import tushare as ts
import pandas as pd

# ── tushare 配置 ──────────────────────────────────────────────
TOKEN = os.environ.get("TUSHARE_TOKEN", "your-tushare-token")
ts.set_token(TOKEN)
pro = ts.pro_api()

# ── 板块类型 ─────────────────────────────────────────────────
TYPE_SW = "sw"       # 申万行业指数
TYPE_CONCEPT = "concept"  # 概念板块指数

# ── 跟踪的板块列表（可自定义） ─────────────────────────────────
# 格式: { ts_code: { name, type, category, sentiment } }
SECTOR_MAP = {
    # ── 申万二级行业 ──
    "801081.SI": {
        "name": "半导体",
        "type": TYPE_SW,
        "category": "硬科技",
        "sentiment": ["中芯国际", "北方华创"],
    },
    "801738.SI": {
        "name": "电网设备",
        "type": TYPE_SW,
        "category": "新基建",
        "sentiment": ["许继电气", "平高电气"],
    },
    "801735.SI": {
        "name": "光伏设备",
        "type": TYPE_SW,
        "category": "新能源",
        "sentiment": ["隆基绿能", "阳光电源"],
    },
    "801737.SI": {
        "name": "电池",
        "type": TYPE_SW,
        "category": "新能源",
        "sentiment": ["宁德时代", "亿纬锂能"],
    },
    "801102.SI": {
        "name": "通信设备",
        "type": TYPE_SW,
        "category": "数字经济",
        "sentiment": ["中兴通讯", "烽火通信"],
    },
    "801078.SI": {
        "name": "自动化设备",
        "type": TYPE_SW,
        "category": "智能制造",
        "sentiment": ["汇川技术", "埃斯顿"],
    },
    "801104.SI": {
        "name": "软件开发",
        "type": TYPE_SW,
        "category": "数字经济",
        "sentiment": ["用友网络", "金山办公"],
    },
    # ── 概念板块 ──
    # index_code: 概念板块对应的真实指数代码（由 pro.index_basic() 搜索得到）
    "TS572": {
        "name": "存储芯片",
        "type": TYPE_CONCEPT,
        "category": "硬科技",
        "sentiment": ["兆易创新", "北京君正"],
        "index_code": "000685.SH",  # 科创芯片
    },
    "TS737": {
        "name": "储能",
        "type": TYPE_CONCEPT,
        "category": "新能源",
        "sentiment": ["派能科技", "鹏辉能源"],
        "index_code": "931746.CSI",  # 储能产业
    },
    "TS689": {
        "name": "创新药",
        "type": TYPE_CONCEPT,
        "category": "医药",
        "sentiment": ["恒瑞医药", "信立泰"],
        "index_code": "931152.CSI",  # CS创新药
    },
    "TS475": {
        "name": "新能源",
        "type": TYPE_CONCEPT,
        "category": "新能源",
        "sentiment": ["比亚迪", "赛力斯"],
        "index_code": "000941.CSI",  # 新能源
    },
    "TS451": {
        "name": "智能电网",
        "type": TYPE_CONCEPT,
        "category": "新基建",
        "sentiment": ["国电南瑞", "思源电气"],
        "index_code": "470054.CNI",  # 深证智能电网R
    },
    "TS876": {
        "name": "AI芯片",
        "type": TYPE_CONCEPT,
        "category": "硬科技",
        "sentiment": ["寒武纪", "海光信息"],
        "index_code": "931071.CSI",  # 人工智能
    },
    "TS709": {
        "name": "军工",
        "type": TYPE_CONCEPT,
        "category": "国防",
        "sentiment": ["中航沈飞", "航发动力"],
        "index_code": "399967.SZ",  # 中证军工
    },
}

# 用户自定义板块文件
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HISTORY_FILE = os.path.join(DATA_DIR, "sector_tracking.json")
CUSTOM_FILE = os.path.join(DATA_DIR, "sector_custom.json")

# ── 5 级情绪体系 ─────────────────────────────────────────────
HEAT_LEVELS = {
    5: "燃烧",   # 周涨>3% + 量增>10% + 连涨≥3天
    4: "发热",   # 周涨>2% + 量增或连涨≥2天
    3: "常温",   # 其他正常波动
    2: "退潮",   # 周跌>1% 或 量缩>20%
    1: "冰点",   # 周跌>2% + 量缩>30% + 连跌≥2天
}


def load_custom_sectors():
    """加载用户自定义板块"""
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
                custom = json.load(f)
                # 合并到 SECTOR_MAP
                for code, info in custom.items():
                    if code not in SECTOR_MAP:
                        SECTOR_MAP[code] = info
        except Exception:
            pass


def save_custom_sectors():
    """保存用户自定义板块"""
    os.makedirs(DATA_DIR, exist_ok=True)
    # 只保存非默认的板块
    custom = {}
    for code, info in SECTOR_MAP.items():
        # 检查是否在默认列表中（通过判断是否是后来添加的）
        custom[code] = info
    with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
        json.dump(custom, f, ensure_ascii=False, indent=2)


def add_sector(code, name, category="自定义", sentiment=None):
    """添加板块"""
    if code.startswith("TS"):
        stype = TYPE_CONCEPT
    else:
        stype = TYPE_SW
    SECTOR_MAP[code] = {
        "name": name,
        "type": stype,
        "category": category,
        "sentiment": sentiment or [],
    }
    save_custom_sectors()
    return True


def remove_sector(code):
    """删除板块"""
    if code in SECTOR_MAP:
        del SECTOR_MAP[code]
        save_custom_sectors()
        return True
    return False


def search_sectors(keyword):
    """搜索板块（申万二级 + 概念）"""
    results = []

    # 搜索申万二级
    try:
        df_sw = pro.index_classify(level="L2", src="SW2021")
        for _, row in df_sw.iterrows():
            if keyword in row["industry_name"]:
                results.append({
                    "code": row["index_code"],
                    "name": row["industry_name"],
                    "type": TYPE_SW,
                    "source": "申万二级",
                })
    except Exception as e:
        print(f"  搜索申万行业失败: {e}")

    # 搜索概念板块
    try:
        df_concept = pro.concept(src="ts")
        for _, row in df_concept.iterrows():
            if keyword in row["name"]:
                results.append({
                    "code": row["code"],
                    "name": row["name"],
                    "type": TYPE_CONCEPT,
                    "source": "概念板块",
                })
    except Exception as e:
        print(f"  搜索概念板块失败: {e}")

    return results


def get_weekly_data(ts_code, sector_type, weeks=4, index_code=None):
    """获取行业指数的周线和日线数据
    index_code: 概念板块对应的真实指数代码（如 000685.SH）
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(weeks=weeks + 2)).strftime("%Y%m%d")

    try:
        if sector_type == TYPE_SW:
            df_daily = pro.sw_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            time.sleep(0.35)
            df_weekly = pro.index_weekly(ts_code=ts_code, start_date=start_date, end_date=end_date)
            time.sleep(0.35)
            return df_daily, df_weekly
        else:
            # 概念板块：优先使用真实指数代码
            code = index_code or ts_code
            df_daily = pro.index_daily(ts_code=code, start_date=start_date, end_date=end_date)
            time.sleep(0.35)
            df_weekly = pro.index_weekly(ts_code=code, start_date=start_date, end_date=end_date)
            time.sleep(0.35)
            # 如果返回有效数据（非空且非全零），直接使用
            if (df_daily is not None and len(df_daily) > 0
                    and df_daily['close'].sum() > 0):
                return df_daily, df_weekly
            # 否则使用成分股方法
            print("(成分股)", end=" ")
            return calc_concept_metrics(ts_code, weeks=weeks)
    except Exception:
        if sector_type == TYPE_CONCEPT:
            print("(成分股)", end=" ")
            try:
                return calc_concept_metrics(ts_code, weeks=weeks)
            except Exception as e2:
                print(f"\n  成分股方法也失败: {e2}")
                return None, None
        print(f"  获取 {ts_code} 数据失败")
        return None, None


def calc_consecutive_days(df_daily, direction="up"):
    """计算连续上涨/下跌天数"""
    if df_daily is None or len(df_daily) == 0:
        return 0

    df = df_daily.sort_values("trade_date", ascending=False)
    count = 0
    for _, row in df.iterrows():
        pct = row.get("pct_change", row.get("pct_chg", 0))
        # 概念板块 index_daily 的 pct_chg 是小数形式
        if abs(pct) < 1 and pct != 0:
            pct = pct * 100
        if direction == "up" and pct > 0:
            count += 1
        elif direction == "down" and pct < 0:
            count += 1
        else:
            break
    return count


def calc_weekly_metrics(df_daily, df_weekly):
    """计算周维度热度指标"""
    result = {
        "weekly_change": 0,
        "amount_change_pct": 0,
        "consecutive_up": 0,
        "consecutive_down": 0,
        "volatility": 0,
        "volume": 0,      # 本周成交量（手）
        "amount": 0,      # 本周成交额（万元）
    }

    now = datetime.now()
    current_week = now.isocalendar()[1]
    current_year = now.year

    # 尝试从周线获取本周数据
    weekly_got = False
    if df_weekly is not None and len(df_weekly) >= 1:
        latest_week = df_weekly.iloc[0]
        week_date = latest_week.get("trade_date", "")
        if week_date:
            try:
                wd = pd.to_datetime(week_date)
                week_is_current = (wd.isocalendar()[1] == current_week
                                   and wd.year == current_year)
            except Exception:
                week_is_current = False
        else:
            week_is_current = False

        if week_is_current:
            raw_pct = latest_week.get("pct_chg", 0)
            result["weekly_change"] = raw_pct * 100 if abs(raw_pct) < 1 else raw_pct
            result["volume"] = latest_week.get("vol", 0)
            result["amount"] = latest_week.get("amount", 0)
            weekly_got = True

        if len(df_weekly) >= 2:
            this_week_amount = latest_week.get("amount", 0)
            last_week_amount = df_weekly.iloc[1].get("amount", 0)
            if last_week_amount > 0:
                result["amount_change_pct"] = (
                    (this_week_amount - last_week_amount) / last_week_amount * 100
                )

    # 如果周线没有本周数据，从日线计算
    if not weekly_got and df_daily is not None and len(df_daily) > 0:
        # 筛选本周的日线数据
        try:
            weeks = pd.to_datetime(df_daily['trade_date']).dt.isocalendar().week.values
            years = pd.to_datetime(df_daily['trade_date']).dt.year.values
            mask = (weeks == current_week) & (years == current_year)
            df_this_week = df_daily[mask]
        except Exception:
            df_this_week = pd.DataFrame()

        if len(df_this_week) >= 2:
            first_close = df_this_week.iloc[0]['close']
            last_close = df_this_week.iloc[-1]['close']
            if first_close > 0:
                result["weekly_change"] = (last_close - first_close) / first_close * 100
            # 本周成交量和成交额
            if 'vol' in df_this_week.columns:
                result["volume"] = df_this_week['vol'].sum()
            if 'amount' in df_this_week.columns:
                result["amount"] = df_this_week['amount'].sum()
            # 成交额变化：本周 vs 上周
            try:
                prev_mask = (weeks == current_week - 1) & (years == current_year)
                df_prev_week = df_daily[prev_mask]
                if len(df_prev_week) > 0 and 'amount' in df_this_week.columns:
                    this_amt = df_this_week['amount'].sum()
                    prev_amt = df_prev_week['amount'].sum()
                    if prev_amt > 0:
                        result["amount_change_pct"] = (this_amt - prev_amt) / prev_amt * 100
            except Exception:
                pass
        elif len(df_this_week) == 1 and 'pct_chg' in df_this_week.columns:
            # 只有1天数据，用 pct_chg
            raw = df_this_week.iloc[0].get('pct_chg', 0)
            result["weekly_change"] = raw * 100 if abs(raw) < 1 else raw

    if df_daily is not None and len(df_daily) > 0:
        result["consecutive_up"] = calc_consecutive_days(df_daily, "up")
        result["consecutive_down"] = calc_consecutive_days(df_daily, "down")

        recent = df_daily.tail(5)
        if len(recent) > 0:
            if "high" in recent.columns and "low" in recent.columns:
                high = recent["high"].max()
                low = recent["low"].min()
            else:
                high = recent["close"].max()
                low = recent["close"].min()
            mid = (high + low) / 2
            if mid > 0:
                result["volatility"] = (high - low) / mid * 100

    return result


def calc_concept_metrics(concept_id, weeks=4, max_stocks=5):
    """
    用成分股计算概念板块的热度指标。

    核心方法：先将每只股票归一化到基准100（第一天=100），
    再取等权平均，这样每只股票对指数的贡献相同，
    不会因为某只高价股主导整个指数。
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=weeks * 30 + 10)).strftime("%Y%m%d")

    # 1) 获取成分股列表
    try:
        df_detail = pro.concept_detail(id=concept_id)
    except Exception as e:
        print(f"获取成分股失败: {e}")
        return None, None

    if df_detail is None or len(df_detail) == 0:
        return None, None

    stock_codes = df_detail['ts_code'].tolist()[:max_stocks]

    # 2) 逐只获取日线数据，并归一化到基准100
    normalized_list = []
    all_raw = []
    for code in stock_codes:
        try:
            df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                all_raw.append(df)
                # 归一化：第一天 close = 100
                base = df.iloc[0]['close']
                if base > 0:
                    df_norm = df[['trade_date', 'close']].copy()
                    df_norm['close'] = df['close'] / base * 100
                    df_norm['vol'] = df['vol']
                    df_norm['amount'] = df['amount']
                    normalized_list.append(df_norm)
        except Exception:
            pass
        time.sleep(0.35)

    if not normalized_list:
        return None, None

    # 3) 等权平均归一化后的价格，构建合成日线
    combined = pd.concat(normalized_list)
    daily = combined.groupby('trade_date').agg({
        'close': 'mean',
        'vol': 'sum',
        'amount': 'sum',
    }).reset_index().sort_values('trade_date')

    # 用合成日线算每日涨跌幅
    daily['pct_chg'] = daily['close'].pct_change() * 100

    # 4) 按自然周分组，计算合成周线
    daily_copy = daily.copy()
    daily_copy['dt'] = pd.to_datetime(daily_copy['trade_date'])
    daily_copy['week'] = daily_copy['dt'].dt.isocalendar().week.values
    daily_copy['year'] = daily_copy['dt'].dt.year

    weekly_rows = []
    for (year, week), wg in daily_copy.groupby(['year', 'week']):
        wg_sorted = wg.sort_values('trade_date')
        weekly_rows.append({
            'trade_date': wg_sorted['trade_date'].iloc[-1],
            'close': wg_sorted['close'].iloc[-1],
            'vol': wg_sorted['vol'].sum(),
            'amount': wg_sorted['amount'].sum(),
        })

    df_synthetic_weekly = pd.DataFrame(weekly_rows)
    df_synthetic_weekly['pct_chg'] = df_synthetic_weekly['close'].pct_change() * 100
    df_synthetic_weekly = df_synthetic_weekly.iloc[::-1].reset_index(drop=True)

    # 5) 计算热度指标
    result = {
        'weekly_change': 0,
        'amount_change_pct': 0,
        'consecutive_up': 0,
        'consecutive_down': 0,
        'volatility': 0,
    }

    if len(df_synthetic_weekly) >= 1:
        latest = df_synthetic_weekly.iloc[0]
        result['weekly_change'] = latest.get('pct_chg', 0) or 0

        if len(df_synthetic_weekly) >= 2:
            this_amt = latest.get('amount', 0)
            prev_amt = df_synthetic_weekly.iloc[1].get('amount', 0)
            if prev_amt > 0:
                result['amount_change_pct'] = (this_amt - prev_amt) / prev_amt * 100

    # 连续涨跌天数
    if len(daily) > 1:
        closes = daily['close'].values
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                result['consecutive_up'] += 1
            else:
                break
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                result['consecutive_down'] += 1
            else:
                break

    # 波动率（最近5个交易日）
    recent = daily.tail(5)
    if len(recent) > 0:
        high = recent['close'].max()
        low = recent['close'].min()
        mid = (high + low) / 2
        if mid > 0:
            result['volatility'] = (high - low) / mid * 100

    return daily, df_synthetic_weekly


def judge_heat(metrics):
    """判断 5 级情绪"""
    change = metrics["weekly_change"]
    amount_change = metrics["amount_change_pct"]
    consec_up = metrics["consecutive_up"]
    consec_down = metrics["consecutive_down"]

    # [5] 燃烧：周涨>3% + 量增>10% + 连涨≥3天
    if change > 3 and amount_change > 10 and consec_up >= 3:
        return 5

    # [4] 发热：周涨>2% + (量增或连涨≥2天)
    if change > 2 and (amount_change > 0 or consec_up >= 2):
        return 4

    # [1] 冰点：周跌>2% + 量缩>30% + 连跌≥2天
    if change < -2 and amount_change < -30 and consec_down >= 2:
        return 1

    # [2] 退潮：周跌>1% 或 量缩>20%
    if change < -1 or amount_change < -20:
        return 2

    # [3] 常温：其他
    return 3


def format_pct(value, show_sign=True):
    """格式化百分比"""
    if show_sign:
        return f"{value:+.1f}%"
    return f"{value:.1f}%"


def get_sentiment_stocks(stock_names):
    """获取情绪标的5日涨幅"""
    results = []
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")

    for name in stock_names:
        try:
            # 按名称查 ts_code
            df_info = pro.stock_basic(name=name, fields="ts_code,name")
            if df_info is None or len(df_info) == 0:
                continue
            ts_code = df_info.iloc[0]["ts_code"]

            # 取日线数据
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is None or len(df) < 2:
                continue

            df = df.sort_values("trade_date")
            # 取最近5个交易日的涨跌幅
            recent = df.tail(5)
            if len(recent) >= 2:
                first_close = recent.iloc[0]["close"]
                last_close = recent.iloc[-1]["close"]
                if first_close > 0:
                    change_5d = (last_close - first_close) / first_close * 100
                    results.append({
                        "name": name,
                        "ts_code": ts_code,
                        "change_5d": round(change_5d, 2),
                    })
            time.sleep(0.35)  # 限流
        except Exception as e:
            print(f"\n  情绪标 {name} 获取失败: {e}")
            continue

    return results


def run_tracking(weeks=1):
    """运行板块跟踪"""
    load_custom_sectors()

    print(f"\n{'='*60}")
    print(f"板块跟踪周报 — {datetime.now().strftime('%Y-%m-%d')} (第{datetime.now().isocalendar()[1]}周)")
    print(f"{'='*60}\n")

    all_results = []

    for ts_code, info in SECTOR_MAP.items():
        print(f"  获取 {info['name']} ({info['type']})...", end=" ")
        df_daily, df_weekly = get_weekly_data(
            ts_code, info["type"], weeks=weeks,
            index_code=info.get("index_code")
        )
        metrics = calc_weekly_metrics(df_daily, df_weekly)
        heat = judge_heat(metrics)

        # 获取情绪标的5日涨幅
        sentiment_names = info.get("sentiment", [])
        sentiment_stocks = get_sentiment_stocks(sentiment_names)

        result = {
            "ts_code": ts_code,
            "name": info["name"],
            "type": info["type"],
            "sentiment": info.get("sentiment", []),
            "sentiment_stocks": sentiment_stocks,
            "category": info.get("category", ""),
            "heat": heat,
            "heat_name": HEAT_LEVELS[heat],
            **metrics,
        }
        all_results.append(result)
        print(f"[{heat}] {HEAT_LEVELS[heat]}")

    # 按热度等级降序、再按周涨跌幅降序
    all_results.sort(key=lambda x: (x["heat"], x["weekly_change"]), reverse=True)

    # 输出表格
    print(f"\n{'─'*72}")
    print(
        f"{'板块':<14} {'类别':<8} {'周涨跌':>8} {'成交变化':>8} {'连涨天':>6} {'波动':>6} {'情绪':>6}  {'情绪标(5日涨幅)'}"
    )
    print(f"{'─'*72}")

    for r in all_results:
        consec = r["consecutive_up"] if r["consecutive_up"] > 0 else -r["consecutive_down"]
        consec_str = f"{consec:+d}" if consec != 0 else "0"

        # 情绪标显示
        sentiment_str = ""
        for s in r.get("sentiment_stocks", []):
            sentiment_str += f"{s['name']}{format_pct(s['change_5d'])} "
        if not sentiment_str:
            sentiment_str = "/".join(r.get("sentiment", []))

        print(
            f"{r['name']:<12} "
            f"{r['category']:<6} "
            f"{format_pct(r['weekly_change']):>8} "
            f"{format_pct(r['amount_change_pct']):>8} "
            f"{consec_str:>6} "
            f"{format_pct(r['volatility'], show_sign=False):>6} "
            f"[{r['heat']}] {r['heat_name']:>4}  "
            f"{sentiment_str}"
        )

    print(f"{'─'*72}")

    # 情绪分布统计
    heat_counts = {i: 0 for i in range(1, 6)}
    for r in all_results:
        heat_counts[r["heat"]] += 1
    print(f"\n情绪分布：")
    for level in range(5, 0, -1):
        count = heat_counts[level]
        if count > 0:
            bars = "█" * count
            print(f"  [{level}] {HEAT_LEVELS[level]:>2}  {bars} {count}")

    # 保存历史
    save_history(all_results)

    return all_results


def save_history(results):
    """保存跟踪历史到JSON"""
    os.makedirs(DATA_DIR, exist_ok=True)

    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass

    week_key = f"{datetime.now().year}-W{datetime.now().isocalendar()[1]:02d}"
    history[week_key] = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "results": results,
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n历史已保存到: {HISTORY_FILE}")


if __name__ == "__main__":
    load_custom_sectors()

    if "--list" in sys.argv:
        print(f"\n当前跟踪 {len(SECTOR_MAP)} 个板块：")
        for code, info in SECTOR_MAP.items():
            print(f"  {code:<12} {info['name']:<12} [{info['type']}] {info['category']}")
        sys.exit(0)

    if "--search" in sys.argv:
        idx = sys.argv.index("--search")
        if idx + 1 < len(sys.argv):
            keyword = sys.argv[idx + 1]
            results = search_sectors(keyword)
            print(f"\n搜索「{keyword}」找到 {len(results)} 个结果：")
            for r in results:
                tracked = " ✓ 已跟踪" if r["code"] in SECTOR_MAP else ""
                print(f"  {r['code']:<12} {r['name']:<16} [{r['source']}]{tracked}")
        else:
            print("用法: --search <关键词>")
        sys.exit(0)

    if "--add" in sys.argv:
        idx = sys.argv.index("--add")
        if idx + 2 < len(sys.argv):
            code = sys.argv[idx + 1]
            name = sys.argv[idx + 2]
            add_sector(code, name)
            print(f"已添加: {code} {name}")
        else:
            print("用法: --add <代码> <名称>")
        sys.exit(0)

    if "--remove" in sys.argv:
        idx = sys.argv.index("--remove")
        if idx + 1 < len(sys.argv):
            code = sys.argv[idx + 1]
            if remove_sector(code):
                print(f"已删除: {code}")
            else:
                print(f"未找到: {code}")
        else:
            print("用法: --remove <代码>")
        sys.exit(0)

    weeks = 1
    for i, arg in enumerate(sys.argv):
        if arg == "--weeks" and i + 1 < len(sys.argv):
            weeks = int(sys.argv[i + 1])
    run_tracking(weeks=weeks)
