"""
AstrBot 明日方舟抽卡模拟插件
"""
import os
import logging
import tempfile
from datetime import datetime

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp

from .engine import GachaEngine
from .banner import BannerManager
from .renderer import GachaRenderer

logger = logging.getLogger(__name__)


@register("arknights_gacha", "皮皮朱", "明日方舟抽卡模拟器", "1.0.0")
class ArknightsGachaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 数据目录
        self.data_dir = self._get_data_dir()
        self.db_path = os.path.join(self.data_dir, "gacha.db")
        self.font_dir = os.path.join(os.path.dirname(__file__), "resource", "fonts")

        # 初始化组件
        source = config.get("data_source", "github")
        self.banner_manager = BannerManager(self.db_path, source)
        self.engine = GachaEngine(self.db_path, config)
        self.renderer = GachaRenderer(self.font_dir)

    def _get_data_dir(self) -> str:
        """获取数据目录"""
        try:
            from astrbot.api.star import StarTools
            return StarTools.get_data_dir("arknights_gacha")
        except (ImportError, AttributeError):
            data_dir = os.path.join(os.path.dirname(__file__), "data")
            os.makedirs(data_dir, exist_ok=True)
            return data_dir

    async def initialize(self):
        """插件初始化"""
        logger.info("方舟抽卡模拟插件初始化中...")

        # 初始化数据库
        await self.banner_manager.initialize_db()

        # 检查是否需要自动更新卡池
        # TODO: 实现自动同步

    async def terminate(self):
        """插件终止"""
        logger.info("方舟抽卡模拟插件已终止")

    # ========== 命令: /方舟抽卡 ==========
    @filter.command("方舟抽卡")
    async def on_single_pull(self, event: AstrMessageEvent):
        """单抽"""
        user_id = self._get_user_id(event)

        # 检查货币
        async with self._get_db() as db:
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                await self._create_user_gacha(db, user_id)
                user_data = await self._get_user_gacha(db, user_id)

            cost = self.config.get("single_pull_cost", 600)
            if user_data["orundum"] < cost:
                yield event.plain_result(f"合成玉不足！需要 {cost}，当前 {user_data['orundum']}")
                return

            # 扣除货币
            await db.execute(
                "UPDATE user_gacha SET orundum = orundum - ? WHERE user_id = ?",
                (cost, user_id)
            )
            await db.commit()

        # 执行抽卡
        banner_id = await self._get_effective_banner_id(user_data.get("current_banner_id", 1))
        try:
            result = await self.engine.pull_single(user_id, banner_id)

            # 渲染结果
            image_bytes = self.renderer.render_single_pull(result)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                temp_path = f.name

            yield event.chain_result([
                Comp.Plain(f"单抽结果:"),
                Comp.Image.fromFileSystem(temp_path)
            ])

            try:
                os.unlink(temp_path)
            except:
                pass
        except Exception as e:
            logger.error(f"抽卡失败: {e}")
            yield event.plain_result(f"抽卡失败: {e}")

    # ========== 命令: /方舟十连 ==========
    @filter.command("方舟十连")
    async def on_ten_pull(self, event: AstrMessageEvent):
        """十连抽"""
        user_id = self._get_user_id(event)

        # 检查货币
        async with self._get_db() as db:
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                await self._create_user_gacha(db, user_id)
                user_data = await self._get_user_gacha(db, user_id)

            cost = self.config.get("ten_pull_cost", 6000)
            if user_data["orundum"] < cost:
                yield event.plain_result(f"合成玉不足！需要 {cost}，当前 {user_data['orundum']}")
                return

            # 扣除货币
            await db.execute(
                "UPDATE user_gacha SET orundum = orundum - ? WHERE user_id = ?",
                (cost, user_id)
            )
            await db.commit()

        # 执行十连
        banner_id = await self._get_effective_banner_id(user_data.get("current_banner_id", 1))
        try:
            results = await self.engine.pull_ten(user_id, banner_id)

            # 渲染结果
            image_bytes = self.renderer.render_ten_pull(results)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                temp_path = f.name

            yield event.chain_result([
                Comp.Plain(f"十连结果:"),
                Comp.Image.fromFileSystem(temp_path)
            ])

            try:
                os.unlink(temp_path)
            except:
                pass
        except Exception as e:
            logger.error(f"十连失败: {e}")
            yield event.plain_result(f"十连失败: {e}")

    # ========== 命令: /方舟卡池 ==========
    @filter.command("方舟卡池")
    async def on_banner_list(self, event: AstrMessageEvent):
        """查看可用卡池"""
        banners = await self.banner_manager.get_active_banners()

        if not banners:
            yield event.plain_result("当前没有可用卡池")
            return

        text = "当前可用卡池:\n\n"
        for banner in banners[:10]:
            pool_name = banner.get("pool_name", "未知")
            pool_type = banner.get("pool_type", 0)
            type_names = ["标准", "限定", "联合", "前路", "中坚", "奔涌"]
            type_name = type_names[pool_type] if pool_type < len(type_names) else "未知"

            text += f"[{banner['id']}] {pool_name} ({type_name})\n"

        text += "\n使用 /方舟切换卡池 <ID> 切换卡池"
        yield event.plain_result(text)

    # ========== 命令: /方舟切换卡池 ==========
    @filter.command("方舟切换卡池")
    async def on_switch_banner(self, event: AstrMessageEvent):
        """切换当前卡池"""
        user_id = self._get_user_id(event)
        banner_id_str = self._get_arg(event)

        if not banner_id_str:
            yield event.plain_result("请输入卡池ID，例如: /方舟切换卡池 1")
            return

        try:
            banner_id = int(banner_id_str)
        except ValueError:
            yield event.plain_result("无效的卡池ID")
            return

        success = await self.banner_manager.switch_banner(user_id, banner_id)
        if success:
            yield event.plain_result(f"已切换到卡池 {banner_id}")
        else:
            yield event.plain_result(f"卡池 {banner_id} 不存在")

    # ========== 命令: /方舟背包 ==========
    @filter.command("方舟背包")
    async def on_inventory(self, event: AstrMessageEvent):
        """查看背包"""
        user_id = self._get_user_id(event)

        async with self._get_db() as db:
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                yield event.plain_result("您还没有抽卡记录")
                return

            # 获取干员列表
            async with db.execute(
                "SELECT op.*, uo.potential FROM user_operators uo "
                "JOIN operator_pool op ON uo.char_id = op.char_id "
                "WHERE uo.user_id = ? ORDER BY op.rarity DESC, op.name",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                operators = [dict(zip(columns, row)) for row in rows]

        # 渲染背包
        try:
            image_bytes = self.renderer.render_inventory(user_data, operators)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                temp_path = f.name

            yield event.chain_result([
                Comp.Plain(f"背包:"),
                Comp.Image.fromFileSystem(temp_path)
            ])

            try:
                os.unlink(temp_path)
            except:
                pass
        except Exception as e:
            logger.error(f"渲染背包失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    # ========== 命令: /方舟签到 ==========
    @filter.command("方舟签到")
    async def on_sign_in(self, event: AstrMessageEvent):
        """每日签到"""
        user_id = self._get_user_id(event)

        async with self._get_db() as db:
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                await self._create_user_gacha(db, user_id)
                user_data = await self._get_user_gacha(db, user_id)

            # 检查今日是否已签到
            today = datetime.now().strftime("%Y-%m-%d")
            last_sign = user_data.get("sign_date", "")

            if last_sign == today:
                yield event.plain_result("今日已签到，明天再来吧！")
                return

            # 发放奖励
            daily_orundum = self.config.get("daily_orundum", 200)
            await db.execute("""
                UPDATE user_gacha
                SET orundum = orundum + ?, sign_date = ?
                WHERE user_id = ?
            """, (daily_orundum, today, user_id))
            await db.commit()

            yield event.plain_result(f"签到成功！获得 {daily_orundum} 合成玉")

    # ========== 命令: /方舟抽卡统计 ==========
    @filter.command("方舟抽卡统计")
    async def on_statistics(self, event: AstrMessageEvent):
        """查看抽卡统计"""
        user_id = self._get_user_id(event)

        async with self._get_db() as db:
            user_data = await self._get_user_gacha(db, user_id)
            if not user_data:
                yield event.plain_result("您还没有抽卡记录")
                return

        # 渲染统计
        try:
            image_bytes = self.renderer.render_statistics(user_data)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                temp_path = f.name

            yield event.chain_result([
                Comp.Plain(f"抽卡统计:"),
                Comp.Image.fromFileSystem(temp_path)
            ])

            try:
                os.unlink(temp_path)
            except:
                pass
        except Exception as e:
            logger.error(f"渲染统计失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    # ========== 命令: /方舟更新数据 ==========
    @filter.command("方舟更新数据")
    async def on_update(self, event: AstrMessageEvent):
        """更新游戏数据"""
        yield event.plain_result("正在更新卡池数据，请稍候...")

        success, msg = await self.banner_manager.sync_banners(force=True)
        yield event.plain_result(f"卡池数据更新: {msg}")

    # ========== 辅助方法 ==========
    def _get_user_id(self, event: AstrMessageEvent) -> str:
        """获取用户ID"""
        return event.get_sender_id() or "unknown"

    def _get_arg(self, event: AstrMessageEvent) -> str:
        """获取命令参数"""
        import re
        try:
            if hasattr(event, 'message_str') and event.message_str:
                text = event.message_str.strip()
                return re.sub(r'[^0-9]', '', text)
        except:
            pass
        return ""

    async def _get_effective_banner_id(self, current_banner_id: int) -> int:
        """获取有效的卡池ID，当前卡池不可用时回退到活跃卡池"""
        async with self._get_db() as db:
            async with db.execute(
                "SELECT id FROM banners WHERE id = ? AND is_active = 1",
                (current_banner_id,)
            ) as cursor:
                if await cursor.fetchone():
                    return current_banner_id

            # 回退到最近的活跃卡池
            async with db.execute(
                "SELECT id FROM banners WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]

        return current_banner_id

    def _get_db(self):
        """获取数据库连接"""
        import aiosqlite
        return aiosqlite.connect(self.db_path)

    async def _get_user_gacha(self, db, user_id: str) -> dict:
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
