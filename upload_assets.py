"""
一键创建 GitHub Release 并上传素材包
用法: python upload_assets.py <GITHUB_TOKEN>

Token 获取: GitHub -> Settings -> Developer settings -> Personal access tokens -> Generate new token
需要的权限: repo (完整仓库访问)

也可通过环境变量设置: set GH_TOKEN=ghp_xxxxx
"""
import json
import os
import sys
import urllib.request
import urllib.error
import zipfile
import io

REPO = "zhuruce123123-collab/arknights_gacha"
TAG = "assets-v1.0.0"
RELEASE_NAME = "游戏素材包 v1.0.0"
RELEASE_BODY = "明日方舟抽卡素材包，包含：\n- rarity/ — 6张 1280x720 十连模板\n- chars_r2/ — 十连干员肖像 (122x580)\n- chars/ — 单抽干员立绘 (1280x720)"
ASSET_FILENAME = "gacha_assets.zip"

# 镜像代理（用于国内环境）
API_PROXIES = [
    "",  # 直连
    "https://edgeone.gh-proxy.com/",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
]


def get_token():
    """获取 GitHub Token"""
    if len(sys.argv) > 1:
        return sys.argv[1]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    print("错误: 请提供 GitHub Token")
    print("用法: python upload_assets.py <TOKEN>")
    print("或设置环境变量: set GH_TOKEN=ghp_xxxxx")
    sys.exit(1)


def find_assets_zip():
    """查找 gacha_assets.zip 文件"""
    # 搜索常见位置
    search_paths = [
        os.path.join(os.path.dirname(__file__), "resource", "gacha_assets.zip"),
        os.path.join(os.path.dirname(__file__), "gacha_assets.zip"),
        os.path.join(os.path.expanduser("~"), "Downloads", "gacha_assets.zip"),
        os.path.join(os.path.expanduser("~"), "Desktop", "gacha_assets.zip"),
    ]

    # 也搜索 NAS 常见挂载路径
    for drive in ["G:", "H:", "I:", "Z:"]:
        search_paths.append(os.path.join(drive, "/", "gacha_assets.zip"))

    for path in search_paths:
        if os.path.exists(path):
            return path

    # 尝试从 resource/gacha_assets/ 目录打包
    assets_dir = os.path.join(os.path.dirname(__file__), "resource", "gacha_assets")
    if os.path.isdir(assets_dir):
        print(f"未找到 {ASSET_FILENAME}，但发现素材目录: {assets_dir}")
        print("正在打包...")
        zip_path = os.path.join(os.path.dirname(__file__), "resource", ASSET_FILENAME)
        create_zip(assets_dir, zip_path)
        return zip_path

    return None


def create_zip(source_dir, zip_path):
    """将目录打包为 zip"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(source_dir))
                zf.write(file_path, arcname)
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"打包完成: {zip_path} ({size_mb:.1f}MB)")


def api_request(url, token, data=None, method="POST"):
    """发送 GitHub API 请求"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "arknights-gacha-release-bot",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_release(token, proxy=""):
    """创建 GitHub Release"""
    url = proxy + f"https://api.github.com/repos/{REPO}/releases"
    data = {
        "tag_name": TAG,
        "name": RELEASE_NAME,
        "body": RELEASE_BODY,
        "draft": False,
        "prerelease": False,
    }
    print(f"正在创建 Release ({proxy or '直连'})...")
    return api_request(url, token, data)


def upload_asset(upload_url, token, file_path, proxy=""):
    """上传素材到 Release"""
    # upload_url 格式: https://uploads.github.com/repos/.../assets{?name}
    # 需要替换为实际文件名
    base_url = upload_url.split("{")[0]
    url = proxy + f"{base_url}?name={ASSET_FILENAME}"

    file_size = os.path.getsize(file_path)
    size_mb = file_size / (1024 * 1024)
    print(f"正在上传 {ASSET_FILENAME} ({size_mb:.1f}MB)...")

    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/zip",
        "User-Agent": "arknights-gacha-release-bot",
    }

    with open(file_path, "rb") as f:
        req = urllib.request.Request(url, data=f.read(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8"))


def main():
    token = get_token()
    print(f"Token: {token[:8]}...{token[-4:]}")

    # 1. 创建 Release
    release = None
    for proxy in API_PROXIES:
        try:
            release = create_release(token, proxy)
            print(f"Release 创建成功！ID: {release['id']}")
            print(f"URL: {release['html_url']}")
            break
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            # 如果 Release 已存在，获取它
            if e.status == 422 and "already_exists" in str(body):
                print("Release 已存在，查找现有 Release...")
                url = proxy + f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
                req = urllib.request.Request(url, headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    release = json.loads(resp.read().decode("utf-8"))
                print(f"找到现有 Release: {release['html_url']}")
                break
            else:
                print(f"  失败 ({proxy or '直连'}): HTTP {e.status} - {body.get('message', '')}")
                continue
        except Exception as e:
            print(f"  失败 ({proxy or '直连'}): {e}")
            continue

    if not release:
        print("\n所有代理均失败。请检查:")
        print("1. Token 是否有效（需要 repo 权限）")
        print("2. 网络连接是否正常")
        sys.exit(1)

    # 2. 上传素材（如果有）
    zip_path = find_assets_zip()
    if zip_path:
        upload_url = release["upload_url"]
        for proxy in API_PROXIES:
            try:
                result = upload_asset(upload_url, token, zip_path, proxy)
                print(f"上传成功！下载链接: {result['browser_download_url']}")
                break
            except Exception as e:
                print(f"  上传失败 ({proxy or '直连'}): {e}")
                continue
        else:
            print("\n素材上传失败，请手动上传:")
            print(f"  1. 打开 {release['html_url']}")
            print(f"  2. 点击 'Edit' 上传 {zip_path}")
    else:
        print(f"\n未找到 {ASSET_FILENAME}，请手动上传:")
        print(f"  1. 打开 {release['html_url']}")
        print(f"  2. 点击 'Edit' 上传素材包")
        print(f"\n素材包打包方法:")
        print(f"  将 resource/gacha_assets/ 目录压缩为 gacha_assets.zip")

    print(f"\n完成！Release 地址: {release['html_url']}")


if __name__ == "__main__":
    main()
