#!/usr/bin/env python3
"""生成 data/city-runtime.json — 给 map-view.html / listings-view.html 消费。

合并 config/profile.yml + cities/{pinyin}.yml 的数据，扁平化 areas，输出单个 JSON。
map-view.html 启动时 fetch 这个文件就拿到当前城市的所有上下文。

用法：
  python3 scripts/build_city_runtime.py                   # 读 profile 里的 city
  python3 scripts/build_city_runtime.py --city beijing    # 指定城市
  python3 scripts/build_city_runtime.py --out path.json   # 指定输出路径
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.lib.city import (
    active_city,
    load_city,
    flatten_areas,
    CityNotFoundError,
)

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "city-runtime.json"
PROFILE_PATH = REPO_ROOT / "config" / "profile.yml"


def _load_profile() -> dict:
    if not PROFILE_PATH.exists():
        return {}
    with PROFILE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build(city: dict, profile: dict) -> dict:
    areas = flatten_areas(city)
    budget = profile.get("budget") or {}
    return {
        "schema_version": 1,
        "city": {
            "name": city.get("name"),
            "pinyin": city.get("pinyin"),
            "code": city.get("code"),
            "center": city.get("center"),
            "amap_city_name": city.get("amap_city_name") or city.get("name"),
        },
        "areas": areas,
        "area_order": list(areas.keys()),
        "work_location": {
            # text only; map-view.html 会用 AMap.Geocoder 解析为坐标
            "label": profile.get("work_location"),
        },
        "profile": {
            "budget_min": budget.get("min"),
            "budget_max": budget.get("max"),
            "type": profile.get("type"),
            "rooms": profile.get("rooms") or [],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    try:
        city = load_city(args.city) if args.city else active_city()
    except CityNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    profile = _load_profile()
    runtime = build(city, profile)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    area_count = len(runtime["areas"])
    print(
        f"✓ 写入 {args.out}  城市={runtime['city']['name']}"
        f"  areas={area_count}"
        f"  work={runtime['work_location']['label'] or '未设置'}"
    )


if __name__ == "__main__":
    main()
