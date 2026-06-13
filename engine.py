"""
抽卡引擎 - 核心 RNG + 保底逻辑
"""
import random
import json
import aiosqlite
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class GachaResult:
    """单次抽卡结果"""

    def __init__(self, char_id: str, name: str, rarity: int, is_new: bool = False):
        self.char_id = char_id
        self.name = name
        self.rarity = rarity
        self.is_new = is_new

    def to_dict(self) -> dict:
        return {
            "char_id": self.char_id,
            "name": self.name,
            "rarity": self.rarity,
            "is_new": self.is_new,
        }


class GachaEngine:
    """抽卡引擎"""

    # BUG-3: SQL 列名白名单，防止动态 SQL 注入
    ALLOWED_USER_COLUMNS = frozenset({
        "orundum", "permits", "ten_permits", "yellow_tickets",
        "green_tickets", "pity_6", "pity_5", "total_pulls",
        "total_6stars", "current_banner_id", "sign_date",
    })

    def __init__(self, db_path: str, config: dict):
        self.db_path = db_path
        self.config = config

        # 概率配置
        self.base_rate_6star = config.get("base_rate_6star", 2) / 100
        self.base_rate_5star = config.get("base_rate_5star", 8) / 100
        self.soft_pity_start = config.get("soft_pity_start", 40)
        self.hard_pity = config.get("hard_pity", 50)

    def _calculate_pull(self, operators: list, banner: dict,
                        pity_6: int, pity_5: int) -> tuple:
        """
        纯 RNG 计算，无 DB 操作。

        Returns:
            (char_id, rarity, new_pity_6, new_pity_5)
        """
        # 6星概率 (含保底)
        rate_6star = self.base_rate_6star
        if pity_6 >= self.soft_pity_start:
            # 软保底: 每抽+2%
            rate_6star += (pity_6 - self.soft_pity_start + 1) * 0.02
        if pity_6 >= self.hard_pity - 1:
            # 硬保底: 必出6星
            rate_6star = 1.0

        # 5星概率 (含保底)
        rate_5star = self.base_rate_5star
        if pity_5 >= 9:
            # 5星也有保底 (10抽)
            rate_5star = 1.0 - rate_6star

        # 随机抽取
        roll = random.random()

        if roll < rate_6star:
            rarity = 6
            char_id = self._pick_operator_sync(operators, 6, banner)
            pity_6 = 0
            pity_5 += 1
        elif roll < rate_6star + rate_5star:
            rarity = 5
            char_id = self._pick_operator_sync(operators, 5, banner)
            pity_6 += 1
            pity_5 = 0
        else:
            rarity = 4
            char_id = self._pick_operator_sync(operators, 4, banner)
            pity_6 += 1
            pity_5 += 1

        return char_id, rarity, pity_6, pity_5

    async def pull_single(self, user_id: str, banner_id: int, cost: int = 0) -> GachaResult:
        """
        单抽

        Args:
            user_id: 用户ID
            banner_id: 卡池ID
            cost: 本次抽卡费用 (0 表示不扣费)

        Returns:
            GachaResult
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 获取用户状态
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                await self._create_user_gacha(db, user_id)
                user_data = await self._get_user_gacha(db, user_id)

            # BUG-2: 原子扣费 - 在同一事务中检查余额
            if cost > 0 and user_data["orundum"] < cost:
                raise ValueError(f"合成玉不足！需要 {cost}，当前 {user_data['orundum']}")

            # 获取卡池信息
            banner = await self._get_banner(db, banner_id)
            if not banner:
                raise ValueError(f"卡池不存在: {banner_id}")

            # 获取干员池
            operators = await self._get_operator_pool(db, banner["pool_type"])

            # BUG-1: 使用纯计算方法，在同一事务中完成所有操作
            pity_6 = user_data["pity_6"]
            pity_5 = user_data["pity_5"]
            char_id, rarity, pity_6, pity_5 = self._calculate_pull(
                operators, banner, pity_6, pity_5
            )

            # 检查是否新干员
            is_new = await self._check_new_operator(db, user_id, char_id)

            # 更新用户状态 (含扣费)
            updates = {
                "pity_6": pity_6,
                "pity_5": pity_5,
                "total_pulls": user_data["total_pulls"] + 1,
                "total_6stars": user_data["total_6stars"] + (1 if rarity == 6 else 0),
            }
            if cost > 0:
                updates["orundum"] = user_data["orundum"] - cost
            await self._update_user_gacha(db, user_id, updates)

            # 添加到背包
            await self._add_to_inventory(db, user_id, char_id)

            # 记录抽卡历史
            await self._record_gacha_history(db, user_id, banner_id, char_id, rarity, is_new)

            await db.commit()

            # 获取干员名称
            operator = await self._get_operator_by_id(db, char_id)
            name = operator["name"] if operator else char_id

            return GachaResult(char_id, name, rarity, is_new)

    async def pull_ten(self, user_id: str, banner_id: int, cost: int = 0) -> list:
        """
        十连抽 - 单事务实现

        Args:
            user_id: 用户ID
            banner_id: 卡池ID
            cost: 本次抽卡费用 (0 表示不扣费)

        Returns:
            list[GachaResult]
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 获取用户状态
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                await self._create_user_gacha(db, user_id)
                user_data = await self._get_user_gacha(db, user_id)

            # BUG-2: 原子扣费 - 在同一事务中检查余额
            if cost > 0 and user_data["orundum"] < cost:
                raise ValueError(f"合成玉不足！需要 {cost}，当前 {user_data['orundum']}")

            # 获取卡池信息
            banner = await self._get_banner(db, banner_id)
            if not banner:
                raise ValueError(f"卡池不存在: {banner_id}")

            # 获取干员池
            operators = await self._get_operator_pool(db, banner["pool_type"])

            # 获取已拥有干员 ID 集合 (用于判断 is_new)
            owned_chars = await self._get_owned_char_ids(db, user_id)

            # BUG-1 + BUG-10: 在内存中计算所有 10 次结果，单事务提交
            results = []
            pity_6 = user_data["pity_6"]
            pity_5 = user_data["pity_5"]
            total_6stars = 0

            for i in range(10):
                char_id, rarity, pity_6, pity_5 = self._calculate_pull(
                    operators, banner, pity_6, pity_5
                )
                is_new = char_id not in owned_chars
                owned_chars.add(char_id)
                results.append((char_id, rarity, is_new))
                if rarity == 6:
                    total_6stars += 1

            # 十连保底: 至少1个5星及以上
            has_high_rarity = any(r >= 5 for _, r, _ in results)
            if not has_high_rarity:
                # 在持久化前替换，不会产生多余记录
                new_char_id = self._pick_operator_sync(operators, 5, banner)
                is_new = new_char_id not in owned_chars
                results[9] = (new_char_id, 5, is_new)

            # 批量持久化所有结果
            for char_id, rarity, is_new in results:
                await self._add_to_inventory(db, user_id, char_id)
                await self._record_gacha_history(db, user_id, banner_id, char_id, rarity, is_new)

            # 单次更新用户状态 (含扣费)
            updates = {
                "pity_6": pity_6,
                "pity_5": pity_5,
                "total_pulls": user_data["total_pulls"] + 10,
                "total_6stars": user_data["total_6stars"] + total_6stars,
            }
            if cost > 0:
                updates["orundum"] = user_data["orundum"] - cost
            await self._update_user_gacha(db, user_id, updates)

            await db.commit()

            # 构建 GachaResult 对象
            gacha_results = []
            for char_id, rarity, is_new in results:
                operator = await self._get_operator_by_id(db, char_id)
                name = operator["name"] if operator else char_id
                gacha_results.append(GachaResult(char_id, name, rarity, is_new))

            return gacha_results

    async def _get_owned_char_ids(self, db, user_id: str) -> set:
        """获取用户已拥有的干员 ID 集合"""
        async with db.execute(
            "SELECT char_id FROM user_operators WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return {row[0] for row in rows}

    async def _get_user_gacha(self, db, user_id: str) -> Optional[dict]:
        """获取用户抽卡状态"""
        async with db.execute(
            "SELECT * FROM user_gacha WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))

    async def _create_user_gacha(self, db, user_id: str):
        """创建新用户"""
        await db.execute("""
            INSERT INTO user_gacha (user_id, orundum, permits, ten_permits,
                                   yellow_tickets, green_tickets, pity_6, pity_5,
                                   total_pulls, total_6stars, current_banner_id)
            VALUES (?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
        """, (user_id,))

    async def _update_user_gacha(self, db, user_id: str, updates: dict):
        """更新用户状态"""
        # BUG-3: 验证列名白名单
        for key in updates.keys():
            if key not in self.ALLOWED_USER_COLUMNS:
                raise ValueError(f"不允许更新列: {key}")

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [user_id]

        await db.execute(
            f"UPDATE user_gacha SET {set_clause} WHERE user_id = ?",
            values
        )

    async def _get_banner(self, db, banner_id: int) -> Optional[dict]:
        """获取卡池信息"""
        async with db.execute(
            "SELECT * FROM banners WHERE id = ? AND is_active = 1", (banner_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))

    async def _get_operator_pool(self, db, pool_type: int) -> list:
        """获取干员池"""
        async with db.execute(
            "SELECT * FROM operator_pool WHERE is_limited = 0 OR ? IN (1, 2)", (pool_type,)
        ) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    def _pick_operator_sync(self, operators: list, rarity: int, banner: dict) -> str:
        """
        从卡池中抽取指定稀有度的干员 (同步版本，无 DB 操作)

        Args:
            operators: 干员池
            rarity: 目标稀有度
            banner: 卡池信息

        Returns:
            char_id
        """
        # 筛选指定稀有度
        candidates = [op for op in operators if op["rarity"] == rarity]

        if not candidates:
            # BUG-6: 修复降级回退范围 (3 → 2 以包含 3 星)
            for r in range(rarity - 1, 2, -1):
                candidates = [op for op in operators if op["rarity"] == r]
                if candidates:
                    break

        if not candidates:
            # 最终回退
            return "char_001_amiya"

        # UP逻辑
        pickup_key = f"pickup_{rarity}"
        pickup_ids = banner.get(pickup_key, "")
        if pickup_ids:
            try:
                pickup_list = json.loads(pickup_ids)
                pickup_candidates = [op for op in candidates if op["char_id"] in pickup_list]

                if pickup_candidates and random.random() < 0.5:
                    # 50%概率出UP
                    return random.choice(pickup_candidates)["char_id"]
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                # BUG-14: 修复 bare except，记录日志
                logger.debug(f"解析 pickup 数据失败: {e}")

        # 随机抽取
        return random.choice(candidates)["char_id"]

    async def _check_new_operator(self, db, user_id: str, char_id: str) -> bool:
        """检查是否为新干员"""
        async with db.execute(
            "SELECT 1 FROM user_operators WHERE user_id = ? AND char_id = ?",
            (user_id, char_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row is None

    async def _add_to_inventory(self, db, user_id: str, char_id: str):
        """添加到背包"""
        async with db.execute(
            "SELECT potential FROM user_operators WHERE user_id = ? AND char_id = ?",
            (user_id, char_id)
        ) as cursor:
            row = await cursor.fetchone()

            if row:
                # 已有，增加潜能
                potential = row[0] + 1
                await db.execute(
                    "UPDATE user_operators SET potential = ? WHERE user_id = ? AND char_id = ?",
                    (potential, user_id, char_id)
                )
            else:
                # 新干员
                await db.execute("""
                    INSERT INTO user_operators (user_id, char_id, potential, obtained_at)
                    VALUES (?, ?, 1, ?)
                """, (user_id, char_id, datetime.now().isoformat()))

    async def _record_gacha_history(self, db, user_id: str, banner_id: int,
                                   char_id: str, rarity: int, is_new: bool):
        """记录抽卡历史"""
        await db.execute("""
            INSERT INTO gacha_history (user_id, banner_id, char_id, rarity, is_new, pulled_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, banner_id, char_id, rarity, 1 if is_new else 0,
              datetime.now().isoformat()))

    async def _get_operator_by_id(self, db, char_id: str) -> Optional[dict]:
        """根据ID获取干员"""
        async with db.execute(
            "SELECT * FROM operator_pool WHERE char_id = ?", (char_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
