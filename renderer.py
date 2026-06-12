"""
抽卡结果 & 素材合成树图片渲染器
"""
import io
import os
from PIL import Image, ImageDraw, ImageFont
from .engine import GachaResult
from .crafting import CraftingTree, RARITY_COLORS as CRAFT_RARITY_COLORS


# 星级颜色 (RGB)
RARITY_COLORS = {
    4: (141, 88, 168),   # 紫
    5: (233, 178, 60),   # 金
    6: (239, 79, 67),    # 红
}


# ========== 共享工具函数 ==========

_FONT_CACHE = {}


def _get_font(font_dir, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """获取字体"""
    key = (font_dir, size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    font = None
    if font_dir:
        font_paths = [
            os.path.join(font_dir, "HarmonyOS_Sans_SC_Bold.ttf" if bold else "HarmonyOS_Sans_SC_Regular.ttf"),
            os.path.join(font_dir, "NotoSansSC-Regular.ttf"),
            os.path.join(font_dir, "HarmonyOS_Sans_SC.ttf"),
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, size)
                    break
                except Exception:
                    pass

    if font is None:
        system_fonts = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/noto-cjk/NotoSansSC-Regular.otf",
        ]
        for sf in system_fonts:
            if os.path.exists(sf):
                try:
                    font = ImageFont.truetype(sf, size)
                    break
                except Exception:
                    pass

    if font is None:
        font = ImageFont.load_default()

    _FONT_CACHE[key] = font
    return font


def _has_cjk_support(font) -> bool:
    """检查字体是否支持中文字符"""
    try:
        bbox = font.getbbox("测")
        return bbox is not None and (bbox[2] - bbox[0]) > 0
    except Exception:
        return False


def _render_stars(rarity: int, font) -> str:
    """渲染星级，如果字体不支持 ★ 则回退到 ASCII"""
    star_char = "★"
    try:
        bbox = font.getbbox(star_char)
        if bbox is None or (bbox[2] - bbox[0]) <= 0:
            star_char = "*"
    except Exception:
        star_char = "*"
    return star_char * rarity


def _draw_rounded_rect(draw: ImageDraw.Draw, xy: tuple, radius: int, fill: tuple):
    """绘制圆角矩形"""
    x1, y1, x2, y2 = xy
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    draw.pieslice([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=fill)
    draw.pieslice([x2 - 2*radius, y1, x2, y1 + 2*radius], 270, 360, fill=fill)
    draw.pieslice([x1, y2 - 2*radius, x1 + 2*radius, y2], 90, 180, fill=fill)
    draw.pieslice([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=fill)


# ========== 抽卡渲染器 ==========

class GachaRenderer:
    """抽卡结果渲染器"""

    def __init__(self, font_dir: str = None):
        self.font_dir = font_dir

    def render_single_pull(self, result: GachaResult, width: int = 600, height: int = 300) -> bytes:
        """渲染单抽结果"""
        img = Image.new('RGB', (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        font_name = _get_font(self.font_dir, 36, bold=True)
        font_rarity = _get_font(self.font_dir, 24)
        font_label = _get_font(self.font_dir, 18)

        # 顶部色条
        rarity_color = RARITY_COLORS.get(result.rarity, (128, 128, 128))
        draw.rectangle([0, 0, width, 8], fill=rarity_color)

        # 干员名称
        name_text = result.name
        if result.is_new:
            name_text += " [新]"

        bbox = font_name.getbbox(name_text)
        name_width = bbox[2] - bbox[0]
        x = (width - name_width) // 2
        draw.text((x, 80), name_text, fill=rarity_color, font=font_name)

        # 星级
        stars = _render_stars(result.rarity, font_rarity)
        bbox = font_rarity.getbbox(stars)
        stars_width = bbox[2] - bbox[0]
        x = (width - stars_width) // 2
        draw.text((x, 140), stars, fill=rarity_color, font=font_rarity)

        # 稀有度标签
        rarity_text = f"{result.rarity}星"
        bbox = font_label.getbbox(rarity_text)
        label_width = bbox[2] - bbox[0] + 20
        label_x = (width - label_width) // 2
        _draw_rounded_rect(draw, (label_x, 180, label_x + label_width, 210), 5, (60, 60, 70))
        draw.text((label_x + 10, 183), rarity_text, fill=(200, 200, 200), font=font_label)

        # 新干员提示
        if result.is_new:
            new_text = "NEW!"
            bbox = font_label.getbbox(new_text)
            new_width = bbox[2] - bbox[0] + 20
            new_x = (width - new_width) // 2
            _draw_rounded_rect(draw, (new_x, 220, new_x + new_width, 250), 5, (239, 79, 67))
            draw.text((new_x + 10, 223), new_text, fill=(255, 255, 255), font=font_label)

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer.getvalue()

    def render_ten_pull(self, results: list[GachaResult], width: int = 800, height: int = 600) -> bytes:
        """渲染十连结果"""
        img = Image.new('RGB', (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 28, bold=True)
        font_name = _get_font(self.font_dir, 20, bold=True)
        font_stars = _get_font(self.font_dir, 16)

        # 标题
        draw.text((20, 15), "十连结果", fill=(255, 255, 255), font=font_title)
        draw.line([(20, 55), (width - 20, 55)], fill=(60, 60, 70), width=1)

        # 统计
        rarity_counts = {6: 0, 5: 0, 4: 0}
        for r in results:
            if r.rarity in rarity_counts:
                rarity_counts[r.rarity] += 1

        star_char = "★" if _has_cjk_support(font_name) else "*"
        stats_text = f"6{star_char}: {rarity_counts[6]}  5{star_char}: {rarity_counts[5]}  4{star_char}: {rarity_counts[4]}"
        draw.text((20, 65), stats_text, fill=(180, 180, 180), font=font_name)

        # 绘制结果网格 (2列 x 5行)
        card_width = (width - 60) // 2
        card_height = 90
        start_x = 20
        start_y = 110

        for i, result in enumerate(results):
            col = i % 2
            row = i // 2

            x = start_x + col * (card_width + 20)
            y = start_y + row * (card_height + 10)

            rarity_color = RARITY_COLORS.get(result.rarity, (128, 128, 128))
            _draw_rounded_rect(draw, (x, y, x + card_width, y + card_height), 8, (50, 50, 55))

            draw.rectangle([x, y, x + 6, y + card_height], fill=rarity_color)

            name_text = result.name
            if result.is_new:
                name_text += " [新]"
            draw.text((x + 15, y + 10), name_text, fill=(255, 255, 255), font=font_name)

            stars = _render_stars(result.rarity, font_stars)
            draw.text((x + 15, y + 45), stars, fill=rarity_color, font=font_stars)

            rarity_text = f"{result.rarity}星"
            draw.text((x + card_width - 60, y + 10), rarity_text, fill=(150, 150, 150), font=font_stars)

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer.getvalue()

    def render_inventory(self, user_data: dict, operators: list[dict],
                        width: int = 600, height: int = None) -> bytes:
        """渲染背包/仓库"""
        line_height = 30
        header_height = 100
        calculated_height = header_height + len(operators) * line_height + 40
        if height is None:
            height = min(calculated_height, 800)

        img = Image.new('RGB', (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 24, bold=True)
        font_body = _get_font(self.font_dir, 16)
        font_small = _get_font(self.font_dir, 14)

        draw.text((20, 15), "背包", fill=(255, 255, 255), font=font_title)
        draw.line([(20, 50), (width - 20, 50)], fill=(60, 60, 70), width=1)

        orundum = user_data.get("orundum", 0)
        permits = user_data.get("permits", 0)
        ten_permits = user_data.get("ten_permits", 0)
        yellow_tickets = user_data.get("yellow_tickets", 0)
        green_tickets = user_data.get("green_tickets", 0)

        y = 60
        draw.text((20, y), f"合成玉: {orundum}", fill=(100, 200, 255), font=font_body)
        y += 25
        draw.text((20, y), f"单人凭证: {permits}  十连凭证: {ten_permits}", fill=(180, 180, 180), font=font_small)
        y += 25
        draw.text((20, y), f"黄票: {yellow_tickets}  绿票: {green_tickets}", fill=(180, 180, 180), font=font_small)
        y += 35

        draw.text((20, y), f"干员 ({len(operators)}名):", fill=(200, 200, 200), font=font_body)
        y += 30

        for op in operators[:20]:
            name = op.get("name", "")
            rarity = op.get("rarity", 1)
            potential = op.get("potential", 1)

            rarity_color = RARITY_COLORS.get(rarity, (128, 128, 128))
            stars = _render_stars(rarity, font_small)

            draw.text((20, y), name, fill=(255, 255, 255), font=font_body)
            draw.text((200, y), stars, fill=rarity_color, font=font_small)
            draw.text((300, y), f"潜能{potential}", fill=(150, 150, 150), font=font_small)

            y += line_height
            if y > height - 20:
                break

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer.getvalue()

    def render_statistics(self, user_data: dict, width: int = 600, height: int = 400) -> bytes:
        """渲染抽卡统计"""
        img = Image.new('RGB', (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 24, bold=True)
        font_body = _get_font(self.font_dir, 18)
        font_small = _get_font(self.font_dir, 14)

        draw.text((20, 15), "抽卡统计", fill=(255, 255, 255), font=font_title)
        draw.line([(20, 50), (width - 20, 50)], fill=(60, 60, 70), width=1)

        total_pulls = user_data.get("total_pulls", 0)
        total_6stars = user_data.get("total_6stars", 0)
        pity_6 = user_data.get("pity_6", 0)
        pity_5 = user_data.get("pity_5", 0)

        y = 70
        draw.text((20, y), f"总抽数: {total_pulls}", fill=(255, 255, 255), font=font_body)
        y += 35

        draw.text((20, y), f"6星数量: {total_6stars}", fill=(239, 79, 67), font=font_body)
        y += 30

        if total_pulls > 0:
            rate_6star = (total_6stars / total_pulls) * 100
            draw.text((20, y), f"6星概率: {rate_6star:.2f}%", fill=(239, 79, 67), font=font_body)
        y += 35

        draw.text((20, y), f"当前6星保底: {pity_6}/{50}", fill=(180, 180, 180), font=font_body)
        y += 30

        draw.text((20, y), f"当前5星保底: {pity_5}/{10}", fill=(180, 180, 180), font=font_body)
        y += 40

        bar_width = width - 60
        bar_height = 20

        draw.text((20, y), "6星保底进度:", fill=(200, 200, 200), font=font_small)
        y += 25
        progress_6 = min(pity_6 / 50, 1.0)
        draw.rectangle([20, y, 20 + bar_width, y + bar_height], fill=(60, 60, 70))
        draw.rectangle([20, y, 20 + int(bar_width * progress_6), y + bar_height], fill=(239, 79, 67))
        y += 30

        draw.text((20, y), "5星保底进度:", fill=(200, 200, 200), font=font_small)
        y += 25
        progress_5 = min(pity_5 / 10, 1.0)
        draw.rectangle([20, y, 20 + bar_width, y + bar_height], fill=(60, 60, 70))
        draw.rectangle([20, y, 20 + int(bar_width * progress_5), y + bar_height], fill=(233, 178, 60))

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer.getvalue()


# ========== 素材渲染器 ==========

class MaterialRenderer:
    """素材合成树渲染器"""

    def __init__(self, font_dir: str = None):
        self.font_dir = font_dir

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple:
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _calculate_tree_size(self, tree: CraftingTree, depth: int = 0) -> tuple:
        if not tree.children:
            return (200, 80)

        child_widths = []
        child_heights = []
        for child, qty in tree.children:
            w, h = self._calculate_tree_size(child, depth + 1)
            child_widths.append(w)
            child_heights.append(h)

        total_width = sum(child_widths) + (len(child_widths) - 1) * 20
        total_height = max(child_heights) + 100

        return (max(total_width, 200), total_height)

    def _draw_tree_node(self, draw: ImageDraw.Draw, tree: CraftingTree,
                       x: int, y: int, width: int, depth: int = 0):
        font_name = _get_font(self.font_dir, 16, bold=True)
        font_qty = _get_font(self.font_dir, 14)

        rarity_color = self._hex_to_rgb(CRAFT_RARITY_COLORS.get(tree.rarity, "#808080"))
        _draw_rounded_rect(draw, (x, y, x + width, y + 60), 8, rarity_color)

        draw.text((x + 10, y + 10), tree.name, fill=(255, 255, 255), font=font_name)

        if tree.quantity > 1:
            draw.text((x + 10, y + 35), f"x{tree.quantity}", fill=(200, 200, 200), font=font_qty)

        if tree.craft_type:
            craft_text = "工作台" if tree.craft_type == "WORKBENCH" else "制造站"
            draw.text((x + width - 60, y + 35), craft_text, fill=(150, 150, 150), font=font_qty)

        if tree.children:
            child_y = y + 80
            child_x = x
            for child, qty in tree.children:
                child_w, child_h = self._calculate_tree_size(child, depth + 1)

                draw.line(
                    [(x + width // 2, y + 60), (child_x + child_w // 2, child_y)],
                    fill=(100, 100, 100), width=2
                )

                self._draw_tree_node(draw, child, child_x, child_y, child_w, depth + 1)

                child_x += child_w + 20

    def render_crafting_tree(self, tree: CraftingTree, width: int = 800, height: int = 600) -> bytes:
        img = Image.new('RGB', (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 24, bold=True)
        font_body = _get_font(self.font_dir, 14)

        draw.text((20, 15), f"合成路线: {tree.name}", fill=(255, 255, 255), font=font_title)
        draw.line([(20, 50), (width - 20, 50)], fill=(60, 60, 70), width=1)

        tree_width = width - 40
        self._draw_tree_node(draw, tree, 20, 70, tree_width)

        if tree.stage_drops:
            drop_y = height - 150
            draw.line([(20, drop_y), (width - 20, drop_y)], fill=(60, 60, 70), width=1)
            drop_y += 10

            draw.text((20, drop_y), "推荐关卡:", fill=(200, 200, 200), font=font_body)
            drop_y += 25

            for drop in tree.stage_drops[:5]:
                stage_name = drop["stage_name"]
                drop_type = drop["drop_type"]
                drop_rate = drop["drop_rate"]
                sp_cost = drop["sp_cost"]

                type_text = {"NORMAL": "普通", "SPECIAL": "特殊", "EXTRA": "额外"}.get(drop_type, drop_type)
                rate_text = f"{drop_rate * 100:.1f}%" if drop_rate > 0 else "未知"

                text = f"{stage_name} | {type_text} | {rate_text} | {sp_cost}理智"
                draw.text((30, drop_y), text, fill=(180, 180, 180), font=font_body)
                drop_y += 20

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer.getvalue()

    def render_stage_drops(self, item_name: str, drops: list[dict],
                          width: int = 600, height: int = None) -> bytes:
        line_height = 30
        header_height = 60
        calculated_height = header_height + len(drops) * line_height + 40
        if height is None:
            height = min(calculated_height, 800)

        img = Image.new('RGB', (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        font_title = _get_font(self.font_dir, 24, bold=True)
        font_body = _get_font(self.font_dir, 14)

        draw.text((20, 15), f"掉落关卡: {item_name}", fill=(255, 255, 255), font=font_title)
        draw.line([(20, 50), (width - 20, 50)], fill=(60, 60, 70), width=1)

        y = 65
        for drop in drops[:20]:
            stage_name = drop["stage_name"]
            drop_type = drop["drop_type"]
            drop_rate = drop["drop_rate"]
            sp_cost = drop["sp_cost"]

            type_text = {"NORMAL": "普通", "SPECIAL": "特殊", "EXTRA": "额外"}.get(drop_type, drop_type)
            rate_text = f"{drop_rate * 100:.1f}%" if drop_rate > 0 else "未知"

            draw.text((20, y), stage_name, fill=(255, 255, 255), font=font_body)
            draw.text((200, y), type_text, fill=(180, 180, 180), font=font_body)
            draw.text((300, y), rate_text, fill=(100, 200, 100), font=font_body)
            draw.text((400, y), f"{sp_cost}理智", fill=(200, 150, 100), font=font_body)

            y += line_height
            if y > height - 20:
                break

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer.getvalue()
