"""测试游戏素材渲染器"""
import sys
import os
import types
import importlib.util

current_dir = os.path.dirname(os.path.abspath(__file__))

# 创建伪包模块，解决相对导入问题
pkg_name = "astrbot_plugin_arknights_gacha"
pkg = types.ModuleType(pkg_name)
pkg.__path__ = [current_dir]
sys.modules[pkg_name] = pkg

# 先加载 constants（解决 crafting 的依赖）
def load_module(mod_name, filepath):
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

constants = load_module(f"{pkg_name}.constants", os.path.join(current_dir, "constants.py"))
engine = load_module(f"{pkg_name}.engine", os.path.join(current_dir, "engine.py"))
crafting = load_module(f"{pkg_name}.crafting", os.path.join(current_dir, "crafting.py"))
renderer_mod = load_module(f"{pkg_name}.renderer", os.path.join(current_dir, "renderer.py"))

GachaResult = engine.GachaResult
GachaAssetLoader = renderer_mod.GachaAssetLoader
AssetGachaRenderer = renderer_mod.AssetGachaRenderer
GachaRenderer = renderer_mod.GachaRenderer


def test_asset_loader():
    """测试素材加载器"""
    assets_dir = os.path.join(current_dir, 'resource', 'gacha_assets')
    if not os.path.isdir(assets_dir):
        print("素材目录不存在，跳过测试")
        return

    loader = GachaAssetLoader(assets_dir)
    print(f"十连立绘数量: {len(loader._ten_pull_assets)}")
    print(f"单抽立绘数量: {len(loader._single_pull_assets)}")
    print(f"Rarity 模板数量: {len(loader._rarity_templates)}")

    # 测试几个干员
    test_cases = [
        ("阿米娅", 5),
        ("凯尔希", 6),
        ("陈", 6),
        ("桃金娘", 4),
    ]
    for name, rarity in test_cases:
        has_ten = loader.has_asset(name, rarity, "ten_pull")
        has_single = loader.has_asset(name, rarity, "single_pull")
        print(f"  {name} ({rarity}星): 十连={has_ten}, 单抽={has_single}")


def test_renderers():
    """测试渲染器"""
    font_dir = os.path.join(current_dir, 'resource', 'fonts')
    resource_dir = os.path.join(current_dir, 'resource')
    assets_dir = os.path.join(resource_dir, 'gacha_assets')

    base_renderer = GachaRenderer(font_dir, resource_dir)

    if os.path.isdir(assets_dir):
        asset_loader = GachaAssetLoader(assets_dir)
        renderer = AssetGachaRenderer(asset_loader, base_renderer)
    else:
        renderer = base_renderer

    # 测试单抽（有素材）
    print("\n测试单抽（阿米娅，有素材）...")
    result = GachaResult('char_001_amiya', '阿米娅', 5, True)
    img_bytes = renderer.render_single_pull(result)
    out_path = os.path.join(current_dir, 'test_single_amiya.png')
    with open(out_path, 'wb') as f:
        f.write(img_bytes)
    print(f"  已保存: {out_path} ({len(img_bytes)} bytes)")

    # 测试单抽（无素材，fallback）
    print("\n测试单抽（慕斯，无素材 fallback）...")
    result = GachaResult('char_010_mousse', '慕斯', 4, False)
    img_bytes = renderer.render_single_pull(result)
    out_path = os.path.join(current_dir, 'test_single_mousse.png')
    with open(out_path, 'wb') as f:
        f.write(img_bytes)
    print(f"  已保存: {out_path} ({len(img_bytes)} bytes)")

    # 测试十连（全有素材）
    print("\n测试十连（全有素材）...")
    results = [
        GachaResult('char_001', '阿米娅', 5, True),
        GachaResult('char_002', '德克萨斯', 5, False),
        GachaResult('char_003', '拉普兰德', 5, False),
        GachaResult('char_004', '桃金娘', 4, True),
        GachaResult('char_005', '讯使', 4, False),
        GachaResult('char_006', '杰西卡', 4, False),
        GachaResult('char_007', '流星', 4, False),
        GachaResult('char_008', '格雷伊', 4, False),
        GachaResult('char_009', '霜叶', 4, False),
        GachaResult('char_010', '远山', 4, False),
    ]
    img_bytes = renderer.render_ten_pull(results)
    out_path = os.path.join(current_dir, 'test_ten_all_assets.png')
    with open(out_path, 'wb') as f:
        f.write(img_bytes)
    print(f"  已保存: {out_path} ({len(img_bytes)} bytes)")

    # 测试十连（部分缺失，fallback）
    print("\n测试十连（部分缺失，fallback）...")
    results = [
        GachaResult('char_001', '阿米娅', 5, True),
        GachaResult('char_002', '慕斯', 4, False),  # 无素材
        GachaResult('char_003', '拉普兰德', 5, False),
        GachaResult('char_004', '桃金娘', 4, True),
        GachaResult('char_005', '讯使', 4, False),
        GachaResult('char_006', '杰西卡', 4, False),
        GachaResult('char_007', '流星', 4, False),
        GachaResult('char_008', '格雷伊', 4, False),
        GachaResult('char_009', '霜叶', 4, False),
        GachaResult('char_010', '远山', 4, False),
    ]
    img_bytes = renderer.render_ten_pull(results)
    out_path = os.path.join(current_dir, 'test_ten_fallback.png')
    with open(out_path, 'wb') as f:
        f.write(img_bytes)
    print(f"  已保存: {out_path} ({len(img_bytes)} bytes)")

    print("\n测试完成!")


if __name__ == '__main__':
    test_asset_loader()
    test_renderers()
