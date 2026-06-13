"""
AstrBot 明日方舟工具箱插件
"""
import contextlib
import os
import re
import logging
import tempfile
from datetime import datetime

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp

from .engine import GachaEngine
from .banner import BannerManager
from .crafting import MaterialDataLoader
from .renderer import GachaRenderer, MaterialRenderer, GachaAssetLoader, AssetGachaRenderer

logger = logging.getLogger(__name__)


@register("arknights_gacha", "皮皮朱", "明日方舟工具箱", "2.3.0")
class ArknightsToolboxPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 数据目录
        self.data_dir = self._get_data_dir()
        self.db_path = os.path.join(self.data_dir, "gacha.db")
        self.resource_dir = os.path.join(os.path.dirname(__file__), "resource")
        self.font_dir = os.path.join(self.resource_dir, "fonts")

        # 初始化抽卡组件
        source = config.get("data_source", "github")
        self.banner_manager = BannerManager(self.db_path, source)
        self.engine = GachaEngine(self.db_path, config)

        # 初始化渲染器（优先使用游戏素材）
        base_renderer = GachaRenderer(self.font_dir, self.resource_dir)
        assets_dir = os.path.join(self.resource_dir, "gacha_assets")
        if os.path.isdir(assets_dir):
            try:
                asset_loader = GachaAssetLoader(assets_dir)
                self.gacha_renderer = AssetGachaRenderer(asset_loader, base_renderer)
                logger.info("[初始化] 游戏素材渲染器已启用")
            except Exception as e:
                logger.warning(f"[初始化] 素材加载失败，使用程序化渲染: {e}")
                self.gacha_renderer = base_renderer
        else:
            self.gacha_renderer = base_renderer

        # 初始化素材组件 (BUG-17: 传入 config)
        self.loader = MaterialDataLoader(self.data_dir, source, config)
        self.material_renderer = MaterialRenderer(self.font_dir)

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
        logger.info("明日方舟工具箱插件初始化中...")

        # 初始化抽卡数据库
        await self.banner_manager.initialize_db()

        # 诊断字体文件状态
        font_path = os.path.join(self.font_dir, "NotoSansSC-Regular.ttf")
        if os.path.exists(font_path):
            font_size = os.path.getsize(font_path)
            logger.info(f"[诊断] 字体文件存在: {font_path} ({font_size} bytes)")
            if font_size < 100000:
                logger.warning(f"[诊断] 字体文件过小，可能损坏，正在重新下载...")
                await self.banner_manager.download_font(self.font_dir)
        else:
            logger.info(f"[诊断] 字体文件不存在: {font_path}，正在下载...")
            await self.banner_manager.download_font(self.font_dir)

        # 加载干员池（优先本地文件，其次 API 下载）
        async with self._get_db() as db:
            async with db.execute("SELECT COUNT(*) FROM operator_pool") as cursor:
                row = await cursor.fetchone()
                op_count = row[0] if row else 0

        if op_count == 0:
            # 尝试从本地 operators.json 加载
            resource_dir = os.path.join(os.path.dirname(__file__), "resource")
            success = await self.banner_manager.load_operators_from_local(resource_dir)
            if not success:
                # 本地文件不存在或加载失败，尝试从 API 下载
                logger.info("本地干员数据不可用，正在从 API 加载...")
                success = await self.banner_manager.load_operators_from_api()
                if success:
                    logger.info("干员池从 API 加载成功")
                else:
                    logger.warning("干员池加载失败，抽卡将使用默认干员")
            else:
                logger.info("干员池从本地文件加载成功")

        # 初始化素材数据库
        await self.loader.initialize_db()
        count = await self.loader.get_item_count()
        if count == 0:
            logger.info("素材数据库为空，首次使用请执行 /方舟更新数据")
        else:
            logger.info(f"素材数据库已有 {count} 个物品")

    async def terminate(self):
        """插件终止"""
        logger.info("明日方舟工具箱插件已终止")

    # ========== 临时文件管理 ==========

    @contextlib.contextmanager
    def _temp_image(self, image_bytes: bytes, suffix: str = ".png"):
        """
        BUG-7: 临时文件上下文管理器，确保清理

        写入图片字节到临时文件，yield 路径，在 finally 中保证删除。
        """
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(image_bytes)
            temp_path = f.name
        try:
            yield temp_path
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    # ========== 抽卡命令 ==========

    @filter.command("方舟抽卡")
    async def on_single_pull(self, event: AstrMessageEvent):
        """单抽"""
        event.stop_event()
        user_id = self._get_user_id(event)
        cost = self.config.get("single_pull_cost", 600)

        # BUG-2: 确保用户存在 (engine 内部也会检查，但这里需要提前获取 banner_id)
        async with self._get_db() as db:
            user_data = await self.engine._get_user_gacha(db, user_id)
            if not user_data:
                await self.engine._create_user_gacha(db, user_id)
                user_data = await self.engine._get_user_gacha(db, user_id)

        banner_id = await self._get_effective_banner_id(user_data.get("current_banner_id", 1))
        try:
            # BUG-2: 原子扣费 - cost 传入 engine，在同一事务中完成
            result = await self.engine.pull_single(user_id, banner_id, cost=cost)

            image_bytes = self.gacha_renderer.render_single_pull(result)
            # BUG-7: 使用上下文管理器确保临时文件清理
            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"单抽结果:"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except ValueError as e:
            # 合成玉不足等预期错误
            yield event.plain_result(str(e))
        except Exception as e:
            logger.error(f"抽卡失败: {e}")
            yield event.plain_result(f"抽卡失败: {e}")

    @filter.command("方舟十连")
    async def on_ten_pull(self, event: AstrMessageEvent):
        """十连抽"""
        event.stop_event()
        user_id = self._get_user_id(event)
        cost = self.config.get("ten_pull_cost", 6000)

        # BUG-2: 确保用户存在
        async with self._get_db() as db:
            user_data = await self.engine._get_user_gacha(db, user_id)
            if not user_data:
                await self.engine._create_user_gacha(db, user_id)
                user_data = await self.engine._get_user_gacha(db, user_id)

        banner_id = await self._get_effective_banner_id(user_data.get("current_banner_id", 1))
        try:
            # BUG-2: 原子扣费 - cost 传入 engine
            results = await self.engine.pull_ten(user_id, banner_id, cost=cost)

            image_bytes = self.gacha_renderer.render_ten_pull(results)
            # BUG-7: 使用上下文管理器
            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"十连结果:"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except ValueError as e:
            yield event.plain_result(str(e))
        except Exception as e:
            logger.error(f"十连失败: {e}")
            yield event.plain_result(f"十连失败: {e}")

    @filter.command("方舟卡池")
    async def on_banner_list(self, event: AstrMessageEvent):
        """查看可用卡池"""
        event.stop_event()
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

    @filter.command("方舟切换卡池")
    async def on_switch_banner(self, event: AstrMessageEvent):
        """切换当前卡池"""
        event.stop_event()
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

    @filter.command("方舟背包")
    async def on_inventory(self, event: AstrMessageEvent):
        """查看背包"""
        event.stop_event()
        user_id = self._get_user_id(event)

        # DRY: 使用 engine 的方法
        async with self._get_db() as db:
            user_data = await self.engine._get_user_gacha(db, user_id)
            if not user_data:
                yield event.plain_result("您还没有抽卡记录")
                return

            async with db.execute(
                "SELECT op.*, uo.potential FROM user_operators uo "
                "JOIN operator_pool op ON uo.char_id = op.char_id "
                "WHERE uo.user_id = ? ORDER BY op.rarity DESC, op.name",
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                operators = [dict(zip(columns, row)) for row in rows]

        try:
            image_bytes = self.gacha_renderer.render_inventory(user_data, operators)
            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"背包:"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except Exception as e:
            logger.error(f"渲染背包失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    @filter.command("方舟签到")
    async def on_sign_in(self, event: AstrMessageEvent):
        """每日签到"""
        event.stop_event()
        user_id = self._get_user_id(event)

        async with self._get_db() as db:
            user_data = await self.engine._get_user_gacha(db, user_id)
            if not user_data:
                await self.engine._create_user_gacha(db, user_id)
                user_data = await self.engine._get_user_gacha(db, user_id)

            today = datetime.now().strftime("%Y-%m-%d")
            last_sign = user_data.get("sign_date", "")

            if last_sign == today:
                yield event.plain_result("今日已签到，明天再来吧！")
                return

            daily_orundum = self.config.get("daily_orundum", 200)
            await db.execute("""
                UPDATE user_gacha
                SET orundum = orundum + ?, sign_date = ?
                WHERE user_id = ?
            """, (daily_orundum, today, user_id))
            await db.commit()

            yield event.plain_result(f"签到成功！获得 {daily_orundum} 合成玉")

    @filter.command("方舟抽卡统计")
    async def on_statistics(self, event: AstrMessageEvent):
        """查看抽卡统计"""
        event.stop_event()
        user_id = self._get_user_id(event)

        async with self._get_db() as db:
            user_data = await self.engine._get_user_gacha(db, user_id)
            if not user_data:
                yield event.plain_result("您还没有抽卡记录")
                return

        try:
            image_bytes = self.gacha_renderer.render_statistics(user_data)
            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"抽卡统计:"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except Exception as e:
            logger.error(f"渲染统计失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    # ========== 素材命令 ==========

    @filter.command("方舟素材")
    async def on_material(self, event: AstrMessageEvent):
        """查看素材合成路线"""
        event.stop_event()
        name = self._get_text_arg(event)
        if not name:
            yield event.plain_result("请输入素材名称，例如: /方舟素材 聚酸酯")
            return

        item = await self.loader.get_item(name)
        if not item:
            yield event.plain_result(f"未找到素材: {name}")
            return

        tree = await self.loader.get_crafting_tree(item["item_id"], max_depth=2)
        if not tree:
            yield event.plain_result(f"未找到合成路线: {name}")
            return

        try:
            tree_width = self.config.get("tree_width", 800)
            tree_height = self.config.get("tree_height", 600)
            image_bytes = self.material_renderer.render_crafting_tree(
                tree, width=tree_width, height=tree_height
            )

            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"【{item['name']}】合成路线"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except Exception as e:
            logger.error(f"渲染合成树失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    @filter.command("方舟关卡")
    async def on_stage(self, event: AstrMessageEvent):
        """查看素材掉落关卡"""
        event.stop_event()
        name = self._get_text_arg(event)
        if not name:
            yield event.plain_result("请输入素材名称，例如: /方舟关卡 聚酸酯")
            return

        item = await self.loader.get_item(name)
        if not item:
            yield event.plain_result(f"未找到素材: {name}")
            return

        drops = await self.loader.get_stage_drops(item["item_id"])
        if not drops:
            yield event.plain_result(f"未找到掉落关卡: {name}")
            return

        try:
            image_bytes = self.material_renderer.render_stage_drops(item["name"], drops)
            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"【{item['name']}】掉落关卡"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except Exception as e:
            logger.error(f"渲染掉落列表失败: {e}")
            text = f"【{item['name']}】掉落关卡:\n"
            for drop in drops[:10]:
                stage = drop["stage_name"]
                rate = drop["drop_rate"]
                sp = drop["sp_cost"]
                rate_text = f"{rate * 100:.1f}%" if rate > 0 else "未知"
                text += f"  {stage} | {rate_text} | {sp}理智\n"
            yield event.plain_result(text)

    @filter.command("方舟合成")
    async def on_craft(self, event: AstrMessageEvent):
        """查看完整合成成本"""
        event.stop_event()
        name = self._get_text_arg(event)
        if not name:
            yield event.plain_result("请输入素材名称，例如: /方舟合成 酮凝集组")
            return

        item = await self.loader.get_item(name)
        if not item:
            yield event.plain_result(f"未找到素材: {name}")
            return

        tree = await self.loader.get_crafting_tree(item["item_id"], max_depth=4)
        if not tree:
            yield event.plain_result(f"未找到合成路线: {name}")
            return

        try:
            image_bytes = self.material_renderer.render_crafting_tree(
                tree, width=1000, height=800
            )
            with self._temp_image(image_bytes) as temp_path:
                yield event.chain_result([
                    Comp.Plain(f"【{item['name']}】完整合成成本"),
                    Comp.Image.fromFileSystem(temp_path)
                ])
        except Exception as e:
            logger.error(f"渲染合成树失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    # ========== 数据管理 ==========

    @filter.command("方舟更新数据")
    async def on_update_data(self, event: AstrMessageEvent):
        """更新游戏数据（卡池 + 素材）"""
        event.stop_event()
        yield event.plain_result("正在更新游戏数据，请稍候...")

        success1, msg1 = await self.banner_manager.sync_banners(force=True)
        success2, msg2 = await self.loader.update_all(force=True)
        count = await self.loader.get_item_count()

        result = f"数据更新完成！\n\n【卡池数据】{msg1}\n【素材数据】{msg2}\n\n物品总数: {count}"
        yield event.plain_result(result)

    # ========== 辅助方法 ==========

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        """获取用户ID"""
        return event.get_sender_id() or "unknown"

    def _get_arg(self, event: AstrMessageEvent) -> str:
        """获取数字参数（用于切换卡池等）"""
        try:
            if hasattr(event, 'message_str') and event.message_str:
                text = event.message_str.strip()
                return re.sub(r'[^0-9]', '', text)
        except Exception:
            pass
        return ""

    def _get_text_arg(self, event: AstrMessageEvent) -> str:
        """获取文本参数（用于素材查询等）"""
        try:
            if hasattr(event, 'message_str') and event.message_str:
                return event.message_str.strip()
        except Exception:
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
