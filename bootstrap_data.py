"""
数据引导脚本 - 在 NAS 上运行一次，下载干员数据到本地

使用方法:
    python bootstrap_data.py

会在 resource/ 目录生成 operators.json（约 50KB）
"""
import json
import os
import sys
import urllib.request
import ssl

# 支持直接运行和作为模块导入
try:
    from .constants import RARITY_MAP
except ImportError:
    from constants import RARITY_MAP

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = os.path.join(SCRIPT_DIR, "resource")
OUTPUT_FILE = os.path.join(RESOURCE_DIR, "operators.json")

# 下载源列表（按优先级）
SOURCES = [
    # GitHub 原始地址
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json",
    # GitHub 镜像代理
    "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json",
    "https://ghfast.top/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json",
    "https://gh-proxy.com/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json",
    # Gitee
    "https://gitee.com/Kengxxiao/ArknightsGameData/raw/master/zh_CN/gamedata/excel/character_table.json",
]


def download_json(url: str, timeout: int = 120) -> dict | None:
    """下载并解析 JSON"""
    ctx = ssl.create_default_context()
    try:
        print(f"  尝试: {url[:80]}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        data = resp.read()
        if len(data) < 100000:
            print(f"  文件太小 ({len(data)} bytes)，跳过")
            return None
        return json.loads(data)
    except Exception as e:
        print(f"  失败: {e}")
        return None


def extract_operators(data: dict) -> dict:
    """从 character_table 提取干员数据"""
    operators = {}
    for char_id, char in data.items():
        if not char_id.startswith("char_"):
            continue
        # 跳过皮肤和特殊 ID
        if "#" in char_id or char_id.endswith("_skin"):
            continue

        name = char.get("name", "")
        rarity_str = char.get("rarity", "TIER_1")
        rarity = RARITY_MAP.get(rarity_str, 1)

        # 只保留 3-6 星干员
        if rarity >= 3:
            obtain_approach = char.get("itemObtainApproach", "")
            is_not_obtainable = char.get("isNotObtainable", False)
            is_limited = 1 if (obtain_approach != "招募寻访" or is_not_obtainable) else 0
            operators[char_id] = {"name": name, "rarity": rarity, "is_limited": is_limited}

    return operators


def main():
    print("=" * 50)
    print("明日方舟工具箱 - 干员数据引导")
    print("=" * 50)

    # 检查是否已存在
    if os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 1000:
        print(f"\noperators.json 已存在 ({os.path.getsize(OUTPUT_FILE)} bytes)")
        resp = input("是否重新下载? (y/N): ").strip().lower()
        if resp != "y":
            print("跳过下载")
            return

    os.makedirs(RESOURCE_DIR, exist_ok=True)

    print(f"\n正在下载 character_table.json...")
    data = None
    for url in SOURCES:
        data = download_json(url)
        if data:
            print(f"  下载成功!")
            break

    if not data:
        print("\n所有下载源均失败。请检查网络连接后重试。")
        sys.exit(1)

    print(f"\n正在提取干员数据...")
    operators = extract_operators(data)
    print(f"提取到 {len(operators)} 名干员")

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(operators, f, ensure_ascii=False, indent=2)

    size = os.path.getsize(OUTPUT_FILE)
    print(f"\n已保存到: {OUTPUT_FILE}")
    print(f"文件大小: {size} bytes ({size/1024:.1f} KB)")
    print("\n完成! 插件现在可以使用本地干员数据了。")


if __name__ == "__main__":
    main()
