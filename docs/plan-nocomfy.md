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
运行层（Colab GPU）:  bootstrap(装 DiffSynth) → HF 直连下载权重 → 常驻 server
                       server: MiniMaxH3Pipeline.from_pretrained() 加载进显存 → FIFO job 队列
                       session.json 记录四阶段时间戳；产出本地即时下载（不变）
生图线（T4/L4）:      ComfyUI 现状原样保留（zimage/qwen21t 档案不动）
```

### API 设计（server_h3.py，~100 行 FastAPI）

| 端点 | 作用 |
|---|---|
| `POST /generate` | `{mode, prompt, seed, seconds, width, height, fps, keyframes, refs}`；单卡 FIFO 排队 |
| `GET /status?job_id` | 状态 + 进度 |
| `GET /result?job_id` | 成片文件列表（下载沿用现有产出目录逻辑） |
| `POST /upload` | 首/尾帧、参考图（对齐现有 `colab upload` 用法） |
| `GET /health` | 探活（重启循环用；READY 判据从 ComfyUI /health 换成此端点） |

- **崩溃恢复**：server 挂 → 权重已在盘 → 重启只重载模型（~1-2min），不重下载；守护逻辑降级为简单 while-restart 循环
- **批量模式**：`--prompts file` 一条命令排队 N 条，适合生产批次
- **逐 job 计时**：每条记录提交/首帧/完成时间，回填到 session.json（成本与产能数据自动积累）

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
| `colab/bootstrap_h3.py` | 拆双运行时：视频档案 → 装 DiffSynth + 起 server；生图档案 → ComfyUI 现状不动 |
| `colab/server_h3.py` | **新增**：DiffSynth pipeline 加载 + FastAPI 端点 + job 队列 + session.json 时间戳 |
| `scripts/h3_generate.py` | 改造：去 `--workflow/--set`，改 `--mode/--prompt/--seed/--seconds/--name`；走新 API |
| `scripts/h3_up.sh / h3_down.sh / h3_status.sh` | 小改：READY 判据换 `/health`，其余不动 |
| `workflows/t2v_api.json` 等视频工作流 | 废弃（留分支历史）；生图 workflows 保留 |
| `docs/plan-nocomfy.md` | 本文档；各 Phase 完成后回填实测数据 |

## 里程碑与预算

| Phase | 内容 | 产出 | CU 预估 |
|---|---|---|---|
| D1 | bootstrap 双运行时改造 + server 骨架 + **T2V 打通**（蒸馏 INT8 兼容性验证在此） | 首条 DiffSynth 出片 | ~1.5 |
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
