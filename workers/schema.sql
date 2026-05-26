-- 冰冰小美知识库 D1 数据库表结构

-- 访问计数器（单行记录，自增更新）
CREATE TABLE IF NOT EXISTS counters (
  id INTEGER PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0
);

-- 初始化计数器（仅当不存在时插入）
INSERT OR IGNORE INTO counters (id, count) VALUES (1, 0);

-- 留言板
CREATE TABLE IF NOT EXISTS guestbook (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  message TEXT NOT NULL,
  ip TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
