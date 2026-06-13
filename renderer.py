"""
抽卡结果 & 素材合成树图片渲染器
参考 nonebot_plugin_gamedraw 的卡片式布局设计
"""
import io
import math
import os
from PIL import Image, ImageDraw, ImageFont
from .engine import GachaResult
from .crafting import CraftingTree, RARITY_COLORS as CRAFT_RARITY_COLORS


# ========== 颜色配置 ==========

# 背景色 (参考 gamedraw 的浅色风格)
BG_COLOR = "#EFF2F5"
CARD_BG = "#FFFFFF"
DARK_TEXT = "#333333"
GRAY_TEXT = "#666666"
LIGHT_TEXT = "#999999"
DIVIDER_COLOR = "#D0D5DD"

# 星级颜色 (RGB)
RARITY_COLORS = {
    3: (78, 126, 189),    # 蓝
    4: (141, 88, 168),    # 紫
    5: (233, 178, 60),    # 金
    6: (239, 79, 67),     # 红
}

# 稀有度对应的卡片边框/渐变色
RARITY_CARD_COLORS = {
    3: ("#4E7EBD", "#E8EEF5"),
    4: ("#8D58A8", "#F0E8F5"),
    5: ("#E9B23C", "#FDF5E6"),
    6: ("#EF4F43", "#FDE8E7"),
}


# ========== 字体工具 ==========

_FONT_CACHE = {}


def _validate_cjk_font(font, test_chars="阿米娅星"):
    """验证字体是否支持中文字符"""
    try:
        for char in test_chars:
            bbox = font.getbbox(char)
            if bbox is None or (bbox[2] - bbox[0]) <= 0:
                return False
        return True
    except Exception:
        return False


def _get_font(font_dir, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """获取字体（带缓存）"""
    import logging
    logger = logging.getLogger(__name__)

    key = (font_dir, size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    font = None

    # 1. 尝试插件目录中的字体
    if font_dir:
        font_paths = [
            os.path.join(font_dir, "HarmonyOS_Sans_SC_Bold.ttf" if bold else "HarmonyOS_Sans_SC_Regular.ttf"),
            os.path.join(font_dir, "NotoSansSC-Regular.ttf"),
            os.path.join(font_dir, "HarmonyOS_Sans_SC.ttf"),
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                file_size = os.path.getsize(fp)
                logger.info(f"[Font] 尝试加载: {fp} ({file_size} bytes)")
                try:
                    test_font = ImageFont.truetype(fp, size)
                    if _validate_cjk_font(test_font):
                        font = test_font
                        logger.info(f"[Font] 加载成功: {fp}")
                        break
                    else:
                        logger.warning(f"[Font] 字体不支持中文: {fp}")
                except Exception as e:
                    logger.warning(f"[Font] 加载失败: {fp} - {e}")
            else:
                logger.debug(f"[Font] 文件不存在: {fp}")

    # 2. 尝试系统字体
    if font is None:
        system_fonts = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/noto-cjk/NotoSansSC-Regular.otf",
            "/usr/share/fonts/google-noto-cjk/NotoSansSC-Regular.otf",
        ]
        for sf in system_fonts:
            if os.path.exists(sf):
                logger.info(f"[Font] 尝试系统字体: {sf}")
                try:
                    test_font = ImageFont.truetype(sf, size)
                    if _validate_cjk_font(test_font):
                        font = test_font
                        logger.info(f"[Font] 系统字体成功: {sf}")
                        break
                except Exception as e:
                    logger.warning(f"[Font] 系统字体失败: {sf} - {e}")

    # 3. 回退
    if font is None:
        logger.error("[Font] 未找到可用的中文字体，将显示乱码")
        font = ImageFont.load_default()

    _FONT_CACHE[key] = font
    return font


# ========== 绘图工具 ==========

def _draw_rounded_rect(draw: ImageDraw.Draw, xy: tuple, radius: int, fill):
    """绘制圆角矩形"""
    x1, y1, x2, y2 = xy
    if x2 - x1 < 2 * radius or y2 - y1 < 2 * radius:
        draw.rectangle(xy, fill=fill)
        return
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    draw.pieslice([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=fill)
    draw.pieslice([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=fill)
    draw.pieslice([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=fill)
    draw.pieslice([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=fill)


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _create_gradient_bg(width: int, height: int, color1: tuple, color2: tuple,
                        direction: str = "vertical") -> Image.Image:
    """创建渐变背景"""
    img = Image.new("RGBA", (width, height))
    pixels = img.load()

    for x in range(width):
        for y in range(height):
            if direction == "vertical":
                ratio = y / height
            else:
                ratio = x / width
            r = int(color1[0] + (color2[0] - color1[0]) * ratio)
            g = int(color1[1] + (color2[1] - color1[1]) * ratio)
            b = int(color1[2] + (color2[2] - color1[2]) * ratio)
            pixels[x, y] = (r, g, b, 255)

    return img


def _add_glow_effect(img: Image.Image, color: tuple, intensity: int = 30) -> Image.Image:
    """添加光晕效果"""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)

    # 在边缘绘制半透明光晕
    for i in range(intensity):
        alpha = int(255 * (1 - i / intensity) * 0.3)
        draw.rectangle([i, i, img.width - i - 1, img.height - i - 1],
                      fill=(*color, alpha))

    # 合并光晕
    img = Image.alpha_composite(img, glow)
    return img


def _draw_rarity_card_bg(card_w: int, card_h: int, rarity: int) -> Image.Image:
    """绘制稀有度卡片背景（参考游戏风格的渐变效果）"""
    # 稀有度对应的渐变色
    rarity_gradients = {
        3: ((200, 220, 240), (230, 240, 250)),  # 蓝色渐变
        4: ((220, 200, 240), (240, 230, 250)),  # 紫色渐变
        5: ((250, 240, 200), (255, 250, 220)),  # 金色渐变
        6: ((255, 220, 200), (255, 240, 220)),  # 红色/橙色渐变
    }

    color1, color2 = rarity_gradients.get(rarity, ((240, 240, 240), (250, 250, 250)))

    # 创建渐变背景
    bg = _create_gradient_bg(card_w, card_h, color1, color2)

    # 高稀有度添加光晕
    if rarity >= 5:
        glow_color = RARITY_COLORS.get(rarity, (255, 255, 255))
        bg = _add_glow_effect(bg, glow_color, intensity=20 if rarity == 5 else 30)

    return bg


def _draw_star_icon(img: Image.Image, x: int, y: int, size: int, color):
    """绘制星形图标 (填充五角星)"""
    draw = ImageDraw.Draw(img)
    cx = x + size / 2
    cy = y + size / 2
    r_outer = size / 2
    r_inner = r_outer * 0.38

    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        px = cx + r * math.cos(angle)
        py = cy - r * math.sin(angle)
        points.append((px, py))
    draw.polygon(points, fill=color)


def _draw_stars_row(img: Image.Image, x: int, y: int, count: int, star_size: int, color):
    """绘制一排星星图标"""
    gap = 2
    for i in range(count):
        sx = x + i * (star_size + gap)
        _draw_star_icon(img, sx, y, star_size, color)


def _render_stars(rarity: int) -> str:
    """渲染星级文字 - 使用数字 + 星字，避免特殊字符问题"""
    return f"{rarity}星"


def _draw_circle_badge(img: Image.Image, x: int, y: int, text: str, bg_color=(220, 50, 50)):
    """绘制圆形徽章 (用于重复次数标记)"""
    draw = ImageDraw.Draw(img)
    font = _get_font(None, 14)
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad = 6
    w = tw + pad * 2
    h = th + pad * 2
    _draw_rounded_rect(draw, (x, y, x + w, y + h), h // 2, bg_color)
    draw.text((x + pad, y + pad - 1), text, fill=(255, 255, 255), font=font)


def _to_bytes(img: Image.Image) -> bytes:
    """将图片转为 PNG bytes"""
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.getvalue()


# ========== 抽卡渲染器 ==========

class GachaRenderer:
    """抽卡结果渲染器 - 卡片式网格布局"""

    def __init__(self, font_dir: str = None, resource_dir: str = None):
        self.font_dir = font_dir
        self.resource_dir = resource_dir
        self._avatar_cache = {}

    def _load_avatar(self, char_id: str, name: str, size: tuple) -> Image.Image:
        """尝试加载干员头像图片"""
        if not self.resource_dir:
            return None

        avatar_dir = os.path.join(self.resource_dir, "avatars")
        if not os.path.isdir(avatar_dir):
            return None

        if char_id in self._avatar_cache:
            avatar = self._avatar_cache[char_id]
            if avatar is not None:
                return avatar.resize(size, Image.LANCZOS)
            return None

        avatar = None
        candidates = [
            os.path.join(avatar_dir, f"{char_id}.png"),
            os.path.join(avatar_dir, f"{name}.png"),
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    avatar = Image.open(path).convert("RGBA")
                    break
                except Exception:
                    pass

        self._avatar_cache[char_id] = avatar
        if avatar is not None:
            return avatar.resize(size, Image.LANCZOS)
        return None

    def _draw_single_card(self, result: GachaResult, card_w: int, card_h: int,
                          duplicate_count: int = 0) -> Image.Image:
        """绘制单个干员卡片"""
        rarity_color = RARITY_COLORS.get(result.rarity, (128, 128, 128))
        border_color, bg_color_hex = RARITY_CARD_COLORS.get(
            result.rarity, ("#808080", "#F0F0F0")
        )
        border_rgb = _hex_to_rgb(border_color)

        # 使用渐变背景
        card = _draw_rarity_card_bg(card_w, card_h, result.rarity)
        draw = ImageDraw.Draw(card)

        # 顶部稀有度色条
        draw.rectangle([0, 0, card_w, 4], fill=border_rgb)

        # 头像区域 (上方 60% 区域)
        avatar_area_h = int(card_h * 0.55)
        avatar_size = min(card_w - 16, avatar_area_h - 8)

        avatar = self._load_avatar(result.char_id, result.name, (avatar_size, avatar_size))
        if avatar:
            ax = (card_w - avatar_size) // 2
            ay = 8
            card.paste(avatar, (ax, ay), avatar if avatar.mode == "RGBA" else None)
        else:
            # 无头像: 绘制稀有色块 + 名称首字
            block_w = min(64, card_w - 20)
            block_h = min(64, avatar_area_h - 12)
            bx = (card_w - block_w) // 2
            by = 8
            _draw_rounded_rect(draw, (bx, by, bx + block_w, by + block_h), 8, rarity_color)
            # 名称首字
            font_initial = _get_font(self.font_dir, 28, bold=True)
            initial = result.name[0] if result.name else "?"
            ib = font_initial.getbbox(initial)
            iw = ib[2] - ib[0]
            ih = ib[3] - ib[1]
            draw.text(
                (bx + (block_w - iw) // 2, by + (block_h - ih) // 2 - 2),
                initial, fill=(255, 255, 255), font=font_initial
            )

        # 星星 (头像下方)
        star_y = avatar_area_h + 2
        star_size = 12
        total_stars_w = result.rarity * (star_size + 2) - 2
        stars_x = (card_w - total_stars_w) // 2
        _draw_stars_row(card, stars_x, star_y, result.rarity, star_size, rarity_color)

        # 名称
        font_name = _get_font(self.font_dir, 14, bold=True)
        display_name = result.name
        if len(display_name) > 6:
            display_name = display_name[:5] + ".."
        nb = font_name.getbbox(display_name)
        nw = nb[2] - nb[0]
        name_y = star_y + star_size + 4
        draw.text(((card_w - nw) // 2, name_y), display_name, fill=DARK_TEXT, font=font_name)

        # 稀有度标签
        font_small = _get_font(self.font_dir, 11)
        rarity_text = _render_stars(result.rarity)
        rb = font_small.getbbox(rarity_text)
        rw = rb[2] - rb[0]
        tag_y = name_y + 18
        _draw_rounded_rect(
            draw,
            ((card_w - rw) // 2 - 4, tag_y, (card_w + rw) // 2 + 4, tag_y + 15),
            3, rarity_color
        )
        draw.text(((card_w - rw) // 2, tag_y + 1), rarity_text, fill=(255, 255, 255), font=font_small)

        # 新干员标记
        if result.is_new:
            _draw_circle_badge(card, card_w - 36, 6, "NEW", (50, 180, 80))

        # 重复次数标记
        if duplicate_count > 1:
            _draw_circle_badge(card, 2, 6, f"x{duplicate_count}", (220, 50, 50))

        return card

    def render_single_pull(self, result: GachaResult, width: int = 400, height: int = 300) -> bytes:
        """渲染单抽结果"""
        img = Image.new("RGB", (width, height), _hex_to_rgb(BG_COLOR))

        card_w = 180
        card_h = 220
        card = self._draw_single_card(result, card_w, card_h)

        cx = (width - card_w) // 2
        cy = (height - card_h) // 2
        img.paste(card, (cx, cy), card)

        return _to_bytes(img)

    def render_ten_pull(self, results: list, width: int = 780) -> bytes:
        """渲染十连结果 - 卡片网格布局"""
        count = len(results)
        num_per_row = min(5, count)
        rows = math.ceil(count / num_per_row)

        card_w = (width - 40 - (num_per_row - 1) * 10) // num_per_row
        card_h = int(card_w * 1.45)
        gap = 10

        grid_h = rows * card_h + (rows - 1) * gap

        # 统计区高度
        stats_h = 90
        total_h = 20 + grid_h + 15 + stats_h + 15

        img = Image.new("RGB", (width, total_h), _hex_to_rgb(BG_COLOR))
        draw = ImageDraw.Draw(img)

        # 标题
        font_title = _get_font(self.font_dir, 22, bold=True)
        draw.text((20, 12), "十连寻访结果", fill=DARK_TEXT, font=font_title)

        # 绘制卡片网格
        for i, result in enumerate(results):
            col = i % num_per_row
            row = i // num_per_row
            x = 20 + col * (card_w + gap)
            y = 45 + row * (card_h + gap)

            card = self._draw_single_card(result, card_w, card_h)
            img.paste(card, (x, y), card)

        # 统计区域
        stats_y = 45 + grid_h + 15
        draw.line([(20, stats_y), (width - 20, stats_y)], fill=DIVIDER_COLOR, width=1)
        stats_y += 10

        font_stats = _get_font(self.font_dir, 14)
        font_note = _get_font(self.font_dir, 12)

        # 星级统计
        rarity_counts = {}
        for r in results:
            rarity_counts[r.rarity] = rarity_counts.get(r.rarity, 0) + 1

        # 星级统计 (手动排列避免重叠)
        sx = 20
        for star in [6, 5, 4, 3]:
            c = rarity_counts.get(star, 0)
            if c > 0:
                color = RARITY_COLORS.get(star, (128, 128, 128))
                text = f"{star}星:{c}"
                draw.text((sx, stats_y), text, fill=color, font=font_stats)
                bbox = font_stats.getbbox(text)
                sx += bbox[2] - bbox[0] + 20

        # 最多重复
        name_counts = {}
        for r in results:
            name_counts[r.name] = name_counts.get(r.name, 0) + 1
        max_dup = max(name_counts.values()) if name_counts else 0
        if max_dup > 1:
            max_name = [n for n, c in name_counts.items() if c == max_dup][0]
            stats_y += 22
            draw.text((20, stats_y), f"最多重复: {max_name} (x{max_dup})", fill=GRAY_TEXT, font=font_stats)

        # 概率说明
        stats_y += 28
        draw.text(
            (20, stats_y),
            "【6星:2%  5星:8%  4星:90%  50抽保底】",
            fill=LIGHT_TEXT, font=font_note
        )

        return _to_bytes(img)

    def render_inventory(self, user_data: dict, operators: list, width: int = 780) -> bytes:
        """渲染背包 - 卡片网格布局"""
        if not operators:
            img = Image.new("RGB", (width, 150), _hex_to_rgb(BG_COLOR))
            draw = ImageDraw.Draw(img)
            font = _get_font(self.font_dir, 18)
            draw.text((20, 20), "背包为空", fill=GRAY_TEXT, font=font)

            # 货币信息
            font_small = _get_font(self.font_dir, 14)
            orundum = user_data.get("orundum", 0)
            permits = user_data.get("permits", 0)
            ten_permits = user_data.get("ten_permits", 0)
            draw.text((20, 55), f"合成玉: {orundum}  单抽券: {permits}  十连券: {ten_permits}", fill=GRAY_TEXT, font=font_small)
            return _to_bytes(img)

        # 卡片布局
        card_w = 110
        card_h = 155
        gap = 8
        num_per_row = (width - 40 + gap) // (card_w + gap)
        num_per_row = max(num_per_row, 1)
        rows = math.ceil(len(operators) / num_per_row)

        grid_h = rows * card_h + (rows - 1) * gap
        header_h = 85
        total_h = header_h + grid_h + 20

        img = Image.new("RGB", (width, total_h), _hex_to_rgb(BG_COLOR))
        draw = ImageDraw.Draw(img)

        # 标题
        font_title = _get_font(self.font_dir, 20, bold=True)
        draw.text((20, 12), "干员背包", fill=DARK_TEXT, font=font_title)

        # 货币
        font_small = _get_font(self.font_dir, 13)
        orundum = user_data.get("orundum", 0)
        permits = user_data.get("permits", 0)
        ten_permits = user_data.get("ten_permits", 0)
        yellow_tickets = user_data.get("green_tickets", 0)
        draw.text((20, 42), f"合成玉: {orundum}", fill=(78, 126, 189), font=font_small)
        draw.text((160, 42), f"单抽券: {permits}  十连券: {ten_permits}", fill=GRAY_TEXT, font=font_small)
        draw.text((20, 62), f"黄票: {yellow_tickets}", fill=(233, 178, 60), font=font_small)
        draw.text((160, 62), f"干员: {len(operators)}名", fill=GRAY_TEXT, font=font_small)

        # 绘制干员卡片
        for i, op in enumerate(operators):
            col = i % num_per_row
            row = i // num_per_row
            x = 20 + col * (card_w + gap)
            y = header_h + row * (card_h + gap)

            rarity = op.get("rarity", 1)
            name = op.get("name", "")
            char_id = op.get("char_id", "")
            potential = op.get("potential", 1)

            # 创建简单卡片
            rarity_color = RARITY_COLORS.get(rarity, (128, 128, 128))
            border_color, bg_hex = RARITY_CARD_COLORS.get(rarity, ("#808080", "#F0F0F0"))
            bg_rgb = _hex_to_rgb(bg_hex)
            border_rgb = _hex_to_rgb(border_color)

            card = Image.new("RGBA", (card_w, card_h), bg_rgb)
            cd = ImageDraw.Draw(card)

            # 顶部色条
            cd.rectangle([0, 0, card_w, 4], fill=border_rgb)

            # 头像
            avatar_area_h = int(card_h * 0.5)
            avatar_size = min(card_w - 16, avatar_area_h - 8)
            avatar = self._load_avatar(char_id, name, (avatar_size, avatar_size))
            if avatar:
                ax = (card_w - avatar_size) // 2
                ay = 8
                card.paste(avatar, (ax, ay), avatar if avatar.mode == "RGBA" else None)
            else:
                block_w = min(50, card_w - 20)
                block_h = min(50, avatar_area_h - 12)
                bx = (card_w - block_w) // 2
                by = 8
                _draw_rounded_rect(cd, (bx, by, bx + block_w, by + block_h), 8, rarity_color)
                font_initial = _get_font(self.font_dir, 22, bold=True)
                initial = name[0] if name else "?"
                ib = font_initial.getbbox(initial)
                iw = ib[2] - ib[0]
                ih = ib[3] - ib[1]
                cd.text((bx + (block_w - iw) // 2, by + (block_h - ih) // 2 - 2), initial, fill=(255, 255, 255), font=font_initial)

            # 星星
            star_y = avatar_area_h + 2
            star_size = 10
            total_stars_w = rarity * (star_size + 2) - 2
            stars_x = (card_w - total_stars_w) // 2
            _draw_stars_row(card, stars_x, star_y, rarity, star_size, rarity_color)

            # 名称
            font_name = _get_font(self.font_dir, 12, bold=True)
            display_name = name if len(name) <= 6 else name[:5] + ".."
            nb = font_name.getbbox(display_name)
            nw = nb[2] - nb[0]
            cd.text(((card_w - nw) // 2, star_y + star_size + 3), display_name, fill=DARK_TEXT, font=font_name)

            # 潜能
            if potential > 1:
                font_pot = _get_font(self.font_dir, 10)
                pot_text = f"潜{potential}"
                pb = font_pot.getbbox(pot_text)
                pw = pb[2] - pb[0]
                _draw_rounded_rect(cd, ((card_w - pw) // 2 - 3, star_y + star_size + 20, (card_w + pw) // 2 + 3, star_y + star_size + 33), 3, (180, 180, 180))
                cd.text(((card_w - pw) // 2, star_y + star_size + 21), pot_text, fill=(255, 255, 255), font=font_pot)

            img.paste(card, (x, y), card)

        return _to_bytes(img)

    def render_statistics(self, user_data: dict, width: int = 500) -> bytes:
        """渲染抽卡统计"""
        row_h = 32
        bar_h = 40
        num_bars = 2
        total_rows = 5
        total_h = 30 + total_rows * row_h + 15 + num_bars * bar_h + 30

        img = Image.new("RGB", (width, total_h), _hex_to_rgb(BG_COLOR))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 20, bold=True)
        font_body = _get_font(self.font_dir, 16)
        font_small = _get_font(self.font_dir, 13)

        # 标题
        draw.text((20, 15), "抽卡统计", fill=DARK_TEXT, font=font_title)
        y = 50
        draw.line([(20, y), (width - 20, y)], fill=DIVIDER_COLOR, width=1)
        y += 18

        total_pulls = user_data.get("total_pulls", 0)
        total_6stars = user_data.get("total_6stars", 0)
        pity_6 = user_data.get("pity_6", 0)
        pity_5 = user_data.get("pity_5", 0)

        # 总抽数
        draw.text((20, y), f"总抽数", fill=GRAY_TEXT, font=font_body)
        draw.text((140, y), f"{total_pulls}", fill=DARK_TEXT, font=font_body)
        y += row_h

        # 6星数量
        draw.text((20, y), f"6星干员", fill=GRAY_TEXT, font=font_body)
        draw.text((140, y), f"{total_6stars}", fill=RARITY_COLORS[6], font=font_body)
        y += row_h

        # 6星概率
        if total_pulls > 0:
            rate_6star = (total_6stars / total_pulls) * 100
            draw.text((20, y), f"6星出率", fill=GRAY_TEXT, font=font_body)
            draw.text((140, y), f"{rate_6star:.2f}%", fill=RARITY_COLORS[6], font=font_body)
        y += row_h

        # 6星保底计数
        draw.text((20, y), f"6星保底", fill=GRAY_TEXT, font=font_body)
        draw.text((140, y), f"{pity_6} / 50", fill=DARK_TEXT, font=font_body)
        y += row_h

        # 5星保底计数
        draw.text((20, y), f"5星保底", fill=GRAY_TEXT, font=font_body)
        draw.text((140, y), f"{pity_5} / 10", fill=DARK_TEXT, font=font_body)
        y += row_h + 10

        # 6星保底进度条
        draw.text((20, y), "6星保底进度", fill=GRAY_TEXT, font=font_small)
        y += 20
        bar_width = width - 40
        progress_6 = min(pity_6 / 50, 1.0)
        _draw_rounded_rect(draw, (20, y, 20 + bar_width, y + 16), 8, _hex_to_rgb("#D8D8D8"))
        if progress_6 > 0:
            pw = max(int(bar_width * progress_6), 16)
            _draw_rounded_rect(draw, (20, y, 20 + pw, y + 16), 8, RARITY_COLORS[6])
        # 百分比
        pct_text = f"{int(progress_6 * 100)}%"
        draw.text((20 + bar_width + 5, y), pct_text, fill=GRAY_TEXT, font=font_small)
        y += bar_h

        # 5星保底进度条
        draw.text((20, y), "5星保底进度", fill=GRAY_TEXT, font=font_small)
        y += 20
        progress_5 = min(pity_5 / 10, 1.0)
        _draw_rounded_rect(draw, (20, y, 20 + bar_width, y + 16), 8, _hex_to_rgb("#D8D8D8"))
        if progress_5 > 0:
            pw = max(int(bar_width * progress_5), 16)
            _draw_rounded_rect(draw, (20, y, 20 + pw, y + 16), 8, RARITY_COLORS[5])
        pct_text = f"{int(progress_5 * 100)}%"
        draw.text((20 + bar_width + 5, y), pct_text, fill=GRAY_TEXT, font=font_small)

        return _to_bytes(img)


# ========== 素材渲染器 ==========

class MaterialRenderer:
    """素材合成树渲染器"""

    def __init__(self, font_dir: str = None):
        self.font_dir = font_dir

    def _calculate_tree_size(self, tree: CraftingTree, depth: int = 0) -> tuple:
        """计算子树所需的空间"""
        node_h = 70
        gap_h = 40

        if not tree.children:
            return (220, node_h)

        child_widths = []
        child_heights = []
        for child, qty in tree.children:
            w, h = self._calculate_tree_size(child, depth + 1)
            child_widths.append(w)
            child_heights.append(h)

        child_gap = 15
        total_width = sum(child_widths) + (len(child_widths) - 1) * child_gap
        total_height = max(child_heights) + node_h + gap_h

        return (max(total_width, 220), total_height)

    def _draw_tree_node(self, img: Image.Image, draw: ImageDraw.Draw,
                        tree: CraftingTree, x: int, y: int, width: int, depth: int = 0):
        """递归绘制合成树节点"""
        font_name = _get_font(self.font_dir, 15, bold=True)
        font_qty = _get_font(self.font_dir, 12)
        font_type = _get_font(self.font_dir, 11)

        node_h = 60

        # 节点颜色 (根据稀有度)
        rarity_color_hex = CRAFT_RARITY_COLORS.get(tree.rarity, "#808080")
        rarity_color = _hex_to_rgb(rarity_color_hex)

        # 绘制节点背景 (圆角矩形)
        _draw_rounded_rect(draw, (x, y, x + width, y + node_h), 10, rarity_color)

        # 名称
        draw.text((x + 12, y + 10), tree.name, fill=(255, 255, 255), font=font_name)

        # 数量
        if tree.quantity > 1:
            draw.text((x + 12, y + 35), f"x{tree.quantity}", fill=(230, 230, 230), font=font_qty)

        # 合成类型
        if tree.craft_type:
            craft_text = "工作台" if tree.craft_type == "WORKBENCH" else "制造站"
            cb = font_type.getbbox(craft_text)
            cw = cb[2] - cb[0]
            draw.text((x + width - cw - 12, y + 35), craft_text, fill=(200, 200, 200), font=font_type)

        if tree.children:
            child_y = y + node_h + 30
            child_gap = 15

            # 计算子节点总宽度
            child_sizes = []
            for child, qty in tree.children:
                cw, ch = self._calculate_tree_size(child, depth + 1)
                child_sizes.append((cw, ch))

            total_child_w = sum(s[0] for s in child_sizes) + (len(child_sizes) - 1) * child_gap
            child_x = x + (width - total_child_w) // 2

            # 绘制连接线
            parent_cx = x + width // 2
            parent_cy = y + node_h
            for i, (child, qty) in enumerate(tree.children):
                cw, ch = child_sizes[i]
                child_cx = child_x + cw // 2

                # 垂直线 + 水平线 + 垂直线
                mid_y = parent_cy + 15
                draw.line([(parent_cx, parent_cy), (parent_cx, mid_y)], fill=(160, 160, 160), width=2)
                draw.line([(parent_cx, mid_y), (child_cx, mid_y)], fill=(160, 160, 160), width=2)
                draw.line([(child_cx, mid_y), (child_cx, child_y)], fill=(160, 160, 160), width=2)

                # 数量标注
                if qty > 1:
                    qty_text = f"x{qty}"
                    qb = font_qty.getbbox(qty_text)
                    qw = qb[2] - qb[0]
                    draw.text((child_cx - qw // 2, child_y - 15), qty_text, fill=GRAY_TEXT, font=font_qty)

                # 递归绘制子节点
                self._draw_tree_node(img, draw, child, child_x, child_y, cw, depth + 1)
                child_x += cw + child_gap

    def render_crafting_tree(self, tree: CraftingTree, width: int = 800, height: int = 600) -> bytes:
        """渲染合成树"""
        # 动态计算所需高度
        tree_w, tree_h = self._calculate_tree_size(tree)
        header_h = 65
        footer_h = 30

        # 如果有推荐关卡，预留空间
        stage_count = len(tree.stage_drops) if tree.stage_drops else 0
        if stage_count > 0:
            footer_h = 40 + stage_count * 22 + 20

        # 计算实际高度
        actual_h = max(height, header_h + tree_h + footer_h + 20)
        actual_w = max(width, tree_w + 60)

        img = Image.new("RGB", (actual_w, actual_h), _hex_to_rgb(BG_COLOR))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 20, bold=True)
        font_body = _get_font(self.font_dir, 13)

        # 标题
        draw.text((20, 15), f"合成路线: {tree.name}", fill=DARK_TEXT, font=font_title)
        draw.line([(20, 48), (actual_w - 20, 48)], fill=DIVIDER_COLOR, width=1)

        # 绘制合成树
        tree_x = (actual_w - tree_w) // 2
        self._draw_tree_node(img, draw, tree, tree_x, header_h, tree_w)

        # 推荐关卡
        if tree.stage_drops:
            drop_y = header_h + tree_h + 20
            draw.line([(20, drop_y), (actual_w - 20, drop_y)], fill=DIVIDER_COLOR, width=1)
            drop_y += 10

            draw.text((20, drop_y), "推荐关卡:", fill=GRAY_TEXT, font=font_body)
            drop_y += 22

            font_drop = _get_font(self.font_dir, 12)
            for drop in tree.stage_drops[:5]:
                stage_name = drop["stage_name"]
                drop_type = drop["drop_type"]
                drop_rate = drop["drop_rate"]
                sp_cost = drop["sp_cost"]

                type_text = {"NORMAL": "普通", "SPECIAL": "特殊", "EXTRA": "额外"}.get(drop_type, drop_type)
                rate_text = f"{drop_rate * 100:.1f}%" if drop_rate > 0 else "未知"

                text = f"{stage_name}  |  {type_text}  |  掉率 {rate_text}  |  {sp_cost}理智"
                draw.text((30, drop_y), text, fill=GRAY_TEXT, font=font_drop)
                drop_y += 22

        return _to_bytes(img)

    def render_stage_drops(self, item_name: str, drops: list, width: int = 600) -> bytes:
        """渲染素材掉落关卡"""
        line_height = 32
        header_height = 65
        total_h = header_height + len(drops) * line_height + 20

        img = Image.new("RGB", (width, total_h), _hex_to_rgb(BG_COLOR))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 20, bold=True)
        font_body = _get_font(self.font_dir, 13)
        font_header = _get_font(self.font_dir, 12, bold=True)

        draw.text((20, 15), f"掉落查询: {item_name}", fill=DARK_TEXT, font=font_title)
        draw.line([(20, 48), (width - 20, 48)], fill=DIVIDER_COLOR, width=1)

        # 表头
        y = 55
        draw.text((20, y), "关卡", fill=GRAY_TEXT, font=font_header)
        draw.text((140, y), "类型", fill=GRAY_TEXT, font=font_header)
        draw.text((240, y), "掉率", fill=GRAY_TEXT, font=font_header)
        draw.text((360, y), "理智", fill=GRAY_TEXT, font=font_header)
        y += 25

        for idx, drop in enumerate(drops[:20]):
            stage_name = drop["stage_name"]
            drop_type = drop["drop_type"]
            drop_rate = drop["drop_rate"]
            sp_cost = drop["sp_cost"]

            type_text = {"NORMAL": "普通", "SPECIAL": "特殊", "EXTRA": "额外"}.get(drop_type, drop_type)
            rate_text = f"{drop_rate * 100:.1f}%" if drop_rate > 0 else "未知"

            # 交替背景色
            if idx % 2 == 0:
                _draw_rounded_rect(draw, (15, y - 2, width - 15, y + line_height - 4), 4, _hex_to_rgb("#E5E8ED"))

            draw.text((20, y + 4), stage_name, fill=DARK_TEXT, font=font_body)
            draw.text((140, y + 4), type_text, fill=GRAY_TEXT, font=font_body)
            draw.text((240, y + 4), rate_text, fill=(50, 160, 80), font=font_body)
            draw.text((360, y + 4), f"{sp_cost}", fill=(180, 130, 60), font=font_body)

            y += line_height

        return _to_bytes(img)
