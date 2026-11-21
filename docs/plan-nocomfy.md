# 落地规划：去 ComfyUI 常驻 Server 视频管线（H3 视频线）

> 制定：2026-11-22 · 执行：待排期 · 目标版本 **v0.3.0（候选）** · 分支 **v0.3**（自 v0.2 切出）
> 决策口径：视频线迁 **DiffSynth-Studio 常驻 server**；生图线（zimage/qwen21t）**留 ComfyUI 不动**；
> v0.2 分支作为 ComfyUI 旧版兜底原样保留，新线在 v0.3 上做，质量对比定案后再决定合并去向。
> 四条硬性要求（2026-11-22 拍板）：启动作为一个操作 / 记录启动时间 / 优先直接下载 / 多次生成只传参数。

## 背景要点（调研结论，2026-11-22）

- ComfyUI 只是官方四条推理路径之一（SGLang / vLLM / **diffusers** / ComfyUI），非必需；SGLang/vLLM 官方示例要 4 GPU，Colab 单卡不适合
- 现有 ~3min/条 的速度来自 **FastH3 8-step 蒸馏权重 + INT8**，不是 ComfyUI 本身；换框架的硬前提是蒸馏加速能保留（base 模型 50 步会慢 ~6×）
- **DiffSynth-Studio**（ModelScope）：
  - `MiniMaxH3Pipeline` 纯 Python ~30 行；T2VA / FL2VA（首尾帧）/ Ref2VA（参考）三模式 + 原生音频全支持
  - 支持 **Comfy-Org INT8 convrot 权重**（`pip install "diffsynth[quant]"`，comfy-kitchen 后端）与 **NF4**（官方量化包 `DiffSynth-Studio/MiniMax-H3-NF4`，最低 ~7GB 显存，offload/tiled VAE 自动管理）
  - 支持 **LoRA 训练**（pruned 模型单卡单阶段）——与 v0.3 路线图的 LoRA 规划天然协同
- 备选：**FastVideo**（现用 FastH3 权重的娘家，`VideoGenerator` API，零质量回归但框架依赖更重）；**LightX2V**（turbo 4-step，比现 8-step 少一半，理论 ~1.5min/条，质量待验证，作为后续加速项）
- 不用换的：`--set "5.text=..."` 节点寻址、`workflows/*.json` 节点图、守护循环坑⑰那类问题——新管线里整类消失

## 目标 UX（四条硬性要求 → 落法）

| 要求 | 落法 |
|---|---|
| 启动作为一个操作 | `h3_up.sh` 外层 UX 不变；内部：装 DiffSynth → HF 直连下载权重 → 起常驻 server → READY |
| 记录启动时间 | `session.json` 记 vm_at / deps_at / weights_at（含字节数、续传与否）/ ready_at 四阶段时间戳；up 结束打印分解 + CU 成本换算（A100 5.4 CU/h × 会话时长） |
| 优先直接下载 | HF `snapshot_download` 主源（断点续传 + 并行）；GCS 仅兜底；Drive 不回归 |
| 多次生成只传参数 | 常驻 server 模型加载进显存后待命；`h3_generate.py` 只传 `--mode/--prompt/--seed/--seconds` 等普通参数；模型不重载，续出每条 ≈ 纯推理时间 |

## 架构（v0.3 视频线）

```
控制层（本机 macOS）:  google-colab-cli（不变）→ SSH 隧道 → server HTTP API
运行层（Colab GPU）:  常驻 server（纯 stdlib 先起，零依赖，装 DiffSynth 前就能跑）
                       ├─ 子进程编排: pip install DiffSynth → HF snapshot_download（进度→SSE 事件）
                       ├─ 同进程加载 MiniMaxH3Pipeline 进显存 → READY（时间戳由 server 统一写 session.json）
                       └─ HTTP 端点 + FIFO job 队列 + SSE /events；产出本地即时下载（不变）
生图线（T4/L4）:      ComfyUI 现状原样保留（zimage/qwen21t 档案不动）
```

### API 设计（server_h3.py，纯 stdlib ~150 行，零第三方依赖）

| 端点 | 作用 |
|---|---|
| `POST /generate` | `{mode, prompt, seed, seconds, width, height, fps, keyframes, refs}`；单卡 FIFO 排队 |
| `GET /status?job_id` | 单 job 状态 + 进度 |
| `GET /result?job_id` | 成片文件列表（下载沿用现有产出目录逻辑） |
| `POST /cancel?job_id` | 取消排队/进行中的 job（POST 即可，无需双向通道） |
| `POST /upload` | 首/尾帧、参考图（对齐现有 `colab upload` 用法） |
| `GET /state` | **全量快照**：会话状态、session.json、job 队列与历史（重连即取） |
| `GET /events` | **SSE 事件流**：启动阶段 / job 进度 / 日志尾部，事件驱动推送 |
| `GET /health` | 探活（重启循环用；READY 判据从 ComfyUI /health 换成此端点） |

- **崩溃恢复**：server 挂 → 权重已在盘 → 重启只重载模型（~1-2min），不重下载；守护逻辑降级为简单 while-restart 循环
- **批量模式**：`--prompts file` 一条命令排队 N 条，适合生产批次
- **逐 job 计时**：每条记录提交/首帧/完成时间，回填到 session.json（成本与产能数据自动积累）

### 可视化面板数据通道（v0.3 预埋，面板直接吃）

**结论：细粒度数据不用轮询——SSE 推送（快照 + 流）；轮询只留给本来就只能拉的源。**

| 数据 | 来源 | 通道 |
|---|---|---|
| 启动阶段进度（deps / 权重下载 % / 模型加载） | server | `/events` SSE，事件驱动 |
| 生成 job 进度（排队 / 步数 / 完成 / 逐 job 计时） | server | `/events` SSE（步级事件节流 ≤1s，防高频重绘） |
| 会话全量快照（状态、session.json、CU 估算） | server | `GET /state` 连接即取 + SSE 心跳 |
| VM 生命周期（up/down/错）、CU 余量 | colab-cli | 面板本地低频轮询 30-60s（Colab 侧无推送源，拉取不可避免） |

- **为什么 SSE 不用 WebSocket**：单向推送为主；SSE 就是普通 HTTP，过 SSH 隧道零配置；浏览器 `EventSource` 原生自带断线重连 + `Last-Event-ID`；取消任务用 `POST /cancel` 即可，不需要双向通道。真出现双向需求（交互式预览帧等）再升 WebSocket，端口与安全姿态不变（仍绑 127.0.0.1）
- **快照 + 流**（防断线"卡在旧状态"）：连上先 `GET /state` 拿全量，再订阅 `/events` 收增量；断线重连 = 重新快照 + 续流，SSE 偶断也不丢状态
- **启动归属反转（关键架构决定）**：把「启动」从 bootstrap 脚本收编进 server 进程——server 纯 stdlib 先起来，子进程跑 pip install / 权重下载（progress 回调转成 SSE 事件），最后同进程加载模型 → READY。面板从第 0 秒起就有阶段进度流，不再"黑盒等 7-10 分钟"；session.json 由 server 统一写，口径一致。`h3_up.sh` 相应简化为：起 VM → 隧道 → spawn server → 等 READY

### 本地控制后端（v0.4 面板，选型预埋 2026-11-22）

**选型：Python + FastAPI 本地后端 + 纯静态前端（单页 HTML/JS，localhost 服务 + 浏览器打开）**

| 维度 | **Python FastAPI** ⭐ | Node/Hono | Go 单二进制 | Electron/Tauri 起步 |
|---|---|---|---|---|
| 与现有栈一致性 | ✅ 全仓 Python（h3_generate/bootstrap） | ✗ 引入第二语言栈 | ✗ | ✗ |
| colab-cli / ssh 子进程编排 | ✅ subprocess 顺手 | 一般 | 一般 | 需 sidecar |
| SSE 转发 + 隧道反代 | ✅ httpx stream | ✅ | ✅ | — |
| 分发/依赖 | 本机自用，uv 管理零问题 | — | 单二进制（对自用过度工程） | 起步即重 |
| 将来桌面化 | pywebview / Tauri sidecar 套壳，后端不动 | — | — | — |

**架构分层与核心原则：CLI 与面板共用同一本地 lib，不搞两套实现**

```
面板前端（静态 HTML/JS，本地后端 serve）
   │ HTTP / SSE
本地控制后端（Python FastAPI ~200 行，h3ctl 包）
   ├─ 控制: 封装 VM 生命周期（colab-cli 子进程）+ ssh -L 隧道管理
   ├─ 转发: 反代 Colab server /state /events /generate（消费 v0.3 已定接口，不重实现生成逻辑）
   ├─ 落地: 产出文件归档 outputs/
   └─ serve: 面板静态页
scripts/h3_up.sh / h3_generate.py 等 → 退化为 h3ctl 的薄壳（CLI 与面板同源，防逻辑漂移）
```

- 本地后端端点草案：`POST /api/vm/up|down`、`GET /api/vm/status`（轮询 colab-cli）、`POST /api/session/up`、`GET /api/events`（SSE 反代）、`POST /api/generate`、`GET /api/outputs`
- **范围纪律**：本地后端属 v0.4 面板阶段；v0.3 只需保证 Colab 侧 `/state` `/events` 接口稳定（已预埋），本地侧不动。唯一可提前做的：把 scripts 里重复逻辑收编为 `h3ctl` lib（D4 文档回填时顺手，可选）
- **不用 Electron 全家桶起步**：Web 面板够用且轻；真要独立 app 再套 pywebview（Python 全栈最短桌面路径）或 Tauri sidecar，后端零改动

## 权重策略（第一期决策）

- **首选：复用现有 INT8 convrot 文件**（FastH3 8-step pruned DiT + qwen3vl_32b TE INT8 + 双 VAE）——零质量变量、下载量不变、可与 v0.2 输出**同 prompt 同 seed 直接对比**
- **待验证（D1 第一验证项）**：DiffSynth comfy-kitchen 后端能否加载 FastVideo 蒸馏 pruned INT8（命名/结构兼容性未实测）
  - 若不行 → 备选 A：lightx2v turbo 4-step（DiffSynth 文档明确支持，更少步数）
  - 备选 B：NF4 base 50 步（慢 ~6×，不推荐作主路径，仅兜底）
- 第二期：NF4 全套（更小更轻）与 4-step turbo A/B，用数据定去留
- 蒸馏步数对齐：8 步 + `shift_video 12.0 / shift_audio 3.0`（对应现 t2v_api.json 的 MiniMaxH3SigmaShift）

## 文件改动清单（v0.3 分支）

| 文件 | 动作 |
|---|---|
| `colab/bootstrap_h3.py` | 拆双运行时：视频档案 → 启动编排移交 server（装 DiffSynth/下载/加载都在 server 内做）；生图档案 → ComfyUI 现状不动 |
| `colab/server_h3.py` | **新增**：纯 stdlib 常驻 server（启动编排 + 模型加载 + job 队列 + `/events` SSE + `/state` + session.json 时间戳） |
| `scripts/h3_generate.py` | 改造：去 `--workflow/--set`，改 `--mode/--prompt/--seed/--seconds/--name`；走新 API |
| `scripts/h3_up.sh / h3_down.sh / h3_status.sh` | 小改：READY 判据换 `/health`，其余不动 |
| `workflows/t2v_api.json` 等视频工作流 | 废弃（留分支历史）；生图 workflows 保留 |
| `docs/plan-nocomfy.md` | 本文档；各 Phase 完成后回填实测数据 |

## 里程碑与预算

| Phase | 内容 | 产出 | CU 预估 |
|---|---|---|---|
| D1 | bootstrap 双运行时改造 + server 骨架（`/state` + `/events` SSE 一起落，面板预埋）+ **T2V 打通**（蒸馏 INT8 兼容性验证在此） | 首条 DiffSynth 出片 | ~1.5 |
| D2 | **同 prompt 同 seed 对比**（vs v0.2 ComfyUI 输出）+ FL2VA / Ref2VA 打通 + 生图线回归测试 | 质量对比定案 | ~1.5 |
| D3 | 批量模式 + 崩溃恢复演练（kill server → 重启只重载）+ session.json 成本回填 | 三模式全通 | ~1 |
| D4 | 文档回填（README / CHANGELOG / manual）+ 合并决策 | 定稿 | 0 |

预期冷启动：deps ~2min + 权重下载 4-6min + 模型加载 1-2min ≈ **7-10min**（少装 ComfyUI/克隆，与现 3-15min 同级或略优）；续出每条 ≈ 纯推理（与现 ~2.9min 持平，换框架不加价；4-step turbo 若验证通过可降至 ~1.5min）

## 风险清单

1. FastH3 蒸馏 INT8 在 DiffSynth 的兼容性未实测（D1 第一验证项，备选已列）
2. DiffSynth 在 A100 40GB 上 INT8 后端的稳定性；NF4 作显存 fallback
3. 蒸馏 8 步的 sigma shift / scheduler 对齐（对不齐会出现模糊/拖影，对照 v0.2 输出可快速发现）
4. bootstrap 双运行时改动不得影响 zimage/qwen21t 生图线（D2 回归测试硬门槛）
5. 路线图关系：v0.3.0 原规划（可视化面板 / LoRA / 教程）——本迁移与 LoRA 协同（DiffSynth 内置训练），面板/教程顺延；版本号归属待合并决策时定
6. DiffSynth `pipe()` 步级进度回调是否可用（D1 验证项）：有 → `/events` 推真实 step x/N；无 → 以首条实测耗时为基线估算进度（面板够用；较 ComfyUI 真实节点进度属小退化，文档注明）
