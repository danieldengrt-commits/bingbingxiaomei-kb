# 冰美知识库 Skills

> 所有冰美相关的 Claude Code Skills 统一管理

## 目录结构

```
skills/
├── README.md                          ← 本文件
├── bingbingxiaomei-perspective/       ← 冰美视角分析
│   └── SKILL.md
├── bingbingxiaomei-reader/            ← 阅读工作流
│   ├── SKILL.md
│   ├── analyze_post.py
│   └── progress.json
└── bingbingxiaomei-workflow/          ← 工作流搭建
    └── SKILL.md
```

## Skills 说明

### 1. bingbingxiaomei-perspective

**功能**：用冰冰小美的视角分析市场情绪、判断流动性走向、审视交易决策

**触发词**：
- 「用冰美的视角」
- 「冰美会怎么看」
- 「冰美模式」
- 「冰冰小美 perspective」

**核心内容**：
- 6个核心心智模型
- 8条决策启发式
- 完整的表达DNA

### 2. bingbingxiaomei-reader

**功能**：按时间顺序逐条展示帖子，讨论后整理成笔记，自动记录阅读进度

**触发词**：
- 「阅读冰美帖子」
- 「开始阅读」
- 「下一条」

**核心内容**：
- 阅读流程管理
- 笔记自动整理
- 进度跟踪

### 3. bingbingxiaomei-workflow

**功能**：分析冰冰小美的日常工作流，搭建冰美的信息处理系统

**触发词**：
- 「冰美工作流」
- 「冰美看什么」
- 「冰美信息源」
- 「冰美怎么分析」

**核心内容**：
- 信息源清单
- 分析框架
- 工作习惯

---

## 安装到 Claude Code

将 skills 目录软链接到 `~/.claude/skills/`：

```bash
ln -s $(pwd)/skills/bingbingxiaomei-perspective ~/.claude/skills/
ln -s $(pwd)/skills/bingbingxiaomei-reader ~/.claude/skills/
ln -s $(pwd)/skills/bingbingxiaomei-workflow ~/.claude/skills/
```

---

## 更新日志

- **2026-05-16**：初始化 skills 目录，整理现有 skill
