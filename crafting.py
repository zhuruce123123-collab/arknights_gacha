"""
素材合成树遍历算法
"""
import os
import json
import time
import aiosqlite
import aiohttp
import logging

logger = logging.getLogger(__name__)

# 素材稀有度颜色
RARITY_COLORS = {
    1: "#808080",
    2: "#808080",
    3: "#4E7EBD",
    4: "#8D58A8",
    5: "#E9B23C",
    6: "#EF4F43",
}

# 素材分类中文名
CATEGORY_MAP = {
    "MATERIAL": "材料",
    "NORMAL": "普通",
    "NONE": "无",
}

# GitHub raw URL base
GITHUB_BASE = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/"
GITEE_BASE = "https://gitee.com/Kengxxiao/ArknightsGameData/raw/master/zh_CN/gamedata/excel/"

# 需要下载的文件
FILES = {
    "item_table.json": "物品数据",
    "building_data.json": "合成配方",
    "stage_table.json": "关卡掉落",
}


class CraftingTree:
    """合成树数据结构"""

    def __init__(self, item_id: str, name: str, rarity: int):
        self.item_id = item_id
        self.name = name
        self.rarity = rarity
        self.children = []  # List[CraftingTree]
        self.quantity = 1
        self.craft_type = None  # "WORKBENCH" / "MANUFACTURE"
        self.stage_drops = []  # 掉落关卡列表

    def add_child(self, child: 'CraftingTree', quantity: int = 1):
        self.children.append((child, quantity))

    def add_stage_drop(self, stage_name: str, drop_type: str, drop_rate: float, sp_cost: int):
        self.stage_drops.append({
            "stage_name": stage_name,
            "drop_type": drop_type,
            "drop_rate": drop_rate,
            "sp_cost": sp_cost,
        })

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "rarity": self.rarity,
            "quantity": self.quantity,
            "craft_type": self.craft_type,
            "children": [
                {"tree": child.to_dict(), "quantity": qty}
                for child, qty in self.children
            ],
            "stage_drops": self.stage_drops,
        }


class MaterialDataLoader:
    """素材数据加载器"""

    def __init__(self, data_dir: str, source: str = "github"):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "material.db")
        self.images_dir = os.path.join(data_dir, "images")
        self.source = source
        self.base_url = GITEE_BASE if source == "gitee" else GITHUB_BASE
        os.makedirs(self.images_dir, exist_ok=True)

    async def initialize_db(self):
        """初始化数据库"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    rarity INTEGER,
                    category TEXT,
                    icon_path TEXT,
                    description TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT,
                    material_id TEXT,
                    quantity INTEGER,
                    craft_type TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS stage_drops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage_id TEXT,
                    stage_name TEXT,
                    item_id TEXT,
                    drop_type TEXT,
                    drop_rate REAL,
                    sp_cost INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS update_log (
                    file_name TEXT PRIMARY KEY,
                    updated_at INTEGER,
                    status TEXT
                )
            """)
            await db.commit()

    async def download_json(self, filename: str) -> dict | None:
        """下载 JSON 文件"""
        url = self.base_url + filename
        logger.info(f"Downloading {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        text = await resp.text(encoding='utf-8')
                        return json.loads(text)
                    else:
                        logger.error(f"Failed to download {filename}: HTTP {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            return None

    async def get_last_update(self, filename: str) -> int:
        """获取上次更新时间"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT updated_at FROM update_log WHERE file_name = ?",
                (filename,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def set_last_update(self, filename: str, status: str = "success"):
        """记录更新时间"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO update_log (file_name, updated_at, status) VALUES (?, ?, ?)",
                (filename, int(time.time()), status)
            )
            await db.commit()

    async def update_all(self, force: bool = False) -> tuple[bool, str]:
        """更新所有数据"""
        results = []

        for filename, desc in FILES.items():
            # 检查是否需要更新
            last_update = await self.get_last_update(filename)
            if not force and last_update > 0 and (time.time() - last_update) < 7 * 86400:
                results.append(f"{desc}: 已是最新")
                continue

            data = await self.download_json(filename)
            if data is None:
                results.append(f"{desc}: 下载失败")
                await self.set_last_update(filename, "failed")
                continue

            try:
                if filename == "item_table.json":
                    await self.load_item_table(data)
                elif filename == "building_data.json":
                    await self.load_building_data(data)
                elif filename == "stage_table.json":
                    await self.load_stage_table(data)

                await self.set_last_update(filename, "success")
                results.append(f"{desc}: 更新成功")
            except Exception as e:
                logger.error(f"Failed to load {filename}: {e}")
                results.append(f"{desc}: 入库失败 - {e}")
                await self.set_last_update(filename, "error")

        return True, "\n".join(results)

    async def load_item_table(self, data: dict):
        """加载物品数据"""
        async with aiosqlite.connect(self.db_path) as db:
            count = 0
            for item_id, item in data.items():
                if not isinstance(item, dict):
                    continue

                name = item.get("name", "")
                rarity_str = item.get("rarity", "TIER_1")
                rarity_map = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3, "TIER_4": 4, "TIER_5": 5, "TIER_6": 6}
                rarity = rarity_map.get(rarity_str, 1)
                category = item.get("sortId", "")
                description = item.get("description", "")

                await db.execute("""
                    INSERT OR REPLACE INTO items (item_id, name, rarity, category, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (item_id, name, rarity, category, description))
                count += 1

            await db.commit()
            logger.info(f"Loaded {count} items")

    async def load_building_data(self, data: dict):
        """从 building_data.json 加载合成配方"""
        async with aiosqlite.connect(self.db_path) as db:
            # 清空旧配方
            await db.execute("DELETE FROM recipes")

            count = 0
            # 工作台配方
            workshop = data.get("workshop", {})
            for item_id, recipe_data in workshop.items():
                materials = recipe_data.get("materials", [])
                for mat in materials:
                    material_id = mat.get("id", "")
                    quantity = mat.get("count", 1)
                    await db.execute("""
                        INSERT INTO recipes (item_id, material_id, quantity, craft_type)
                        VALUES (?, ?, ?, 'WORKBENCH')
                    """, (item_id, material_id, quantity))
                    count += 1

            await db.commit()
            logger.info(f"Loaded {count} workshop recipes")

    async def load_stage_table(self, data: dict):
        """从 stage_table.json 加载关卡掉落"""
        async with aiosqlite.connect(self.db_path) as db:
            # 清空旧掉落
            await db.execute("DELETE FROM stage_drops")

            count = 0
            stages = data.get("stages", {})
            for stage_id, stage in stages.items():
                if not isinstance(stage, dict):
                    continue

                stage_name = stage.get("name", stage_id)
                # 关卡难度
                difficulty = stage.get("difficulty", "NORMAL")
                # 体力消耗
                ap_cost = stage.get("apCost", 0)

                # 掉落物
                drop_info = stage.get("dropInfo", {})
                drops = drop_info.get("drops", [])
                for drop in drops:
                    item_id = drop.get("itemId", "")
                    drop_type = drop.get("dropType", "")
                    drop_rate = drop.get("dropRate", 0.0)

                    if item_id:
                        await db.execute("""
                            INSERT INTO stage_drops (stage_id, stage_name, item_id, drop_type, drop_rate, sp_cost)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (stage_id, stage_name, item_id, drop_type, drop_rate, ap_cost))
                        count += 1

            await db.commit()
            logger.info(f"Loaded {count} stage drops")

    async def get_item(self, name: str) -> dict | None:
        """根据名称获取物品"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # 精确匹配
            async with db.execute(
                "SELECT * FROM items WHERE name = ?", (name,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)

            # 模糊匹配
            async with db.execute(
                "SELECT * FROM items WHERE name LIKE ? LIMIT 1", (f"%{name}%",)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)

            return None

    async def get_crafting_tree(self, item_id: str, max_depth: int = 3) -> CraftingTree | None:
        """获取物品的合成树"""
        async with aiosqlite.connect(self.db_path) as db:
            # 获取物品信息
            async with db.execute(
                "SELECT * FROM items WHERE item_id = ?", (item_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                columns = [desc[0] for desc in cursor.description]
                item = dict(zip(columns, row))

            tree = CraftingTree(item_id, item["name"], item["rarity"])

            # 获取配方
            if max_depth > 0:
                async with db.execute(
                    "SELECT * FROM recipes WHERE item_id = ?", (item_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    columns = [desc[0] for desc in cursor.description]
                    recipes = [dict(zip(columns, row)) for row in rows]

                    # 按材料分组
                    materials = {}
                    for recipe in recipes:
                        mat_id = recipe["material_id"]
                        qty = recipe["quantity"]
                        craft_type = recipe["craft_type"]

                        if mat_id not in materials:
                            materials[mat_id] = {"quantity": 0, "craft_type": craft_type}
                        materials[mat_id]["quantity"] += qty

                    # 递归获取子材料
                    for mat_id, info in materials.items():
                        child_tree = await self.get_crafting_tree(mat_id, max_depth - 1)
                        if child_tree:
                            child_tree.quantity = info["quantity"]
                            child_tree.craft_type = info["craft_type"]
                            tree.add_child(child_tree, info["quantity"])

            # 获取掉落关卡
            async with db.execute(
                "SELECT * FROM stage_drops WHERE item_id = ? ORDER BY drop_rate DESC", (item_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                drops = [dict(zip(columns, row)) for row in rows]

                for drop in drops[:10]:  # 最多10个关卡
                    tree.add_stage_drop(
                        drop["stage_name"],
                        drop["drop_type"],
                        drop["drop_rate"],
                        drop["sp_cost"]
                    )

            return tree

    async def get_stage_drops(self, item_id: str) -> list[dict]:
        """获取物品的掉落关卡"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM stage_drops WHERE item_id = ? ORDER BY drop_rate DESC", (item_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]

    async def search_items(self, query: str, rarity: int = None, limit: int = 20) -> list[dict]:
        """搜索物品"""
        async with aiosqlite.connect(self.db_path) as db:
            conditions = []
            params = []

            if query:
                conditions.append("name LIKE ?")
                params.append(f"%{query}%")

            if rarity is not None:
                conditions.append("rarity = ?")
                params.append(rarity)

            where = " AND ".join(conditions) if conditions else "1=1"
            sql = f"SELECT * FROM items WHERE {where} ORDER BY rarity DESC, name LIMIT ?"
            params.append(limit)

            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]

    async def get_item_count(self) -> int:
        """获取物品总数"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM items") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
