# H3 控制台（看板）设计参考

> **本文是 v0.4 前端看板（h3ctl go:embed 静态页）的实现蓝本。**
> 原型：[`原型/H3控制台.html`](../原型/H3控制台.html)（单文件、零依赖、双击即开；仓库内未提交，仅本地保留）。
> 原型 QA 基线：design audit 双视口（1440/390）必修 0；browser-use 真浏览器交互走查全过（2026-11-22）。

## 定位（用户拍板，勿回退）

**这是看板，不是操作台。** 出片提交、重试、改参数、回写 outputs 全部由 agent 完成；
人只看 **记录** 和 **日志**。面板唯一的操作：

1. 起机（开机，带进度）
2. 关机（两段式确认）
3. 取消开机
4. 重连隧道

不做出片表单、不做"新建任务"按钮、不做取消/重试任务——那是 agent 的活。

## 信息架构：三视图（⌘1–3 切换）

| 视图 | 内容 | 数据来源（实现时） |
|---|---|---|
| **看板** | CU 燃烧条 + 近 14 天柱图 + 机位卡（起/关机在此）+ 最近产出条 | `/state` 快照：quota / usage_daily / stations / recent_outputs |
| **记录** | 只读任务表：任务（名称+工作流 tag+prompt 摘要）、机位、提交（agent/手动 chip+时间）、状态（出片进度条/排队/完成/失败）、耗时·CU、产出（灯箱） | `/state.records[]`；产出文件 outputs/ |
| **日志** | 事件流水，**列表 / 终端双样式切换**（用户点名要终端样式，实现必须保留）；搜索 + 级别筛选；错误行可展开 detail | `/events` SSE（lv / src / msg / t / detail），两种渲染消费同一流 |

侧栏固定：品牌（H3 · MiniMax H3 生产管线 · v0.3.0-dev）、三导航（记录带活动任务数徽标、日志带 ERROR 数徽标）、隧道状态灯（ok/err + 地址）、⌘1–3 提示。
顶栏固定：视图标题+副题、余额 chip（mono 数字）。

## 设计 tokens（:root，照抄原型）

| token | 值 | 用途 |
|---|---|---|
| `--bg` | `#07080a` | 页面底（**html 与 body 都要设**，audit 对比度采样兜底） |
| `--surface` | `#101113` | 卡片 |
| `--surface-2` | `#17181c` | 卡内嵌套 / 按钮底 / 输入框 |
| `--border` | `rgba(255,255,255,.08)` | 常规描边 |
| `--border-strong` | `rgba(255,255,255,.16)` | 强调描边 / kbd / 输入框 |
| `--fg` | `#f2f3f5` | 主文字 |
| `--fg-2` | `#b8bdc7` | 次文字 |
| `--muted` | `#82889a` | 弱文字（对比度已过 AA，别再调暗） |
| `--accent` | `#55b3ff` | 主操作 / 链接 / 进度（暗底蓝，非 AI-slop 紫渐变） |
| `--accent-ink` | `#041220` | accent 上的文字 |
| `--ok` / `--warn` / `--danger` | `#54c78f` / `#ffb838` / `#ff6a5e` | 状态灯 / 警告 / 错误 |
| `--r-s` / `--r-m` | `8px` / `12px` | 小件 / 卡片圆角 |
| `--ease` | `cubic-bezier(.2,0,0,1)` | 全部过渡（M3 standard，非 M2 legacy 曲线） |
| `--font-ui` | system-ui + PingFang SC 栈 | 界面文字 |
| `--font-mono` | ui-monospace / SF Mono / Menlo 栈 | **一切数字、时间、CU、命令、日志**（`font-variant-numeric: tabular` 效果由 mono 保证） |

基调：暗色机房夜班控制台（Raycast 系）。无渐变横幅、无 emoji 图标（全部 1.6–1.8 描边 SVG 单色线图）。

## 间距与布局（本次踩坑后定死的节奏）

- **view 内区块间距：20px**（移动端 ≤860px：16px）。`.view` 必须是 `display:flex;flex-direction:column;gap:20px`——
  ⚠️ 教训：外层 main 的 flex gap 只作用于 view 之间，view 内部若不自己建 flex gap，**卡片会完全贴边**（v0.2 首版真实翻过车）
- main：`padding:24px 28px 28px`（上 24 / 左右 28 / 底 28，页脚前留气口）
- 页脚：`border-top` + `padding:14px 28px 20px`，**不带 margin-top**（由 main 底 padding 负责）
- 卡片：`padding:16px 18px`，卡内标题 `margin-bottom:12px`
- KPI 行：`grid: 1.45fr 1fr 1fr 1fr`，**`align-items:stretch` + `.kpi` flex column + 副行 `margin-top:auto`**——四卡强制等高、副行同底线
  （教训：`align-items:start` 会让各卡高度参差；只 stretch 不钉底则副行悬在半空）
- 机位卡内站点行：flex wrap + `.st-info{flex:1;flex-basis:180px}`（390px 不溢出）
- 记录表：`.tbl-scroll{overflow-x:auto}` 包住 `min-width:760px` 的 table（移动端横向滚动，不许撑破布局）
- 移动端 ≤860px：侧栏折叠为顶栏横向导航（brand-sub / 隧道卡 / ⌘ 提示隐藏），KPI 两列，图 x 轴隔项隐藏

## 组件规格

- **CU 燃烧条**：10px 圆角轨道，`transform:scaleX(var(--p))` 填充（不许用 width 动画），黄色投影刻度线（预计月底位置），meta 行：日均 / 挂机开销 / 预计月底剩（黄色）
- **机位卡**：状态灯（ok 绿呼吸 2.4s / warn 黄呼吸 1.2s / off 灰，`prefers-reduced-motion` 下全部静止）+ 名称 + 状态文案 + mono meta 行；booting 时出 progressbar（role+aria-valuenow）+ 取消按钮；running 时 出"收工关机"→ 点后原地换 **确认关机/取消** 两键（两段式确认，不弹窗）
- **记录表状态列**：出片（绿点+进度条 90px + `62% · 余 1:12`）/ 排队#n（灰点）/ 已完成（绿）/ 失败（红 + 下一行原因）；提交列 `agent`/`手动` chip + 时间；CU/耗时全部 mono
- **日志列表**：行 = 时间(mono) + 级别 chip(INFO 灰/WARN 黄/ERROR 红底) + 来源边框 tag(agent/h3api/h3up/h3down/tunnel/cost) + 消息；ERROR 行淡红底；detail 行尾 chevron 展开 `<pre>`（12px mono，横向滚动）
- **日志终端样式**：深底 `#0a0b0d` 控制台块，标题栏 `h3ctl logs --follow` + 行数；行 = grid `64px 48px 64px minmax(0,1fr) 24px`（时间/级别/来源/消息/展开钮），级别纯色无底，消息 `overflow-wrap:anywhere`；底部 `h3ctl ▍` 闪烁光标（steps(1) 1.1s，reduced-motion 静止）
- **灯箱**：原生 `<dialog>`，⚠️ **必须显式 `dialog{margin:auto}`**——全局 reset `*{margin:0}` 会杀掉 UA 居中，v0.2 真实翻过车（弹窗钉在左上角）；backdrop `rgba(0,0,0,.62)`；Esc / 点背景 / 右上 × 三种关闭都要通
- **toast**：底部居中胶囊，`role="status" aria-live="polite"`，2.6s 自动消
- **空态**：虚线框卡 + 标题 + 一句引导（记录筛选空 / 日志搜索空各一个）
- **数字**：余额、CU、耗时、百分比、时间戳一律 `--font-mono`

## 交互规范

- ⌘1/2/3（Ctrl 同）切视图，`preventDefault`
- 视图切换同步顶栏标题/副题 + 导航 `aria-current`
- 筛选（记录状态 5 chip / 日志级别 4 chip）：`aria-pressed`，计数实时算，空结果显示空态块
- 面板操作全部有 toast 反馈，且**写一条实时日志**（起机 → h3up、关机 → h3down、重连 → tunnel）——这是 `/events` SSE 的演示闭环，实现时同样由后端广播
- 灯箱可从 记录"查看" 和 看板"最近产出" 两个入口打开

## 数据契约映射（v0.3 已预埋 /state + /events SSE；v0.4 h3ctl(Go) go:embed 本页）

| 原型演示字段 | 真实来源 |
|---|---|
| 余额 / 燃烧条 / 挂机开销 / 月底预测 | `h3_usage.sh` 输出 → `/state.quota`（AI Pro 200 CU/月，¥≈0.72/CU） |
| 近 14 天柱图 | 用量时序 → `/state.usage_daily[]` |
| 机位卡（状态/费率/会话时长/今日 CU） | `h3_status.sh` + 会话计时 → `/state.stations[]`；起/关机 → POST 到本地 h3ctl → 调 `h3_up.sh` / `h3_down.sh`（起机 4–6 分钟，进度来自 h3up 输出流） |
| 记录表 | agent 任务账本（提交者=agent/手动、工作流 profile、耗时、CU、产出路径）→ `/state.records[]` |
| 日志流 | h3up/h3down/h3api/agent/tunnel/cost 事件 → `/events` SSE，面板按 lv/src 过滤渲染 |
| 隧道状态灯 / banner | 心跳检测 → `/state.tunnel`（断开时记录视图顶部出 role=alert 横幅 + 重连钮） |
| 灯箱缩略图 | 真实实现用 outputs/ 成片首帧或原图（原型为 SVG 占位） |

实测参考值（写死进演示数据、实现后由真实数据替换）：PDD 4 步 47s ≈ 0.11 CU/条、8 步 103s ≈ 0.22 CU、A100 5.4 CU/h、T4 1.15、L4 1.67（**L4 仅实验用，不作视频生产档**）。

## 实现注意（原型阶段真实踩过的坑）

1. `<dialog>` 显式 `margin:auto`（见上）
2. `html` 与 `body` 都设背景色，否则 audit 对比度采样走到白底误报
3. 严禁 `<button>` 嵌 `<button>`（HTML 解析器会强制闭合并逐层逃逸，整页布局崩）——可点卡片内若要放小按钮，卡片用 `div role="button" tabindex="0"` 或拆结构
4. 字号下限 12px（正文 13–14px）；CJK 小字 `line-height ≥1.5`
5. 网格列写 `minmax(0,1fr)` 不写裸 `1fr`（min:auto 会让 760px 表格撑破布局）
6. 进度条用 `transform:scaleX`，不用 width
7. 状态确认类动画 ≤200ms，进场 transform-only（opacity 动画会污染对比度采样）
8. **终端样式日志必须保留**（用户点名）；**不回做出片表单**（agent-first 边界）
9. QA 基线：`design audit` 双视口必修 0 才算完；改动后 browser-use 至少走查 起机/关机确认/重连/筛选/灯箱/⌘ 主链
