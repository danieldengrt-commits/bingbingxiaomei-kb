#!/usr/bin/env python3
"""冰冰小美帖子词频分析"""

import os
import re
import json
from collections import Counter
import jieba
import jieba.analyse

# 路径 — 从环境变量读取 Obsidian vault 位置
VAULT_PATH = os.environ.get("BBXM_VAULT_PATH", ".")
POSTS_DIR = os.path.join(VAULT_PATH, "输入/贴子")
COLUMNS_DIR = os.path.join(VAULT_PATH, "输入/专栏")
SYSTEM_DIR = os.path.join(VAULT_PATH, "输入/交易体系")
CASES_DIR = os.path.join(VAULT_PATH, "输入/三要素案例")

# 停用词
STOPWORDS = set([
    # 结构助词/虚词
    '的', '了', '是', '在', '有', '和', '就', '不', '都', '一', '一个',
    '上', '也', '很', '到', '说', '要', '去', '会', '着', '没有', '好',
    '自己', '这', '他', '她', '它', '们', '那', '被', '从', '把', '过', '对', '以',
    '而', '但', '与', '让', '中', '来', '什么', '可以', '没', '吧',
    '啊', '呢', '吗', '哦', '嗯', '哈', '呀', '啦', '嘛', '么', '哈哈', '哈哈哈',
    '还是', '或者', '因为', '所以', '如果', '虽然', '但是', '然后', '可能',
    '应该', '其实', '这样', '那样', '怎么', '为什么', '多少',
    '一些', '一下', '一直', '一样', '不是', '不能', '不会', '只是', '只要', '只有',
    '而且', '而是', '否则', '除了', '关于', '之后', '之前', '以上', '以下', '以及',
    # 指代词/语气词
    '这种', '这个', '那个', '这些', '那些', '那么', '这么', '这是', '那是',
    '就是', '还是', '只是', '正是', '也是', '不是',
    # 连词/副词（无信息量）
    '因此', '所以', '因为', '如果', '虽然', '但是', '然而', '不过', '而且',
    '并且', '或者', '此外', '另外', '同时', '随后', '接着', '之后', '之前',
    '对于', '关于', '按照', '根据', '通过', '基于', '为了', '以及', '以及',
    '如何', '怎么', '怎样', '为何', '是否', '能否', '可以',
    '比如', '例如', '包括', '等等', '之类', '等等',
    '需要', '能够', '可以', '应该', '必须', '将会', '可能',
    # 时间/状态填充词
    '目前', '现在', '当前', '最近', '今日', '今天', '昨日', '昨天', '明日', '明天',
    '已经', '正在', '开始', '继续', '仍然', '依旧', '依然', '始终',
    '之前', '以后', '以来', '以后', '之后',
    '一次', '再次', '最后', '最终', '终于', '第一次',
    # 主观/模糊词
    '觉得', '认为', '感觉', '发现', '知道', '看到', '想到', '觉得',
    '非常', '比较', '特别', '尤其', '更加', '相当', '十分', '最为',
    '很多', '大多数', '绝大多数', '部分', '大部分', '几乎', '差不多',
    '往往', '经常', '总是', '一般', '只是', '主要', '基本',
    '更为', '更加', '越来越', '越来越', '显得',
    # 连接/填补
    '故而', '因此', '于是', '所以', '从而', '进而', '然后', '接着',
    '不同', '相同', '类似', '一样', '同样', '一种',
    '整个', '全部', '所有', '某些', '某个', '每个',
    '里面', '外面', '上面', '下面', '前面', '后面', '这里', '那里',
    '时候', '情况', '方面', '过程', '结果', '原因', '问题', '方式',
    '带来', '导致', '引起', '产生', '出现', '发生', '形成', '成为',
    '进行', '给予', '使得', '做出', '来自', '受到', '得以',
    # 人称/指代
    '有人', '每个人', '别人', '大家', '人们', '很多人', '投资者', '股民',
    # HTML/URL残留
    'br', 'nbsp', 'md', 'http', 'https', 'com', 'www', 'amp',
    # 纯数字
    '10', '20', '30', '50', '100', '200', '300', '500',
])

# 自定义词典（确保这些词被正确切分）
CUSTOM_WORDS = [
    # 冰美核心概念
    '冰冰小美', '情绪标', '挣钱效应', '亏钱效应', '情绪周期', '竞争格局',
    '流动性', '三要素', '价值投机', '情绪溢价', '核按钮', '冲天炮',
    '空间板', '连板', '情绪冰点', '情绪高潮', '情绪转折', '套利',
    '龙头', '游资', '散户', '机构', '量化', '国家队',
    '抱团', '分化', '轮动', '回流', '修复', '退潮',
    '冰点的美', '亏钱认知', '风险减弱', '情绪螺旋',
    # 宏观/政策
    '美联储', '央妈', '央行', '降息', '加息', '放水', '缩表',
    '美元', '人民币', '汇率', '国债', '美债', '利率',
    '去杠杆', '中央加杠杆', '特别国债', '财政赤字',
    # 市场/板块
    '创业板', '科创板', '北交所', '港股', '美股', 'A股', 'H股',
    '上证', '深证', '沪深', '中证', '国证',
    # 产业/科技
    '人工智能', '半导体', '新能源', '光伏', '锂电池', '储能',
    '芯片', '光刻机', '光刻胶', '先进封装', '成熟制程',
    '商业航天', '低空经济', '机器人', '自动驾驶',
    '算力', '数据中心', '云计算',
    # 商品/资产
    '黄金', '白银', '铜', '铝', '锌', '镍', '锡', '铅',
    '原油', '石油', '天然气', '煤炭',
    '碳酸锂', '氢氧化锂', '钴', '稀土', '钨', '锑', '锗', '镓',
    '铁矿石', '螺纹钢', '热卷', '焦煤', '焦炭',
    '比特币', '以太坊', '加密货币',
    '大豆', '玉米', '小麦', '棉花', '白糖', '橡胶',
    # 企业/个股
    '华为', '苹果', '英伟达', '特斯拉', '比亚迪', '宁德时代',
    '贵州茅台', '中芯国际', '赛力斯', '小米', '美的', '格力',
    '紫金矿业', '洛阳钼业', '西部矿业', '北方铜业',
    '万华化学', '柳工', '三一重工', '浙江鼎力',
    '中际旭创', '新易盛', '天孚通信', '光迅科技',
    '长电科技', '闻泰科技', '通富微电', '华天科技',
    '韦尔股份', '卓胜微', '汇顶科技', '兆易创新', '北京君正',
    '北方华创', '中微公司', '盛美上海', '拓荆科技',
    '海康威视', '大华股份', '科大讯飞', '商汤科技',
    '药明康德', '恒瑞医药', '迈瑞医疗', '百济神州',
    '工商银行', '建设银行', '招商银行', '农业银行',
    '中国石油', '中国石化', '中国海油', '中国神华',
    '中国移动', '中国电信', '中国联通',
    '中国船舶', '中国重工', '中国卫星', '中国卫通',
    '新洁能', '信维通信', '欧菲光', '歌尔股份',
    '江淮汽车', '长安汽车', '长城汽车', '吉利汽车',
    '腾讯', '阿里巴巴', '美团', '京东', '拼多多', '百度',
    # 国际企业
    '微软', '谷歌', '亚马逊', 'Meta', 'OpenAI', '台积电', '三星', '海力士',
    # 贸易/地缘
    '关税', '贸易战', '制裁', '脱钩', '一带一路', '门罗主义',
    '页岩油', '欧佩克',
]
for w in CUSTOM_WORDS:
    jieba.add_word(w)


def extract_text(filepath):
    """从md文件提取正文"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 去掉 frontmatter
    content = re.sub(r'^---.*?---\s*', '', content, flags=re.DOTALL)
    # 去掉标题行
    content = re.sub(r'^#.*$', '', content, flags=re.MULTILINE)
    # 去掉元数据行
    content = re.sub(r'^>.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^- (转发|收藏|点赞|评论)数.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^---\s*$', '', content, flags=re.MULTILINE)
    # 去掉 HTML 标签
    content = re.sub(r'<[^>]+>', ' ', content)
    # 去掉多余空白
    content = re.sub(r'\s+', ' ', content).strip()
    return content


def load_all_posts():
    """加载所有帖子文本"""
    texts = []
    filenames = []

    # 贴子目录
    if os.path.isdir(POSTS_DIR):
        for fname in sorted(os.listdir(POSTS_DIR)):
            if fname.endswith('.md'):
                text = extract_text(os.path.join(POSTS_DIR, fname))
                if len(text) > 5:
                    texts.append(text)
                    filenames.append(f"贴子/{fname}")

    # 专栏
    if os.path.isdir(COLUMNS_DIR):
        for fname in sorted(os.listdir(COLUMNS_DIR)):
            if fname.endswith('.md'):
                text = extract_text(os.path.join(COLUMNS_DIR, fname))
                if len(text) > 5:
                    texts.append(text)
                    filenames.append(f"专栏/{fname}")

    # 交易体系
    if os.path.isdir(SYSTEM_DIR):
        for fname in sorted(os.listdir(SYSTEM_DIR)):
            if fname.endswith('.md'):
                text = extract_text(os.path.join(SYSTEM_DIR, fname))
                if len(text) > 5:
                    texts.append(text)
                    filenames.append(f"体系/{fname}")

    # 三要素案例
    if os.path.isdir(CASES_DIR):
        for fname in sorted(os.listdir(CASES_DIR)):
            if fname.endswith('.md'):
                text = extract_text(os.path.join(CASES_DIR, fname))
                if len(text) > 5:
                    texts.append(text)
                    filenames.append(f"案例/{fname}")

    return texts, filenames


def word_frequency(texts, top_n=200):
    """词频统计"""
    counter = Counter()
    for text in texts:
        words = jieba.lcut(text)
        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in STOPWORDS and not w.isdigit():
                counter[w] += 1
    return counter.most_common(top_n)


def bigram_frequency(texts, top_n=100):
    """二元词组频率"""
    counter = Counter()
    for text in texts:
        words = jieba.lcut(text)
        words = [w.strip() for w in words if len(w.strip()) >= 2 and w.strip() not in STOPWORDS]
        for i in range(len(words) - 1):
            bigram = f"{words[i]}{words[i+1]}"
            if len(bigram) >= 4:
                counter[bigram] += 1
    return counter.most_common(top_n)


def extract_keywords_tfidf(texts, top_n=100):
    """TF-IDF 关键词提取"""
    all_text = '\n'.join(texts)
    keywords = jieba.analyse.extract_tags(all_text, topK=top_n, withWeight=True)
    return keywords


def entity_frequency(texts, top_n=100):
    """个股 + 资产 + 商品提及频率"""
    entities = [
        # A股核心标的
        '比亚迪', '宁德时代', '贵州茅台', '中芯国际', '赛力斯', '美的', '格力',
        '万华化学', '柳工', '三一重工', '浙江鼎力', '徐工机械',
        '紫金矿业', '洛阳钼业', '西部矿业', '北方铜业', '江西铜业',
        '中际旭创', '新易盛', '天孚通信', '光迅科技',
        '长电科技', '闻泰科技', '通富微电', '华天科技', '晶方科技',
        '韦尔股份', '卓胜微', '兆易创新', '北京君正', '汇顶科技',
        '北方华创', '中微公司', '盛美上海', '拓荆科技',
        '海康威视', '大华股份', '科大讯飞', '寒武纪',
        '药明康德', '恒瑞医药', '迈瑞医疗', '百济神州',
        '工商银行', '建设银行', '招商银行', '农业银行',
        '中国石油', '中国石化', '中国海油', '中国神华',
        '中国移动', '中国电信', '中国联通',
        '中国船舶', '中国重工', '中国卫星', '中国卫通',
        '新洁能', '信维通信', '欧菲光', '歌尔股份', '立讯精密',
        '江淮汽车', '比亚迪', '长安汽车', '长城汽车', '赛力斯',
        '华力创通', '捷荣技术', '华映科技', '光弘科技', '欧菲光',
        '焦点科技', '小商品城', '四方精创', '江波龙',
        '银宝山新', '亚世光电', '福日电子', '鸿博股份', '信雅达',
        '高新发展', '荣科科技', '中科曙光', '永达股份',
        '思美传媒', '天威视讯', '中文在线', '万达电影',
        '海尔智家', '海尔', '美的集团', '格力电器',
        '金龙羽', '中国卫星', '拉卡拉', '中油资本',
        # 国际企业
        '英伟达', '特斯拉', '苹果', '微软', '谷歌', '亚马逊', 'Meta',
        '台积电', '三星', '海力士', 'OpenAI', '博通', 'ASML',
        '华为', '小米', '腾讯', '阿里巴巴', '美团', '京东', '百度', '拼多多',
        # 商品/资源
        '黄金', '白银', '铜', '铝', '锌', '镍', '锡', '铅',
        '原油', '石油', '天然气', '煤炭',
        '碳酸锂', '氢氧化锂', '钴', '稀土', '钨', '锑', '锗', '镓',
        '铁矿石', '螺纹钢', '热卷', '焦煤', '焦炭',
        '比特币', '以太坊', '加密货币',
        '大豆', '玉米', '小麦', '棉花', '白糖', '橡胶',
        # 板块/指数
        '上证', '深证', '沪深300', '中证500', '中证1000',
        '创业板', '科创板', '北交所', '恒生', '纳斯达克', '标普',
        '芯片ETF', '半导体ETF', '科创50', '科创100',
        # 基金/大V
        '广发', '易方达', '华夏', '南方', '富国', '嘉实', '博时', '招商基金',
        '高瓴', '高毅', '景林', '淡水泉',
    ]

    counter = Counter()
    for text in texts:
        for entity in entities:
            count = text.count(entity)
            if count > 0:
                counter[entity] += count
    return counter.most_common(top_n)


def concept_frequency(texts, top_n=50):
    """概念词频率"""
    concepts = [
        '情绪', '流动性', '竞争格局', '三要素', '挣钱效应', '亏钱效应',
        '情绪标', '情绪周期', '情绪冰点', '情绪高潮', '情绪溢价',
        '价值投机', '套利', '龙头', '游资', '散户', '机构', '量化',
        '抱团', '分化', '轮动', '回流', '修复', '退潮',
        '国运', '产业', '趋势', '周期', '景气度',
        '美联储', '央行', '降息', '加息', '放水',
        '美元', '人民币', '汇率', '国债', '利率',
        '护城河', '安全边际', '内在价值', '复利', '常识',
        '假象', '真相', '人性', '贪婪', '恐惧', '羊群效应',
        '核按钮', '冲天炮', '空间板', '连板', '断板',
        '涨停', '跌停', '新高', '新低', '突破',
        '做空', '做多', '买入', '卖出', '持仓', '空仓',
        '短线', '中线', '长线', '波段',
        '宏观', '中观', '微观',
        '通胀', '通缩', '衰退', '复苏', '繁荣', '萧条',
        '半导体', '新能源', '光伏', '锂电', '储能', '人工智能', 'AI',
        '华为', '苹果', '英伟达',
        '一带一路', '中特估', '国企改革',
        '关税', '贸易战', '制裁', '脱钩',
    ]

    counter = Counter()
    for text in texts:
        for concept in concepts:
            count = text.count(concept)
            if count > 0:
                counter[concept] += count
    return counter.most_common(top_n)


OUTPUT_DIR = os.environ.get("BBXM_OUTPUT_DIR", os.path.join(VAULT_PATH, "知识体系 DeepSeek 生成"))


def save_results(freq, cf, sf, tfidf, bi, texts):
    """保存结果到文件"""
    filepath = os.path.join(OUTPUT_DIR, "词频分析.md")

    total_chars = sum(len(t) for t in texts)

    lines = []
    from datetime import datetime
    lines.append("# 冰冰小美帖子词频分析")
    lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 总篇数: {len(texts):,}")
    lines.append(f"> 总字数: {total_chars:,}")
    lines.append("")

    # 1. 全部词频
    lines.append("## 全量词频排名")
    lines.append("")
    lines.append("| 排名 | 词汇 | 次数 |")
    lines.append("|------|------|------|")
    for i, (word, count) in enumerate(freq, 1):
        lines.append(f"| {i} | {word} | {count:,} |")

    # 2. 概念词频率
    lines.append("\n## 核心概念词频率")
    lines.append("")
    lines.append("| 排名 | 概念 | 次数 |")
    lines.append("|------|------|------|")
    for i, (word, count) in enumerate(cf, 1):
        lines.append(f"| {i} | {word} | {count:,} |")

    # 3. 个股 & 资产提及
    lines.append("\n## 个股 & 资产提及频率")
    lines.append("")
    lines.append("| 排名 | 标的 | 次数 |")
    lines.append("|------|------|------|")
    for i, (stock, count) in enumerate(sf, 1):
        lines.append(f"| {i} | {stock} | {count:,} |")

    # 4. TF-IDF
    lines.append("\n## TF-IDF 关键词")
    lines.append("")
    lines.append("| 排名 | 词汇 | 权重 |")
    lines.append("|------|------|------|")
    for i, (word, weight) in enumerate(tfidf, 1):
        lines.append(f"| {i} | {word} | {weight:.4f} |")

    # 5. 二元词组
    lines.append("\n## 高频二元词组")
    lines.append("")
    lines.append("| 排名 | 词组 | 次数 |")
    lines.append("|------|------|------|")
    for i, (bigram, count) in enumerate(bi, 1):
        lines.append(f"| {i} | {bigram} | {count:,} |")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"\n结果已保存到: {filepath}")
    return filepath


if __name__ == '__main__':
    print("=" * 60)
    print("冰冰小美帖子词频分析")
    print("=" * 60)

    print("\n加载帖子...")
    texts, filenames = load_all_posts()
    print(f"共加载 {len(texts)} 篇内容")
    print(f"  贴子: {sum(1 for f in filenames if f.startswith('贴子/'))}")
    print(f"  专栏: {sum(1 for f in filenames if f.startswith('专栏/'))}")
    print(f"  体系: {sum(1 for f in filenames if f.startswith('体系/'))}")
    print(f"  案例: {sum(1 for f in filenames if f.startswith('案例/'))}")

    total_chars = sum(len(t) for t in texts)
    print(f"  总字数: {total_chars:,}")

    # 1. 总词频 - 完整排名
    print("\n生成全量词频排名...")
    freq = word_frequency(texts, 500)

    # 2. 概念词频率
    print("生成概念词频率...")
    cf = concept_frequency(texts, 60)

    # 3. 个股 & 资产提及频率
    print("生成个股&资产提及频率...")
    sf = entity_frequency(texts, 100)

    # 4. TF-IDF 关键词
    print("生成TF-IDF关键词...")
    tfidf = extract_keywords_tfidf(texts, 100)

    # 5. 二元词组
    print("生成高频二元词组...")
    bi = bigram_frequency(texts, 100)

    # 保存结果
    save_results(freq, cf, sf, tfidf, bi, texts)

    # 终端展示前100
    print("\n" + "=" * 60)
    print("全量词频 TOP 50（完整列表见输出文件）")
    print("=" * 60)
    for i, (word, count) in enumerate(freq[:50], 1):
        print(f"{i:3d}. {word:<12s} {count:>6d}")

    print("\n分析完成！")
