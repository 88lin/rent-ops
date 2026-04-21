# 模式：map — 高德地图可视化

在浏览器中打开房源地图，展示所有已跟踪的房源位置。

## 前提

- 高德地图 JS API Key（Web端类型）— 需在 [console.amap.com](https://console.amap.com) 申请
- API Key 和安全密钥已配置在 `${CLAUDE_SKILL_DIR}/data/map-view.html` 中

## 执行

### 1. 生成 city-runtime.json

根据 `${CLAUDE_SKILL_DIR}/config/profile.yml` 的 `city` 字段和对应 `${CLAUDE_SKILL_DIR}/cities/{pinyin}.yml`，生成地图需要的运行时配置：

```bash
${CLAUDE_SKILL_DIR}/scripts/python.sh ${CLAUDE_SKILL_DIR}/scripts/build_city_runtime.py
```

输出 `${CLAUDE_SKILL_DIR}/data/city-runtime.json`。map-view.html 启动时会 fetch 这个文件拿到当前城市上下文（中心坐标、片区、颜色、工作地文本）。

**切换城市流程：** 改 `config/profile.yml` 的 `city` 字段 → 重跑上述命令 → 刷新页面。

### 2. 确认数据文件

检查 `${CLAUDE_SKILL_DIR}/data/listings.json` 是否存在且非空。如果不存在或为空：
- 若 `city=shenzhen`，map-view.html 会 fallback 加载 `${CLAUDE_SKILL_DIR}/data/sample-shenzhen.json` 示例数据
- 其他城市：提示用户先运行 `/rent scrape` 或 `/rent scan` 获取房源数据

### 3. 启动 HTTP 服务

高德地图 JS API 不支持 `file://` 协议，必须通过 HTTP 访问：

```bash
cd ${CLAUDE_SKILL_DIR}/data && ${CLAUDE_SKILL_DIR}/scripts/python.sh -m http.server 8765 &
```

检查端口是否已被占用（`lsof -i :8765`），如已占用则跳过启动。

### 4. 打开地图

```bash
open http://localhost:8765/map-view.html
```

## 地图功能

### 标记
- 红色脉冲圆点：工作地点（profile.yml 的 `work_location`，由 AMap.Geocoder 解析为坐标）
- 彩色圆点：房源位置，颜色按区域区分（色值来自 `cities/{pinyin}.yml` 的 `areas[].color`，未指定时自动分配）
- 大圆（有数字）：有明确价格的房源，数字 = 月租/千元
- 小圆（?）：价格待询

### 筛选器
顶部 chip 动态渲染，来源是当前城市 `cities/{pinyin}.yml` 的 `areas`：
- 按行政区/片区（按 `area_order` 前 12 个自动生成）
- 按价格档（用户 budget_max 的 0.85x）
- 按平台（小红书 / 豆瓣 等）

### 详情面板
点击圆点弹出右侧面板：
- 平台来源 + 小区名
- 价格 + 户型
- 距工作地直线距离
- 「查看原帖」链接

### 地理编码
新抓取的房源通过高德 PlaceSearch API 自动定位小区坐标：
- 从标题提取小区名（如「云海天城」「万科云城」）→ POI 搜索 → 真实坐标
- 提取失败时降级到区域中心点，面板标注「大致位置」
- 右下角显示定位进度

## 更新地图数据

地图数据来自 `${CLAUDE_SKILL_DIR}/data/listings.json`。更新流程：

1. 运行 `/rent scrape`（爬取新数据）
2. 数据整合脚本会自动更新 `listings.json`
3. 刷新浏览器页面即可看到新房源

## API Key 配置

用户需要在 `${CLAUDE_SKILL_DIR}/data/map-view.html` 中配置两个值：

```html
<script>
window._AMapSecurityConfig = { securityJsCode: '你的安全密钥' };
</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=你的API_Key&plugin=..."></script>
```

获取方式：
1. 访问 [console.amap.com](https://console.amap.com)
2. 创建应用 → 添加 Key → 选择「Web端(JS API)」
3. 复制 Key 和安全密钥到上述位置

## 扩展地图

想在 `data/map-view.html` 之外加能力（3D 楼块、热力图、POI 聚合、行政区边界、自定义图层等）？调用高德官方的 `amap-jsapi-skill`（<https://clawhub.ai/lbs-amap/amap-jsapi-skill>）让它按需求生成代码片段，再合并进 `map-view.html`。rent-ops 这边只维护最小可用版本。
