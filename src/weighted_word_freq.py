#!/usr/bin/env python3
"""冰冰小美 加权词频分析

核心逻辑：
- 交易体系（20篇）：权重 15x — 最核心框架
- 专栏（101篇）：权重 10x — 深度分析
- 三要素案例（42篇）：权重 6x — 实战应用
- 贴子：
  >3000字：权重 5x — 超长帖
  >1000字：权重 3x — 长帖
  >500字：权重 2x — 中帖
  >200字：权重 1x — 普通帖
  <=200字：权重 0.5x — 碎片帖
"""

import os, re, json
from collections import Counter
from datetime import datetime
import jieba
import jieba.analyse

# ============================================================
# 路径配置 — 从环境变量读取
# ============================================================
BASE = os.environ.get("BBXM_VAULT_PATH", os.path.expanduser("~/Obsidian/想想冰美怎么做/输入"))
POSTS_DIR = os.path.join(BASE, "贴子")
COLUMNS_DIR = os.path.join(BASE, "专栏")
SYSTEM_DIR = os.path.join(BASE, "交易体系")
CASES_DIR = os.path.join(BASE, "三要素案例")
OUTPUT_DIR = os.environ.get("BBXM_OUTPUT_DIR", os.path.join(os.path.dirname(BASE), "知识体系 DeepSeek 生成"))

# ============================================================
# 停用词
# ============================================================
STOPWORDS = set([
    '的','了','是','在','有','和','就','不','都','一','一个',
    '上','也','很','到','说','要','去','会','着','没有','好',
    '自己','这','他','她','它','们','那','被','从','把','过','对','以',
    '而','但','与','让','中','来','什么','可以','没','吧',
    '啊','呢','吗','哦','嗯','哈','呀','啦','嘛','么','哈哈','哈哈哈',
    '还是','或者','因为','所以','如果','虽然','但是','然后','可能',
    '应该','其实','这样','那样','怎么','为什么','多少',
    '一些','一下','一直','一样','不是','不能','不会','只是','只要','只有',
    '而且','而是','否则','除了','关于','之后','之前','以上','以下','以及',
    '这种','这个','那个','这些','那些','那么','这么','这是','那是',
    '就是','还是','只是','正是','也是','不是',
    '因此','所以','因为','如果','虽然','但是','然而','不过','而且',
    '并且','或者','此外','另外','同时','随后','接着','之后','之前',
    '对于','关于','按照','根据','通过','基于','为了','以及','以及',
    '如何','怎么','怎样','为何','是否','能否','可以',
    '比如','例如','包括','等等','之类','等等',
    '需要','能够','可以','应该','必须','将会','可能',
    '目前','现在','当前','最近','今日','今天','昨日','昨天','明日','明天',
    '已经','正在','开始','继续','仍然','依旧','依然','始终',
    '之前','以后','以来','以后','之后',
    '一次','再次','最后','最终','终于','第一次',
    '觉得','认为','感觉','发现','知道','看到','想到','觉得',
    '非常','比较','特别','尤其','更加','相当','十分','最为',
    '很多','大多数','绝大多数','部分','大部分','几乎','差不多',
    '往往','经常','总是','一般','只是','主要','基本',
    '更为','更加','越来越','越来越','显得',
    '故而','因此','于是','所以','从而','进而','然后','接着',
    '不同','相同','类似','一样','同样','一种',
    '整个','全部','所有','某些','某个','每个',
    '里面','外面','上面','下面','前面','后面','这里','那里',
    '时候','情况','方面','过程','结果','原因','问题','方式',
    '带来','导致','引起','产生','出现','发生','形成','成为',
    '进行','给予','使得','做出','来自','受到','得以',
    '有人','每个人','别人','大家','人们','很多人','投资者','股民',
    'br','nbsp','md','http','https','com','www','amp',
    '10','20','30','50','100','200','300','500',
])

# ============================================================
# 自定义词典
# ============================================================
CUSTOM_WORDS = [
    '冰冰小美','情绪标','挣钱效应','亏钱效应','情绪周期','竞争格局',
    '流动性','三要素','价值投机','情绪溢价','核按钮','冲天炮',
    '空间板','连板','情绪冰点','情绪高潮','情绪转折','套利',
    '龙头','游资','散户','机构','量化','国家队',
    '抱团','分化','轮动','回流','修复','退潮',
    '冰点的美','亏钱认知','风险减弱','情绪螺旋',
    '美联储','央妈','央行','降息','加息','放水','缩表',
    '美元','人民币','汇率','国债','美债','利率',
    '去杠杆','中央加杠杆','特别国债','财政赤字',
    '创业板','科创板','北交所','港股','美股','A股','H股',
    '上证','深证','沪深','中证','国证',
    '人工智能','半导体','新能源','光伏','锂电池','储能',
    '芯片','光刻机','光刻胶','先进封装','成熟制程',
    '商业航天','低空经济','机器人','自动驾驶',
    '算力','数据中心','云计算',
    '黄金','白银','铜','铝','锌','镍','锡','铅',
    '原油','石油','天然气','煤炭',
    '碳酸锂','氢氧化锂','钴','稀土','钨','锑','锗','镓',
    '铁矿石','螺纹钢','热卷','焦煤','焦炭',
    '比特币','以太坊','加密货币',
    '大豆','玉米','小麦','棉花','白糖','橡胶',
    '华为','苹果','英伟达','特斯拉','比亚迪','宁德时代',
    '贵州茅台','中芯国际','赛力斯','小米','美的','格力',
    '万华化学','柳工','三一重工','浙江鼎力',
    '紫金矿业','洛阳钼业','西部矿业','北方铜业',
    '中际旭创','新易盛','天孚通信','光迅科技',
    '长电科技','闻泰科技','通富微电','华天科技',
    '海康威视','大华股份','科大讯飞',
    '药明康德','恒瑞医药','迈瑞医疗',
    '工商银行','建设银行','招商银行','农业银行',
    '中国石油','中国石化','中国海油','中国神华',
    '中国移动','中国电信','中国联通',
    '中国船舶','中国重工','中国卫星','中国卫通',
    '新洁能','信维通信','欧菲光','歌尔股份',
    '江淮汽车','长安汽车','长城汽车','赛力斯',
    '腾讯','阿里巴巴','美团','京东','拼多多','百度',
    '微软','谷歌','亚马逊','Meta','OpenAI','台积电','三星','海力士',
    '关税','贸易战','制裁','脱钩','一带一路','门罗主义','页岩油','欧佩克',
    # 化工
    '化工','产能','库存','开工率','周期股','供给侧',
    '万华','巴斯夫','MDI','聚氨酯','乙烯','丙烯','纯碱','烧碱',
    '钛白粉','氟化工','磷化工','有机硅','煤化工','石油化工','大炼化',
    '六氟磷酸锂','磷酸铁锂','恩捷股份','天赐材料','多氟多',
    '荣盛石化','恒力石化','恒逸石化','桐昆股份',
    '华鲁恒升','鲁西化工','宝丰能源','龙佰集团',
    '巨化股份','新安股份','合盛硅业','兴发集团','云天化',
    '中国化学','中化国际','中泰化学',
]
for w in CUSTOM_WORDS:
    jieba.add_word(w)

# ============================================================
# 文本提取
# ============================================================
def extract_text(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'^---.*?---\s*', '', content, flags=re.DOTALL)
    content = re.sub(r'^#.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^>.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^- (转发|收藏|点赞|评论)数.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^- 转发.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^---\s*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'<[^>]+>', ' ', content)
    content = re.sub(r'\s+', ' ', content).strip()
    return content

# ============================================================
# 加权加载
# ============================================================
def get_post_weight(text_len, source):
    """根据来源和字数返回权重"""
    if source == "交易体系":
        return 15
    elif source == "专栏":
        return 10
    elif source == "三要素案例":
        return 6
    elif source == "贴子":
        if text_len > 3000:
            return 5
        elif text_len > 1000:
            return 3
        elif text_len > 500:
            return 2
        elif text_len > 200:
            return 1
        else:
            return 0.5
    return 1

def load_weighted():
    """加载所有内容，带权重"""
    sources = {
        POSTS_DIR: "贴子",
        COLUMNS_DIR: "专栏",
        SYSTEM_DIR: "交易体系",
        CASES_DIR: "三要素案例",
    }

    records = []  # (text, weight, source, filename)
    stats = {}

    for dirpath, source in sources.items():
        if not os.path.isdir(dirpath):
            continue
        count = 0
        total_weight = 0
        for fname in sorted(os.listdir(dirpath)):
            if fname.endswith('.md'):
                text = extract_text(os.path.join(dirpath, fname))
                if len(text) > 5:
                    weight = get_post_weight(len(text), source)
                    records.append((text, weight, source, fname))
                    total_weight += weight
                    count += 1
        stats[source] = (count, total_weight)

    return records, stats

# ============================================================
# 加权词频计算
# ============================================================
def weighted_word_freq(records, top_n=300):
    """加权词频：每个词的计数乘以文档权重"""
    counter = Counter()
    for text, weight, source, fname in records:
        words = jieba.lcut(text)
        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in STOPWORDS and not w.isdigit():
                counter[w] += weight

    return counter.most_common(top_n)

def unweighted_word_freq(records, top_n=300):
    """未加权词频（对比用）"""
    counter = Counter()
    for text, weight, source, fname in records:
        words = jieba.lcut(text)
        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in STOPWORDS and not w.isdigit():
                counter[w] += 1
    return counter.most_common(top_n)

# ============================================================
# 概念词加权频率
# ============================================================
def concept_weighted_freq(records):
    """已知概念词的加权频率"""
    concepts = [
        # 三要素框架
        '情绪','流动性','竞争格局','三要素','情绪标','情绪周期',
        '情绪冰点','情绪高潮','情绪溢价','情绪螺旋',
        # 风险/盈亏
        '风险','亏钱效应','挣钱效应','亏损','盈利','泡沫','危机',
        '恐慌','贪婪','假象','人性','恐惧',
        # 交易行为
        '交易','套利','短线','中线','长线','波段',
        '买入','卖出','持仓','空仓','建仓','减仓',
        '龙头','接力','核按钮','冲天炮','空间板','连板',
        '新高','跌停','涨停','突破','修复','退潮',
        # 市场结构
        '市场','行情','指数','个股','板块','A股','港股','美股','H股',
        '机构','游资','散户','量化','国家队','外资',
        '抱团','分化','轮动','回流','权重','题材',
        # 宏观
        '宏观','中观','微观','国运','产业','周期','趋势',
        '美元','人民币','汇率','利率','国债','美债',
        '央妈','央行','美联储','降息','加息','放水','缩表',
        '通胀','通缩','衰退','复苏',
        '关税','贸易战','制裁','脱钩','一带一路',
        '债务','杠杆','去杠杆','中央加杠杆','财政赤字',
        # 产业
        '科技','芯片','半导体','AI','人工智能','新能源','光伏','锂电','储能',
        '商业航天','机器人','自动驾驶','低空经济',
        '化工','产能','库存','周期股','供给侧',
        '制造业','房地产','汽车','消费','医药',
        # 资产
        '黄金','白银','铜','石油','原油','稀土','碳酸锂','比特币',
        # 交易哲学
        '价值投机','常识','护城河','安全边际','内在价值','复利',
        '博弈','格局','优势','竞争','发展','创新',
        '信心','节点','引导','行为','观察','理解','选择',
        '历史','未来','时代','改革','安全','稳定',
        '牛市','熊市','结构性',
    ]

    counter = Counter()
    for text, weight, source, fname in records:
        for concept in concepts:
            count = text.count(concept)
            if count > 0:
                counter[concept] += count * weight

    return counter.most_common(len(concepts))

# ============================================================
# 输出
# ============================================================
def print_top(title, items, n=30):
    print(f"\n{'='*60}")
    print(title)
    print('='*60)
    for i, (word, count) in enumerate(items[:n], 1):
        bar = "█" * (int(count) // 50)
        print(f"{i:3d}. {word:<14s} {int(count):>8d}  {bar}")

def save_results(weighted, unweighted, concepts, stats, records):
    filepath = os.path.join(OUTPUT_DIR, "加权词频分析.md")

    lines = []
    lines.append("# 冰冰小美 加权词频分析")
    lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 总篇数: {len(records):,}")
    lines.append("")

    # 权重方案说明
    lines.append("## 权重方案")
    lines.append("")
    lines.append("| 来源 | 条件 | 权重 |")
    lines.append("|------|------|------|")
    lines.append("| 交易体系 | 全部 20 篇 | 15x |")
    lines.append("| 专栏 | 全部 101 篇 | 10x |")
    lines.append("| 三要素案例 | 全部 42 篇 | 6x |")
    lines.append("| 贴子 | >3000字 | 5x |")
    lines.append("| 贴子 | >1000字 | 3x |")
    lines.append("| 贴子 | >500字 | 2x |")
    lines.append("| 贴子 | >200字 | 1x |")
    lines.append("| 贴子 | <=200字 | 0.5x |")
    lines.append("")

    # 各来源统计
    lines.append("## 内容统计")
    lines.append("")
    lines.append("| 来源 | 篇数 | 总权重 |")
    lines.append("|------|------|--------|")
    for source, (count, total_w) in stats.items():
        lines.append(f"| {source} | {count} | {total_w:,.0f} |")
    lines.append("")

    # 加权词频
    lines.append("## 加权词频 TOP 100")
    lines.append("")
    lines.append("| 排名 | 词汇 | 加权次数 |")
    lines.append("|------|------|----------|")
    for i, (word, count) in enumerate(weighted[:100], 1):
        lines.append(f"| {i} | {word} | {int(count):,} |")
    lines.append("")

    # 加权 vs 未加权对比
    lines.append("## 加权 vs 未加权 对比（词汇排名变化）")
    lines.append("")
    lines.append("| 词汇 | 加权排名 | 未加权排名 | 排名变化 | 变化方向 |")
    lines.append("|------|----------|------------|----------|----------|")

    w_rank = {w: i+1 for i, (w, _) in enumerate(weighted)}
    uw_rank = {w: i+1 for i, (w, _) in enumerate(unweighted)}

    changes = []
    for word in w_rank:
        if word in uw_rank:
            change = uw_rank[word] - w_rank[word]
            if abs(change) >= 3:
                changes.append((word, w_rank[word], uw_rank[word], change))
    changes.sort(key=lambda x: -x[3])  # 按排名上升幅度排序

    for word, wr, uwr, change in changes[:50]:
        direction = "↑ 加权上升" if change > 0 else "↓ 加权下降"
        lines.append(f"| {word} | {wr} | {uwr} | +{change} | {direction} |")
    lines.append("")

    # 加权概念词
    lines.append("## 加权概念词频率")
    lines.append("")
    lines.append("| 排名 | 概念 | 加权分数 |")
    lines.append("|------|------|----------|")
    for i, (word, score) in enumerate(concepts, 1):
        lines.append(f"| {i} | {word} | {int(score):,} |")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"\n结果已保存到: {filepath}")

# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    print("加载并加权...")
    records, stats = load_weighted()

    total_weight = sum(s[1] for s in stats.values())
    print(f"总篇数: {len(records):,}, 总权重: {total_weight:,.0f}")
    for source, (count, w) in stats.items():
        print(f"  {source}: {count}篇, 权重{w:,.0f} ({w/total_weight*100:.0f}%)")

    # 加权词频
    print("\n计算加权词频...")
    weighted = weighted_word_freq(records, 300)
    print_top("加权词频 TOP 50", weighted, 50)

    # 未加权词频（对比）
    print("\n计算未加权词频...")
    unweighted = unweighted_word_freq(records, 300)

    # 排名变化
    print(f"\n{'='*60}")
    print("加权后排名上升显著的概念（专栏/体系高频出现）")
    print('='*60)
    w_rank = {w: i+1 for i, (w, _) in enumerate(weighted)}
    uw_rank = {w: i+1 for i, (w, _) in enumerate(unweighted)}
    changes = []
    for word in w_rank:
        if word in uw_rank and w_rank[word] <= 100:
            change = uw_rank[word] - w_rank[word]
            if change >= 5:
                changes.append((word, w_rank[word], uw_rank[word], change))
    changes.sort(key=lambda x: -x[3])
    for word, wr, uwr, change in changes[:30]:
        print(f"  {word}: 未加权#{uwr} → 加权#{wr} (↑{change})")

    # 加权概念词
    print("\n计算概念词加权频率...")
    concepts = concept_weighted_freq(records)
    print_top("加权概念词频率 TOP 40", concepts, 40)

    # 保存
    save_results(weighted, unweighted, concepts, stats, records)
    print("\n完成！")
