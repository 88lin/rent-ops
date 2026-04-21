#!/usr/bin/env python3
"""高德 Web 服务 API 客户端。

封装 4 个核心能力：
- geocode(text, city)              地理编码：地址/小区名 → 坐标
- search_around(center, type, ...) 周边 POI 搜索
- route_transit(from, to, city)    公交+地铁路线（含换乘）
- route_driving / walking / bicycling  其他出行方式

设计原则：
- 纯标准库 + urllib（不强依赖 requests）
- 带本地 JSON 缓存，同请求复用，不烧 quota
- 所有失败都返回 dict（status="error", message=...），不抛异常
- 离线模式（无 key）时整体 disable，返回 {"status": "disabled"}

直接当 CLI 也行（简单调试用）：
    python3 scripts/lib/amap.py geocode "望京SOHO" --city 北京
    python3 scripts/lib/amap.py poi 116.48,39.99 --type 060200 --radius 500
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import yaml
except ImportError:
    print("需要 PyYAML: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "amap.yml"
DEFAULT_CACHE_PATH = REPO_ROOT / "data" / "amap-cache.json"

BASE = "https://restapi.amap.com"
USER_AGENT = "rent-ops/0.2"


# ─── 配置加载 ────────────────────────────────────────────────────────────────
def load_amap_config(path: Path | None = None) -> dict[str, Any]:
    """读 config/amap.yml。不存在或 key 为空 → 返回 {'disabled': True, 'reason': ...}。"""
    p = path or DEFAULT_CONFIG_PATH
    if not p.exists():
        return {
            "disabled": True,
            "reason": f"未找到 {p}，请从 templates/amap.example.yml 复制一份并填入 Web 服务 Key",
        }
    with p.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not cfg.get("web_service_key"):
        return {
            "disabled": True,
            "reason": f"{p} 的 web_service_key 为空。申请见文件内注释",
        }
    return cfg


# ─── 缓存 ────────────────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    @staticmethod
    def key_for(endpoint: str, params: dict) -> str:
        # 把 key 本身排除在缓存键外，避免 key 变了缓存失效
        canon = json.dumps(
            {"ep": endpoint, "p": {k: v for k, v in params.items() if k != "key"}},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha1(canon.encode("utf-8")).hexdigest()

    def get(self, k: str) -> Any | None:
        entry = self._data.get(k)
        if not entry:
            return None
        return entry.get("v")

    def put(self, k: str, value: Any) -> None:
        self._data[k] = {"v": value, "ts": int(time.time())}
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
        )


# ─── 客户端 ──────────────────────────────────────────────────────────────────
class AmapClient:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config if config is not None else load_amap_config()
        self.disabled = bool(self.config.get("disabled"))
        self.key = self.config.get("web_service_key", "")
        self.retries = int(self.config.get("retries", 2))
        cache_path = Path(self.config.get("cache_path", DEFAULT_CACHE_PATH))
        if not cache_path.is_absolute():
            cache_path = REPO_ROOT / cache_path
        self.cache = _Cache(cache_path)

    # ---- 内部请求封装 ----
    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.disabled:
            return {"status": "disabled", "message": self.config.get("reason", "")}
        # 复制 + 注入 key
        full = dict(params)
        full["key"] = self.key
        cache_k = _Cache.key_for(endpoint, full)
        cached = self.cache.get(cache_k)
        if cached is not None:
            return cached

        url = f"{BASE}{endpoint}?{urlencode(full, doseq=True)}"
        is_v4 = "/v4/" in endpoint
        err: str = ""
        infocode: str = ""
        for attempt in range(self.retries + 1):
            try:
                req = Request(url, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=8) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                # v3 成功：status="1"；v4 成功：errcode=0
                if is_v4:
                    success = str(data.get("errcode")) == "0"
                else:
                    success = str(data.get("status")) == "1"
                if success:
                    self.cache.put(cache_k, data)
                    return data
                if is_v4:
                    infocode = str(data.get("errcode") or "")
                    err = f"{infocode} {data.get('errmsg')}"
                else:
                    infocode = str(data.get("infocode") or "")
                    err = f"{infocode} {data.get('info')}"
                # 10003 限流、10021 服务不可用 → 值得重试；其他是业务错误，不重试
                if infocode not in ("10003", "10021", "10026"):
                    break
            except (HTTPError, URLError) as e:
                err = f"network: {e}"
            except json.JSONDecodeError as e:
                err = f"bad json: {e}"
            time.sleep(0.4 * (attempt + 1))
        # Actionable hints for 常见 auth 错误码
        hints = {
            "10001": "Key 无效或已删除",
            "10008": "Key 未开通该 API 的权限",
            "10009": "Key 的服务平台不匹配（需要「Web服务」类型，不是 JS API）",
            "10010": "Key 的 IP 白名单校验未通过",
            "10044": "Key 当日调用量已超额度",
        }
        out: dict[str, Any] = {"status": "error", "message": err or "unknown"}
        if infocode in hints:
            out["infocode"] = infocode
            out["hint"] = hints[infocode]
            out["fix"] = "去 console.amap.com → 应用管理 → 添加 Key → 服务平台选「Web服务」，把新 Key 填到 config/amap.yml"
        return out

    # ---- 地理编码 ----
    def geocode(self, address: str, city: str = "") -> dict[str, Any]:
        """返回 {lng, lat, formatted} 或 {status:'error',...}"""
        r = self._request("/v3/geocode/geo", {
            "address": address,
            "city": city or "",
        })
        if r.get("status") != "1":
            return r
        geocodes = r.get("geocodes") or []
        if not geocodes:
            return {"status": "empty", "message": f"未找到地址: {address}"}
        g = geocodes[0]
        lng, lat = g["location"].split(",")
        return {
            "status": "ok",
            "lng": float(lng),
            "lat": float(lat),
            "formatted": g.get("formatted_address"),
            "level": g.get("level"),
        }

    # ---- POI 文本搜索（geocode 兜底 + 关键词搜索）----
    def search_text(
        self, keywords: str, city: str = "", city_limit: bool = True,
        types: str = "", page_size: int = 10,
    ) -> dict[str, Any]:
        """对 /v3/place/text 的封装。比 geocode 更能处理片区名/商圈名。"""
        params: dict[str, Any] = {
            "keywords": keywords,
            "offset": str(page_size),
            "page": "1",
            "extensions": "base",
            "citylimit": "true" if city_limit else "false",
        }
        if city:
            params["city"] = city
        if types:
            params["types"] = types
        r = self._request("/v3/place/text", params)
        if r.get("status") != "1":
            return r
        pois = r.get("pois") or []
        if not pois:
            return {"status": "empty", "message": f"未找到: {keywords}"}
        first = pois[0]
        lng, lat = first["location"].split(",")
        return {
            "status": "ok",
            "lng": float(lng),
            "lat": float(lat),
            "name": first.get("name"),
            "address": first.get("address"),
            "type": first.get("type"),
        }

    def resolve_location(self, text: str, city: str = "") -> dict[str, Any]:
        """综合解析：先 geocode，失败时降级 PlaceSearch 文本搜索。
        片区名（如「科技园」）、商圈名、小区名都能 handle。"""
        g = self.geocode(text, city)
        if g.get("status") == "ok":
            return g
        # geocode 失败/empty → PlaceSearch 兜底
        s = self.search_text(text, city)
        if s.get("status") == "ok":
            s["source"] = "place_search"
            return s
        # 两种都失败，返回最后一个错误
        return s if s.get("status") != "ok" else g

    # ---- POI 周边搜索 ----
    def search_around(
        self,
        location: tuple[float, float] | str,
        types: str = "",
        keywords: str = "",
        radius: int = 1000,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """location 传 (lng,lat) 或 "lng,lat"。"""
        loc = location if isinstance(location, str) else f"{location[0]},{location[1]}"
        params: dict[str, Any] = {
            "location": loc,
            "radius": str(radius),
            "offset": str(page_size),
            "page": "1",
            "extensions": "base",
        }
        if types:
            params["types"] = types
        if keywords:
            params["keywords"] = keywords
        r = self._request("/v3/place/around", params)
        if r.get("status") != "1":
            return r
        pois = []
        for poi in r.get("pois") or []:
            loc_xy = poi.get("location") or ""
            try:
                px, py = loc_xy.split(",")
                px, py = float(px), float(py)
            except ValueError:
                continue
            pois.append({
                "id": poi.get("id"),
                "name": poi.get("name"),
                "type": poi.get("type"),
                "lng": px,
                "lat": py,
                "address": poi.get("address"),
                "distance_m": int(poi.get("distance") or 0),
                "tel": poi.get("tel"),
            })
        pois.sort(key=lambda p: p["distance_m"])
        return {"status": "ok", "count": len(pois), "pois": pois}

    # ---- 路径规划 ----
    def route_transit(
        self, origin: tuple[float, float] | str, destination: tuple[float, float] | str,
        city: str, city_dest: str = "",
    ) -> dict[str, Any]:
        """公交+地铁。返回最快方案的 {duration_min, distance_m, transfers, segments}。"""
        o = origin if isinstance(origin, str) else f"{origin[0]},{origin[1]}"
        d = destination if isinstance(destination, str) else f"{destination[0]},{destination[1]}"
        r = self._request("/v3/direction/transit/integrated", {
            "origin": o,
            "destination": d,
            "city": city,
            "cityd": city_dest or city,
            "strategy": "0",
            "nightflag": "0",
        })
        if r.get("status") != "1":
            return r
        transits = ((r.get("route") or {}).get("transits")) or []
        if not transits:
            return {"status": "empty", "message": "无公交方案（可能距离过近）"}
        best = min(transits, key=lambda t: int(t.get("duration") or 0))
        segments = best.get("segments") or []
        bus_count = sum(
            1 for s in segments
            if (s.get("bus") or {}).get("buslines")
        )
        return {
            "status": "ok",
            "duration_min": round(int(best.get("duration") or 0) / 60, 1),
            "walking_distance_m": int(best.get("walking_distance") or 0),
            "distance_m": int(best.get("distance") or 0),
            "transfers": max(0, bus_count - 1),  # 换乘次数 = 公交/地铁段 - 1
            "cost_cny": float(best.get("cost") or 0),
            "segments_count": len(segments),
        }

    def route_driving(
        self, origin: tuple[float, float] | str, destination: tuple[float, float] | str,
    ) -> dict[str, Any]:
        o = origin if isinstance(origin, str) else f"{origin[0]},{origin[1]}"
        d = destination if isinstance(destination, str) else f"{destination[0]},{destination[1]}"
        r = self._request("/v3/direction/driving", {
            "origin": o, "destination": d, "strategy": "32",
        })
        if r.get("status") != "1":
            return r
        paths = ((r.get("route") or {}).get("paths")) or []
        if not paths:
            return {"status": "empty", "message": "无驾车方案"}
        best = paths[0]
        return {
            "status": "ok",
            "duration_min": round(int(best.get("duration") or 0) / 60, 1),
            "distance_m": int(best.get("distance") or 0),
            "tolls_cny": float(best.get("tolls") or 0),
        }

    def route_walking(
        self, origin: tuple[float, float] | str, destination: tuple[float, float] | str,
    ) -> dict[str, Any]:
        o = origin if isinstance(origin, str) else f"{origin[0]},{origin[1]}"
        d = destination if isinstance(destination, str) else f"{destination[0]},{destination[1]}"
        r = self._request("/v3/direction/walking", {"origin": o, "destination": d})
        if r.get("status") != "1":
            return r
        paths = ((r.get("route") or {}).get("paths")) or []
        if not paths:
            return {"status": "empty", "message": "无步行方案"}
        best = paths[0]
        return {
            "status": "ok",
            "duration_min": round(int(best.get("duration") or 0) / 60, 1),
            "distance_m": int(best.get("distance") or 0),
        }

    def route_bicycling(
        self, origin: tuple[float, float] | str, destination: tuple[float, float] | str,
    ) -> dict[str, Any]:
        o = origin if isinstance(origin, str) else f"{origin[0]},{origin[1]}"
        d = destination if isinstance(destination, str) else f"{destination[0]},{destination[1]}"
        r = self._request("/v4/direction/bicycling", {"origin": o, "destination": d})
        # 由 _request 的 v4 分支保证成功响应才返回；否则透传 error 上来
        if r.get("status") == "error":
            return r
        data = r.get("data") or {}
        paths = data.get("paths") or []
        if not paths:
            return {"status": "empty", "message": "无骑行方案"}
        best = paths[0]
        return {
            "status": "ok",
            "duration_min": round(int(best.get("duration") or 0) / 60, 1),
            "distance_m": int(best.get("distance") or 0),
        }


# ─── CLI (调试用) ────────────────────────────────────────────────────────────
def _resolve_location(client: AmapClient, text: str, city: str) -> tuple[float, float] | None:
    """'lng,lat' 原样返；否则 geocode。"""
    if "," in text:
        try:
            lng, lat = text.split(",")
            return float(lng), float(lat)
        except ValueError:
            pass
    r = client.geocode(text, city)
    if r.get("status") != "ok":
        return None
    return r["lng"], r["lat"]


def _cli() -> None:
    parser = argparse.ArgumentParser(description="高德 Web 服务 API 客户端（调试）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_geo = sub.add_parser("geocode", help="地理编码")
    p_geo.add_argument("address")
    p_geo.add_argument("--city", default="")

    p_poi = sub.add_parser("poi", help="周边搜索")
    p_poi.add_argument("location", help="'lng,lat' 或地址文本")
    p_poi.add_argument("--type", default="")
    p_poi.add_argument("--keywords", default="")
    p_poi.add_argument("--radius", type=int, default=1000)
    p_poi.add_argument("--city", default="")

    for name in ("transit", "driving", "walking", "bicycling"):
        sp = sub.add_parser(name, help=f"{name} 路线")
        sp.add_argument("origin")
        sp.add_argument("destination")
        sp.add_argument("--city", default="")

    args = parser.parse_args()
    client = AmapClient()
    if client.disabled:
        print(json.dumps({"status": "disabled", "reason": client.config.get("reason")},
                          ensure_ascii=False, indent=2))
        sys.exit(2)

    if args.cmd == "geocode":
        out = client.geocode(args.address, args.city)
    elif args.cmd == "poi":
        loc = _resolve_location(client, args.location, args.city)
        if loc is None:
            out = {"status": "error", "message": f"无法解析 location: {args.location}"}
        else:
            out = client.search_around(loc, types=args.type, keywords=args.keywords,
                                        radius=args.radius)
    elif args.cmd in ("transit", "driving", "walking", "bicycling"):
        o = _resolve_location(client, args.origin, args.city)
        d = _resolve_location(client, args.destination, args.city)
        if not o or not d:
            out = {"status": "error", "message": "origin/destination 解析失败"}
        else:
            if args.cmd == "transit":
                if not args.city:
                    out = {"status": "error", "message": "--city required for transit"}
                else:
                    out = client.route_transit(o, d, args.city)
            elif args.cmd == "driving":
                out = client.route_driving(o, d)
            elif args.cmd == "walking":
                out = client.route_walking(o, d)
            else:
                out = client.route_bicycling(o, d)
    else:
        parser.error("unknown cmd")
        return

    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out.get("status") == "ok" else 1)


if __name__ == "__main__":
    _cli()
