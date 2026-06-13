"""
游戏素材运行时下载器
从 GitHub Release 下载 gacha_assets.zip 并解压到 resource/gacha_assets/
支持国内镜像代理回退
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import zipfile

logger = logging.getLogger(__name__)

# 素材包版本（与 GitHub Release tag 对应）
ASSET_VERSION = "v1.0.0"
ASSET_ZIP_NAME = "gacha_assets.zip"

# GitHub Release 下载地址
GITHUB_RELEASE_BASE = (
    "https://github.com/zhuruce123123-collab/arknights_gacha/"
    f"releases/download/assets-{ASSET_VERSION}/{ASSET_ZIP_NAME}"
)

# 镜像代理
MIRROR_PROXIES = [
    "https://edgeone.gh-proxy.com/",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
]

# 版本标记文件名
VERSION_MARKER = ".asset_version"


class GachaAssetDownloader:
    """游戏素材下载器"""

    def __init__(self, resource_dir: str, config: dict = None):
        self.resource_dir = resource_dir
        self.config = config or {}
        self.assets_dir = os.path.join(resource_dir, "gacha_assets")
        self.marker_path = os.path.join(self.assets_dir, VERSION_MARKER)

    @property
    def is_downloaded(self) -> bool:
        """检查素材是否已下载且版本匹配"""
        if not os.path.isdir(self.assets_dir):
            return False
        if not os.path.exists(self.marker_path):
            return False
        try:
            with open(self.marker_path, "r") as f:
                return f.read().strip() == ASSET_VERSION
        except Exception:
            return False

    def get_installed_version(self) -> str:
        """获取已安装的素材版本"""
        if not os.path.exists(self.marker_path):
            return "none"
        try:
            with open(self.marker_path, "r") as f:
                return f.read().strip()
        except Exception:
            return "unknown"

    def _build_download_urls(self) -> list:
        """构建下载 URL 列表（原始 + 镜像代理）"""
        urls = [GITHUB_RELEASE_BASE]
        for proxy in MIRROR_PROXIES:
            urls.append(proxy + GITHUB_RELEASE_BASE)
        return urls

    async def download_assets(self, progress_callback=None) -> bool:
        """
        下载并解压素材包

        Args:
            progress_callback: 可选的进度回调 (status_text: str)

        Returns:
            是否成功
        """
        if self.is_downloaded:
            logger.info(f"[AssetDownloader] 素材已是最新版本 ({ASSET_VERSION})，无需下载")
            return True

        urls = self._build_download_urls()

        for i, url in enumerate(urls):
            try:
                if progress_callback:
                    source = "原始地址" if i == 0 else f"镜像 {i}"
                    await progress_callback(f"正在从{source}下载素材包...")

                logger.info(f"[AssetDownloader] 尝试下载: {url}")
                data = await self._download_with_timeout(url, timeout=300)

                if data is None or len(data) < 1000:
                    logger.warning(f"[AssetDownloader] 下载数据无效或过短 ({len(data) if data else 0} bytes)，跳过")
                    continue

                if progress_callback:
                    size_mb = len(data) / (1024 * 1024)
                    await progress_callback(f"下载完成 ({size_mb:.1f}MB)，正在解压...")

                logger.info(f"[AssetDownloader] 下载成功: {len(data)} bytes")

                # 解压
                success = self._extract_zip(data)
                if success:
                    # 写入版本标记
                    self._write_version_marker()
                    if progress_callback:
                        await progress_callback(f"素材安装完成！版本: {ASSET_VERSION}")
                    return True
                else:
                    logger.warning("[AssetDownloader] 解压失败，尝试下一个源")
                    continue

            except asyncio.TimeoutError:
                logger.warning(f"[AssetDownloader] 下载超时: {url}")
                continue
            except Exception as e:
                logger.warning(f"[AssetDownloader] 下载失败: {url} - {e}")
                continue

        logger.error("[AssetDownloader] 所有下载源均失败")
        if progress_callback:
            await progress_callback("素材下载失败，将使用程序化渲染（功能不受影响）")
        return False

    async def _download_with_timeout(self, url: str, timeout: int = 300) -> bytes | None:
        """带超时的 HTTP 下载"""
        import aiohttp

        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"[AssetDownloader] HTTP {resp.status}: {url}")
                    return None
                return await resp.read()

    def _extract_zip(self, data: bytes) -> bool:
        """解压素材包到 resource/gacha_assets/"""
        try:
            buf = io.BytesIO(data)
            with zipfile.ZipFile(buf) as zf:
                # 安全检查：防止路径穿越
                for name in zf.namelist():
                    if name.startswith("/") or ".." in name:
                        logger.error(f"[AssetDownloader] 检测到不安全路径: {name}")
                        return False

                # 解压到 resource 目录（zip 内部应有 gacha_assets/ 前缀）
                zf.extractall(self.resource_dir)

            # 验证解压结果
            required_dirs = ["chars", "chars_r2", "rarity"]
            for d in required_dirs:
                if not os.path.isdir(os.path.join(self.assets_dir, d)):
                    logger.error(f"[AssetDownloader] 解压后缺少目录: {d}")
                    return False

            # 统计文件数
            file_count = sum(
                len(files) for _, _, files in os.walk(self.assets_dir)
            )
            logger.info(f"[AssetDownloader] 解压完成: {file_count} 个文件")
            return True

        except (zipfile.BadZipFile, Exception) as e:
            logger.error(f"[AssetDownloader] 解压失败: {e}")
            return False

    def _write_version_marker(self):
        """写入版本标记"""
        os.makedirs(self.assets_dir, exist_ok=True)
        with open(self.marker_path, "w") as f:
            f.write(ASSET_VERSION)
