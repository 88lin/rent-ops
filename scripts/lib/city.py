#!/usr/bin/env python3
"""城市配置 loader。

用法（库）：
    from scripts.lib.city import load_city, active_city
    cfg = active_city()                    # 读 config/profile.yml 的 city 字段
    cfg = load_city("beijing")             # 指定城市
    url = resolve_platform_url(platform_cfg, cfg)

用法（CLI，用于调试）：
    python3 scripts/lib/city.py shenzhen   # dump 解析后的配置
    python3 scripts/lib/city.py --list     # 列出所有可用城市
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("需要 PyYAML: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
CITIES_DIR = REPO_ROOT / "cities"
PROFILE_PATH = REPO_ROOT / "config" / "profile.yml"


class CityNotFoundError(Exception):
    pass


def list_cities() -> list[str]:
    """所有可用城市 pinyin 列表。"""
    return sorted(p.stem for p in CITIES_DIR.glob("*.yml"))


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_city(name_or_pinyin: str) -> dict[str, Any]:
    """按中文名或拼音加载城市配置。找不到抛 CityNotFoundError。"""
    if not name_or_pinyin:
        raise CityNotFoundError("city 参数为空")

    # 直接拼音命中
    direct = CITIES_DIR / f"{name_or_pinyin}.yml"
    if direct.exists():
        return _load_yaml(direct)

    # 中文名反查
    for path in CITIES_DIR.glob("*.yml"):
        cfg = _load_yaml(path)
        if cfg.get("name") == name_or_pinyin or cfg.get("pinyin") == name_or_pinyin:
            return cfg

    available = list_cities()
    raise CityNotFoundError(
        f"未找到城市 '{name_or_pinyin}'。可用: {', '.join(available)}。"
        f"新增见 cities/README.md"
    )


def active_city() -> dict[str, Any]:
    """从 config/profile.yml 读 city 字段并加载。"""
    if not PROFILE_PATH.exists():
        raise CityNotFoundError(
            f"{PROFILE_PATH} 不存在。先从 config/profile.example.yml 复制一份。"
        )
    profile = _load_yaml(PROFILE_PATH)
    city_name = profile.get("city")
    if not city_name:
        raise CityNotFoundError(f"{PROFILE_PATH} 缺少 city 字段")
    return load_city(city_name)


def resolve_platform_url(template: str, city: dict[str, Any]) -> str:
    """把 platforms.yml 里的模板 URL 替换成真实 URL。

    支持占位符：
        {city_code}       如 sz/bj/sh
        {city_pinyin}     如 shenzhen/beijing
        {city_name}       如 深圳/北京（URL 编码由调用方负责）
    """
    return (
        template
        .replace("{city_code}", city.get("code", ""))
        .replace("{city_pinyin}", city.get("pinyin", ""))
        .replace("{city_name}", city.get("name", ""))
    )


def all_area_names(city: dict[str, Any]) -> list[str]:
    """返回所有区域名 + sub_areas 名，去重保序。用于爬虫 AREA_RE。"""
    names: list[str] = []
    seen: set[str] = set()
    for area, area_cfg in (city.get("areas") or {}).items():
        if area not in seen:
            names.append(area)
            seen.add(area)
        for sub_name in (area_cfg.get("sub_areas") or {}):
            if sub_name not in seen:
                names.append(sub_name)
                seen.add(sub_name)
    return names


def build_area_regex(city: dict[str, Any]) -> "re.Pattern[str]":
    """构造匹配该城市所有片区名的正则。"""
    names = all_area_names(city)
    if not names:
        # 空城市（只有 center 没填 areas）→ 永远不匹配
        return re.compile(r"^(?!.*)")
    # 按长度降序，避免"南山"抢先匹配"南山区"
    names.sort(key=len, reverse=True)
    return re.compile("|".join(re.escape(n) for n in names))


def flatten_areas(city: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{ area_name: { pos, color, parent? } }，包含 sub_areas（parent=行政区名）。"""
    out: dict[str, dict[str, Any]] = {}
    for area, area_cfg in (city.get("areas") or {}).items():
        out[area] = {
            "pos": area_cfg.get("center"),
            "color": area_cfg.get("color"),
        }
        for sub_name, sub_cfg in (area_cfg.get("sub_areas") or {}).items():
            # sub_cfg 可能是 {pos:..., color:...} 或简写 [lng,lat]
            if isinstance(sub_cfg, dict):
                out[sub_name] = {
                    "pos": sub_cfg.get("pos") or sub_cfg.get("center"),
                    "color": sub_cfg.get("color"),
                    "parent": area,
                }
            elif isinstance(sub_cfg, list):
                out[sub_name] = {"pos": sub_cfg, "color": None, "parent": area}
    return out


def _main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        sys.exit(0)
    if args[0] == "--list":
        for c in list_cities():
            cfg = _load_yaml(CITIES_DIR / f"{c}.yml")
            print(f"  {c:12s} {cfg.get('name', ''):6s} (code={cfg.get('code', '')})")
        return

    try:
        cfg = load_city(args[0])
    except CityNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    summary = {
        "name": cfg.get("name"),
        "pinyin": cfg.get("pinyin"),
        "code": cfg.get("code"),
        "center": cfg.get("center"),
        "douban_group": cfg.get("douban", {}).get("group_id"),
        "area_count": len(cfg.get("areas") or {}),
        "area_names": all_area_names(cfg),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
