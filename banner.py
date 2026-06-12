"""
卡池数据模型 + gacha_table 解析
"""
import json
import aiosqlite
import aiohttp
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# GitHub raw URL base
GITHUB_BASE = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/"
GITEE_BASE = "https://gitee.com/Kengxxiao/ArknightsGameData/raw/master/zh_CN/gamedata/excel/"

# 卡池类型映射
POOL_TYPE_MAP = {
    "NORMAL": 0,      # 标准寻访
    "LIMITED": 1,     # 限定寻访
    "LINKAGE": 2,     # 联合寻访
    "CLASSIC": 3,     # 前路回响
    "MIDDLE": 4,      # 中坚寻访
    "RUSH": 5,        # 奔涌寻访
}


class BannerManager:
    """卡池管理器"""

    def __init__(self, db_path: str, source: str = "github"):
        self.db_path = db_path
        self.source = source
        self.base_url = GITEE_BASE if source == "gitee" else GITHUB_BASE

    async def initialize_db(self):
        """初始化数据库表"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS banners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_name TEXT,
                    pool_type INTEGER,
                    pickup_6 TEXT,
                    pickup_5 TEXT,
                    pickup_4 TEXT,
                    is_active INTEGER DEFAULT 1,
                    version TEXT,
                    data_json TEXT,
                    created_at TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS operator_pool (
                    char_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    rarity INTEGER,
                    is_limited INTEGER DEFAULT 0,
                    is_classic INTEGER DEFAULT 0
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_gacha (
                    user_id TEXT PRIMARY KEY,
                    orundum INTEGER DEFAULT 0,
                    permits INTEGER DEFAULT 0,
                    ten_permits INTEGER DEFAULT 0,
                    yellow_tickets INTEGER DEFAULT 0,
                    green_tickets INTEGER DEFAULT 0,
                    pity_6 INTEGER DEFAULT 0,
                    pity_5 INTEGER DEFAULT 0,
                    total_pulls INTEGER DEFAULT 0,
                    total_6stars INTEGER DEFAULT 0,
                    current_banner_id INTEGER,
                    sign_date TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_operators (
                    user_id TEXT NOT NULL,
                    char_id TEXT NOT NULL,
                    potential INTEGER DEFAULT 1,
                    obtained_at TEXT,
                    PRIMARY KEY (user_id, char_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS gacha_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    banner_id INTEGER,
                    char_id TEXT,
                    rarity INTEGER,
                    is_new INTEGER DEFAULT 1,
                    pulled_at TEXT
                )
            """)

            await db.commit()

    async def download_gacha_table(self) -> Optional[dict]:
        """下载 gacha_table.json"""
        url = self.base_url + "gacha_table.json"
        logger.info(f"Downloading {url}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        text = await resp.text(encoding='utf-8')
                        return json.loads(text)
                    else:
                        logger.error(f"Failed to download gacha_table: HTTP {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Failed to download gacha_table: {e}")
            return None

    async def sync_banners(self, force: bool = False) -> tuple[bool, str]:
        """
        同步卡池数据

        Args:
            force: 是否强制更新

        Returns:
            (success, message)
        """
        data = await self.download_gacha_table()
        if not data:
            return False, "下载失败"

        try:
            # 解析卡池列表
            pools = data.get("gachaPoolClient", [])
            if not pools:
                return False, "未找到卡池数据"

            async with aiosqlite.connect(self.db_path) as db:
                # 获取当前时间戳
                now = int(datetime.now().timestamp())

                # 停用所有旧卡池
                if force:
                    await db.execute("UPDATE banners SET is_active = 0")

                # 添加新卡池
                added_count = 0
                for pool in pools:
                    pool_id = pool.get("gachaPoolId", "")
                    pool_name = pool.get("gachaPoolName", "")
                    open_time = pool.get("openTime", 0)
                    end_time = pool.get("endTime", 0)
                    gacha_rule_type = pool.get("gachaRuleType", "NORMAL")

                    # 检查是否已存在
                    async with db.execute(
                        "SELECT id FROM banners WHERE pool_name = ?", (pool_name,)
                    ) as cursor:
                        existing = await cursor.fetchone()
                        if existing:
                            continue

                    # 判断是否当前活跃
                    is_active = 1 if open_time <= now <= end_time else 0

                    # 解析卡池类型
                    pool_type = POOL_TYPE_MAP.get(gacha_rule_type, 0)

                    # 解析 UP 干员 (从 gachaPoolDetail 中提取)
                    detail = pool.get("gachaPoolDetail", "")
                    pickup_6 = self._extract_pickup(detail, 6)
                    pickup_5 = self._extract_pickup(detail, 5)
                    pickup_4 = self._extract_pickup(detail, 4)

                    # 存储卡池
                    data_json = json.dumps(pool, ensure_ascii=False)
                    await db.execute("""
                        INSERT INTO banners (pool_name, pool_type, pickup_6, pickup_5, pickup_4,
                                           is_active, version, data_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pool_name, pool_type, pickup_6, pickup_5, pickup_4,
                          is_active, "v1", data_json, datetime.now().isoformat()))

                    added_count += 1

                await db.commit()

            return True, f"同步成功，新增 {added_count} 个卡池"

        except Exception as e:
            logger.error(f"Failed to sync banners: {e}")
            return False, f"同步失败: {e}"

    def _extract_pickup(self, detail: str, rarity: int) -> str:
        """
        从卡池详情中提取 UP 干员

        Args:
            detail: 卡池详情文本
            rarity: 稀有度 (4/5/6)

        Returns:
            JSON array of char_ids
        """
        # 简化处理: 从详情文本中提取干员名称
        # 实际应该从 gacha_table 的结构化数据中提取
        # 这里返回空数组，后续需要完善
        return "[]"

    async def get_active_banners(self) -> list[dict]:
        """获取当前活跃卡池"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM banners WHERE is_active = 1 ORDER BY id DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]

    async def get_all_banners(self, limit: int = 20) -> list[dict]:
        """获取所有卡池"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM banners ORDER BY id DESC LIMIT ?", (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]

    async def switch_banner(self, user_id: str, banner_id: int) -> bool:
        """切换用户当前卡池"""
        async with aiosqlite.connect(self.db_path) as db:
            # 检查卡池是否存在
            async with db.execute(
                "SELECT id FROM banners WHERE id = ?", (banner_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False

            # 更新用户当前卡池
            await db.execute(
                "UPDATE user_gacha SET current_banner_id = ? WHERE user_id = ?",
                (banner_id, user_id)
            )
            await db.commit()
            return True

    async def load_operator_pool(self, character_data: dict):
        """
        从 character_table.json 加载干员池

        Args:
            character_data: character_table.json 的完整数据
        """
        async with aiosqlite.connect(self.db_path) as db:
            count = 0
            for char_id, char in character_data.items():
                if not char_id.startswith("char_"):
                    continue

                name = char.get("name", "")
                rarity_str = char.get("rarity", "TIER_1")
                rarity_map = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3,
                             "TIER_4": 4, "TIER_5": 5, "TIER_6": 6}
                rarity = rarity_map.get(rarity_str, 1)

                # 判断是否限定
                is_limited = 0
                is_classic = 0

                await db.execute("""
                    INSERT OR REPLACE INTO operator_pool
                    (char_id, name, rarity, is_limited, is_classic)
                    VALUES (?, ?, ?, ?, ?)
                """, (char_id, name, rarity, is_limited, is_classic))
                count += 1

            await db.commit()
            logger.info(f"Loaded {count} operators to pool")
