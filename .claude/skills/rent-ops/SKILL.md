---
name: rent-ops
description: AI 租房助手 — 扫描平台、评估房源、检测风险、准备看房
user_invocable: true
args: mode
---

# rent-ops — 路由

## Mode 路由

根据 `{{mode}}` 确定执行哪个模式：

| 输入 | Mode |
|------|------|
| （空 / 无参数） | `discovery` — 显示命令菜单 |
| 房源链接或描述文本（非子命令） | **`auto-evaluate`** |
| `scan` | `scan` |
| `tracker` | `tracker` |
| `risk` | `risk` |
| `visit` | `visit` |
| `map` | `map` — 打开地图可视化 |
| `scrape` | `scrape` — 执行豆瓣/小红书爬虫 |
| `compare` | 提示「v0.2 开发中」 |
| `negotiate` | 提示「v0.2 开发中」 |
| `batch` | 提示「v0.2 开发中」 |

**自动检测：** 如果 `{{mode}}` 不是已知子命令，但包含房源链接（ke.com、ziroom.com、58.com、douban.com、xiaohongshu.com、xianyu.com），自动走 `auto-evaluate`。

如果 `{{mode}}` 既不是子命令也不像房源链接，显示 discovery。

---

## Discovery Mode（无参数）

显示：

```
🏠 rent-ops — AI 租房助手

可用命令：

/rent {粘贴链接}          → 自动评估（抓取 + 打分 + 风险扫描 + 入 tracker）
/rent scan               → 主动扫描各平台找房（WebSearch/Playwright）
/rent scrape             → 执行豆瓣/小红书爬虫（Playwright stealth + MediaCrawler）
/rent map                → 打开高德地图可视化（所有房源定位展示）
/rent tracker            → 查看/管理房源跟踪表
/rent risk {小区名}       → 搜集该小区避雷信息
/rent visit              → 看房准备（路线 + checklist + 砍价话术）

即将支持：
/rent compare            → 多房源横向对比
/rent negotiate {房源#}   → 砍价策略生成
/rent batch              → 批量评估

直接粘贴房源链接即可开始评估。
```

---

## Context 加载规则

确定 mode 后，加载对应文件再执行：

### 需要 _shared.md + _profile.md + mode 文件：
- `auto-evaluate`, `scan`

加载顺序：先读 `modes/_shared.md`，再读 `modes/_profile.md`（用户配置覆盖系统默认），最后读 `modes/{mode}.md`。

### 需要 _shared.md + _profile.md + mode 文件 + tracker 数据：
- `visit`

额外读取 `data/listings.md` 获取已有房源信息。

### 需要 _shared.md + _profile.md + mode 文件 + platforms.yml：
- `scrape`

额外读取 `platforms.yml` 获取爬虫配置和运行命令。

### 独立 mode（只读自己的 mode 文件）：
- `tracker`, `risk`, `map`

---

## Onboarding 检查

**每次会话首次触发 /rent 时**，在执行任何 mode 之前，按照 `CLAUDE.md` 中的 Onboarding 流程检查必要文件是否存在。如果缺失，先完成引导再执行 mode。
