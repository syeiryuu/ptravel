-- 「下一站扭蛋」数据库 schema (SQLite)
--
-- Design goals
-- ------------
-- 1. POI 库持久化：每次高德抓到的 POI 都 upsert 入库累积，构建自己的 POI 库，
--    以 amap_id 去重，避免重复消耗高德配额。原始事实(poi)与派生文案(poi_copy)
--    分离，重跑文案/prompt 迭代不影响原始抓取库。
-- 2. 离线 T+1 预生成：recommend_pool 存放定时任务算好的、按用户画像分桶的推荐池，
--    前端只取结果。
-- 3. 基于现有高德标签的权重关联：不额外打标签，直接用 poi 里已有的
--    category / typecode / rating / cost 等字段做加权（策略在 recommend.py）。
--
-- 所有时间统一存 ISO8601 文本 (UTC)。JSON 字段以 TEXT 存 JSON 字符串。

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 1. poi —— 自建 POI 库（核心，持久累积；字段对齐 collect._normalise 输出）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS poi (
    amap_id        TEXT PRIMARY KEY,          -- 高德 POI id，去重键
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,             -- 产品品类: cafe/food/park/culture/shop/night/weird
    amap_type      TEXT,                      -- 高德类型中文
    typecode       TEXT,                      -- 高德类型编码, 如 050100
    address        TEXT,
    adname         TEXT,                      -- 行政区
    business_area  TEXT,                      -- 商圈, 如 三里屯
    lng            REAL,
    lat            REAL,
    tel            TEXT,
    opentime       TEXT,                      -- 原始营业时间文本
    rating         TEXT,                      -- 高德评分（内部用于加权，不展示）
    cost           TEXT,                      -- 高德人均（内部用于加权，不展示）
    tag            TEXT,                      -- 高德标签: 菜品/特色关键词, 逗号分隔
    alias          TEXT,
    photo_titles   TEXT,                      -- JSON 数组字符串
    photo_count    INTEGER DEFAULT 0,
    -- 抓取管理字段
    first_seen_at  TEXT NOT NULL,             -- 首次抓到时间
    last_seen_at   TEXT NOT NULL,             -- 最近一次抓到时间（判断是否需重抓）
    source         TEXT DEFAULT 'amap'        -- amap / mock / ...
);
CREATE INDEX IF NOT EXISTS idx_poi_category ON poi(category);
CREATE INDEX IF NOT EXISTS idx_poi_geo ON poi(lng, lat);
CREATE INDEX IF NOT EXISTS idx_poi_seen ON poi(last_seen_at);

-- ---------------------------------------------------------------------------
-- 2. poi_copy —— 清洗产物 + LLM 文案（可 T+1 重跑，与原始 poi 解耦）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS poi_copy (
    amap_id          TEXT PRIMARY KEY,
    open_hour        INTEGER,
    close_hour       INTEGER,
    duration_minutes INTEGER,
    direction        TEXT,
    rarity           TEXT,                    -- common / uncommon / rare
    lucky            INTEGER,
    hook             TEXT,
    reason           TEXT,
    oracle           TEXT,
    action           TEXT,
    sources          TEXT,                    -- JSON 数组: 文案依据的字段
    copy_version     INTEGER DEFAULT 1,       -- prompt 迭代版本
    generated_at     TEXT,
    FOREIGN KEY (amap_id) REFERENCES poi(amap_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_copy_rarity ON poi_copy(rarity);

-- ---------------------------------------------------------------------------
-- 3. user_profile —— 用户个人数据（匿名 uuid 标识）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_profile (
    user_id      TEXT PRIMARY KEY,            -- 前端生成的匿名 uuid，或登录 id
    mbti         TEXT DEFAULT '',
    zodiac       TEXT DEFAULT '',
    preferences  TEXT DEFAULT '[]',           -- JSON 数组: ["forage","idle"]
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 4. recommend_pool —— T+1 离线预生成的推荐池（按用户画像分桶）
--    profile_key 是用户画像的归一化指纹（如 "mbti=I;pref=forage,idle"），
--    同一画像的用户共享一份预生成的加权候选池。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recommend_pool (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key  TEXT NOT NULL,               -- 用户画像指纹
    amap_id      TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0,   -- 该 POI 在此画像下的抽取权重
    rank         INTEGER,                     -- 预排序名次（可选）
    built_at     TEXT NOT NULL,               -- 生成批次时间（T+1 判断）
    FOREIGN KEY (amap_id) REFERENCES poi(amap_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pool_key ON recommend_pool(profile_key, built_at);

-- ---------------------------------------------------------------------------
-- 5. draw_history —— 抽卡历史（会话内去重 + T+1 分析）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS draw_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT,
    amap_id    TEXT,
    luck       INTEGER,
    is_super   INTEGER DEFAULT 0,             -- 0/1
    drawn_at   TEXT NOT NULL,
    FOREIGN KEY (amap_id) REFERENCES poi(amap_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_draw_user ON draw_history(user_id, drawn_at);

-- ---------------------------------------------------------------------------
-- 6. collect_log —— 抓取日志（管理高德配额，判断哪些网格/品类该重抓）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collect_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category     TEXT,
    grid_cell    TEXT,                        -- 抓取的网格 polygon
    found_count  INTEGER DEFAULT 0,           -- 本次返回条数
    new_count    INTEGER DEFAULT 0,           -- 本次新增（去重后）
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collectlog_time ON collect_log(collected_at);
