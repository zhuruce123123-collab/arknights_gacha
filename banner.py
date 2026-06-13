"""
卡池数据模型 + gacha_table 解析
"""
import json
import os
import aiosqlite
import aiohttp
import logging
from datetime import datetime
from typing import Optional

from .constants import GITHUB_BASE, GITEE_BASE, GITHUB_PROXIES, RARITY_MAP

logger = logging.getLogger(__name__)

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

                # BUG-4: 获取已知干员列表，用于 UP 干员名称匹配
                known_operators = await self._get_known_operators(db)

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

                    # BUG-4: 解析 UP 干员 (从 gachaPoolDetail 中名称匹配)
                    detail = pool.get("gachaPoolDetail", "")
                    pickup_6 = self._extract_pickup(detail, 6, known_operators)
                    pickup_5 = self._extract_pickup(detail, 5, known_operators)
                    pickup_4 = self._extract_pickup(detail, 4, known_operators)

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

    async def _get_known_operators(self, db) -> list:
        """获取已知干员列表 (用于 UP 匹配)"""
        async with db.execute(
            "SELECT char_id, name, rarity FROM operator_pool"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"char_id": r[0], "name": r[1], "rarity": r[2]} for r in rows]

    def _extract_pickup(self, detail: str, rarity: int, known_operators: list = None) -> str:
        """
        从卡池详情中提取 UP 干员

        BUG-4: 实现名称匹配逻辑，从 gachaPoolDetail 文本中查找干员名

        Args:
            detail: 卡池详情文本
            rarity: 稀有度 (4/5/6)
            known_operators: 已知干员列表

        Returns:
            JSON array of char_ids
        """
        if not detail or not known_operators:
            return "[]"

        # 筛选指定稀有度的干员
        candidates = [op for op in known_operators if op["rarity"] == rarity]

        # 在详情文本中查找干员名称
        pickup_ids = []
        for op in candidates:
            name = op.get("name", "")
            if name and name in detail:
                pickup_ids.append(op["char_id"])

        return json.dumps(pickup_ids, ensure_ascii=False)

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
                # BUG-16: 与 load_operators_from_local 保持一致的过滤逻辑
                if "#" in char_id:
                    continue

                name = char.get("name", "")
                rarity_str = char.get("rarity", "TIER_1")
                rarity = RARITY_MAP.get(rarity_str, 1)

                # BUG-16: 只加载 3 星及以上干员
                if rarity < 3:
                    continue

                # BUG-5: 从 itemObtainApproach 判断是否限定
                obtain_approach = char.get("itemObtainApproach", "")
                is_not_obtainable = char.get("isNotObtainable", False)
                is_limited = 1 if (obtain_approach != "招募寻访" or is_not_obtainable) else 0
                is_classic = 0

                await db.execute("""
                    INSERT OR REPLACE INTO operator_pool
                    (char_id, name, rarity, is_limited, is_classic)
                    VALUES (?, ?, ?, ?, ?)
                """, (char_id, name, rarity, is_limited, is_classic))
                count += 1

            await db.commit()
            logger.info(f"Loaded {count} operators to pool")

    async def load_operators_from_local(self, resource_dir: str) -> bool:
        """
        从本地 operators.json 或 character_table.json 加载干员池

        Args:
            resource_dir: resource 目录路径

        Returns:
            是否成功加载
        """
        # 优先尝试 operators.json（精简版）
        operators_file = os.path.join(resource_dir, "operators.json")
        character_table_file = os.path.join(resource_dir, "character_table.json")

        data = None
        source_name = ""

        if os.path.exists(operators_file):
            try:
                with open(operators_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                source_name = "operators.json"
            except Exception as e:
                logger.warning(f"Failed to load operators.json: {e}")

        if not data and os.path.exists(character_table_file):
            try:
                with open(character_table_file, "r", encoding="utf-8") as f:
                    full_data = json.load(f)
                # 提取干员数据
                data = {}
                for char_id, char in full_data.items():
                    if char_id.startswith("char_") and "#" not in char_id:
                        name = char.get("name", "")
                        rarity_str = char.get("rarity", "TIER_1")
                        rarity = RARITY_MAP.get(rarity_str, 1)
                        if rarity >= 3:
                            # BUG-5: 从 itemObtainApproach 判断是否限定
                            obtain_approach = char.get("itemObtainApproach", "")
                            is_not_obtainable = char.get("isNotObtainable", False)
                            is_limited = 1 if (obtain_approach != "招募寻访" or is_not_obtainable) else 0
                            data[char_id] = {"name": name, "rarity": rarity, "is_limited": is_limited}
                source_name = "character_table.json"
            except Exception as e:
                logger.warning(f"Failed to load character_table.json: {e}")

        if not data:
            return False

        try:
            async with aiosqlite.connect(self.db_path) as db:
                count = 0
                for char_id, char_data in data.items():
                    if isinstance(char_data, dict):
                        name = char_data.get("name", "")
                        rarity = char_data.get("rarity", 1)
                        is_limited = char_data.get("is_limited", 0)
                        is_classic = char_data.get("is_classic", 0)
                    else:
                        continue

                    await db.execute("""
                        INSERT OR REPLACE INTO operator_pool
                        (char_id, name, rarity, is_limited, is_classic)
                        VALUES (?, ?, ?, ?, ?)
                    """, (char_id, name, rarity, is_limited, is_classic))
                    count += 1

                await db.commit()
                logger.info(f"Loaded {count} operators from {source_name}")
                return count > 0
        except Exception as e:
            logger.error(f"Failed to load operators from local file: {e}")
            return False

    async def load_operators_from_api(self) -> bool:
        """从 GitHub API 下载 character_table.json 并加载干员池"""
        raw_url = self.base_url + "character_table.json"

        # 构建下载源列表：原始 URL + 镜像代理（仅对 GitHub 源）
        urls = [raw_url]
        if self.source != "gitee":
            for proxy in GITHUB_PROXIES:
                urls.append(proxy + raw_url)

        data = None
        for url in urls:
            try:
                logger.info(f"Downloading character_table from {url}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                        if resp.status == 200:
                            text = await resp.text(encoding='utf-8')
                            data = json.loads(text)
                            break
                        else:
                            logger.warning(f"HTTP {resp.status} from {url}")
            except Exception as e:
                logger.warning(f"Failed to download from {url}: {e}")
                continue

        if not data:
            logger.error("All character_table download sources failed")
            return False

        await self.load_operator_pool(data)

        # 验证加载结果
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM operator_pool") as cursor:
                row = await cursor.fetchone()
                count = row[0] if row else 0
                logger.info(f"Operator pool now has {count} entries")
                return count > 0

    async def download_font(self, font_dir: str) -> Optional[str]:
        """
        下载中文字体到指定目录

        Returns:
            字体文件路径，失败返回 None
        """
        os.makedirs(font_dir, exist_ok=True)
        font_path = os.path.join(font_dir, "NotoSansSC-Regular.ttf")

        if os.path.exists(font_path) and os.path.getsize(font_path) > 100000:
            logger.info(f"Font already exists: {font_path}")
            return font_path

        # 原始 URL
        raw_url = "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansSC-Regular.otf"

        # 构建下载源列表：原始 URL + 镜像代理
        urls = [raw_url]
        for proxy in GITHUB_PROXIES:
            urls.append(proxy + raw_url)
        # jsdelivr CDN 作为备用
        urls.append("https://cdn.jsdelivr.net/gh/googlefonts/noto-cjk@main/Sans/OTF/SimplifiedChinese/NotoSansSC-Regular.otf")

        for url in urls:
            try:
                logger.info(f"Trying to download font from {url}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if len(data) > 100000:
                                with open(font_path, "wb") as f:
                                    f.write(data)
                                logger.info(f"Font downloaded: {font_path} ({len(data)} bytes)")
                                return font_path
                            else:
                                logger.warning(f"Downloaded file too small ({len(data)} bytes), trying next source")
            except Exception as e:
                logger.warning(f"Font download failed from {url}: {e}")
                continue

        logger.error("All font download sources failed")
        return None
