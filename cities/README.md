# cities/ — 城市配置

rent-ops 多城市适配的源头。每个城市一份 YAML，`config/profile.yml` 里写 `city: 北京` 或 `city: beijing` 就能切过去。

## 已内置

| 文件 | 城市 | 状态 |
|------|------|------|
| `shenzhen.yml` | 深圳 | ✅ 完整（作者常用，坐标人工核对） |
| `beijing.yml`  | 北京 | ⚠️ 豆瓣 group_id 未核实 |
| `shanghai.yml` | 上海 | ⚠️ 豆瓣 group_id 未核实 |
| `guangzhou.yml`| 广州 | ⚠️ 豆瓣 group_id 未核实 |
| `hangzhou.yml` | 杭州 | ⚠️ 豆瓣 group_id 未核实 |
| `chengdu.yml`  | 成都 | ⚠️ 豆瓣 group_id 未核实 |

**未核实**指作者未亲自验证豆瓣小组 ID。第一次跑 `/rent scrape` 时如果报错，去 `https://www.douban.com/group/search?cat=1013&q={城市}租房` 手查后覆盖 `douban.group_id`。

## Schema

```yaml
name: 北京                  # 中文名
pinyin: beijing             # 英文 slug（文件名不带 .yml 的部分）
code: bj                    # 平台子域代码（贝壳/58/安居客/自如/房天下 通用）

center: [116.4074, 39.9042] # [lng, lat]，地图默认中心
amap_city_name: 北京         # 高德 PlaceSearch 的 city 字段

douban:
  group_id: "279962"        # 豆瓣小组数字 ID
  group_name: 北京租房
  group_url: https://www.douban.com/group/279962/

areas:                      # 行政区 → 片区
  朝阳:
    center: [116.4551, 39.9219]
    color: "#0071e3"        # 可选，地图颜色
    sub_areas:
      国贸:    { pos: [116.4597, 39.9087], color: "#0071e3" }
      三里屯:  { pos: [116.4533, 39.9346] }

platform_overrides:         # 可选，平台特定的城市参数
  fangtianxia:
    area_codes:
      朝阳: a02
```

## 新增一个城市

1. **找 `code`** — 去贝壳/58/安居客 看你城市的子域是啥（武汉=wh、西安=xa、苏州=su、南京=nj、重庆=cq...）
2. **找豆瓣小组 ID** — 搜 `{城市}租房`，取 URL 里的数字
3. **填行政区** — 只填你关心的 3-5 个片区即可，其他可先空
4. **拿坐标** — 高德地图/百度地图搜地标，右键可复制坐标（注意 lng,lat 顺序）
5. **复制 `shenzhen.yml`** 作为模板，替换字段，保存为 `{pinyin}.yml`
6. **测一下** — `python scripts/lib/city.py {pinyin}` 应该能 dump 出解析后的配置

## 设计原则

- **code 和 pinyin 分开**：`code` 是平台用的（sz/bj），`pinyin` 是文件名和 CLI 用的（shenzhen/beijing）。贝壳北京子域是 `bj.ke.com`，不是 `beijing.ke.com`。
- **areas 是"命名位置"，不是行政区划**：只要是用户会提到的片区名（如"望京"、"后海"、"软件园"）都值得列，用来做区域识别正则 + 地图颜色。
- **坐标不是精确**：小区级坐标由 AMap PlaceSearch 动态解析，这里的坐标只是**兜底 + 默认视角**。不用追求精确到米。
- **color 可选**：地图渲染时未指定会自动分配（palette 见 `data/map-view.html`）。
- **未核实数据不敷衍**：未核实的字段必须加 `# 未核实` 注释，让用户清楚。

## 香港 / 海外

暂未支持。港澳的房源生态（28Hse/Spacious/Squarefoot/中原）和大陆平台完全不同，后续会用单独的 `cities/hongkong.yml` + `platforms-hk.yml` 适配层。
