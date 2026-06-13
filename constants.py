"""
共享常量 - 消除跨文件重复定义
"""

# 稀有度映射
RARITY_MAP = {
    "TIER_1": 1, "TIER_2": 2, "TIER_3": 3,
    "TIER_4": 4, "TIER_5": 5, "TIER_6": 6,
}

# GitHub raw URL base
GITHUB_BASE = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/"
GITEE_BASE = "https://gitee.com/Kengxxiao/ArknightsGameData/raw/master/zh_CN/gamedata/excel/"

# GitHub 镜像代理（用于国内环境下载）
GITHUB_PROXIES = [
    "https://edgeone.gh-proxy.com/",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
]
