# 落地规划：Qwen-Image-2.1-Turbo 档案（qwen21t）

> 制定：2026-11-20 · 执行：2026-11-21 · 目标版本 **v0.2.1** · 预算 ~2 CU（余额 181.82）· 工时两次会话（T4 + L4 各 ~30-40 分钟）
> 决策口径：**官方权重先行**（管线兼容、零自定义节点），Uncensored-GGUF 作为可选 A/B 步用数据定去留。

## 背景要点（详见 research-image.md）

- 7B MMDiT，T2I + 指令编辑 + RGBA 原生 + 16 参考图 + 原生 2K——**选它的核心动机是编辑/RGBA 能力**（Z-Image 只有 T2I）
- 许可证：Qwen Research License **仅限非商用**（产出物商用前须解决授权；开源仓库展示无碍）

## 选型定案（2026-11-20 用户拍板：直接用 Uncensored 版）

Uncensored 版本社区调研结论（详见对话记录）：

| 版本 | 去审核层次 | 步数 | ComfyUI | 结论 |
|---|---|---|---|---|
| **Noct-Q V4 INT8**（Noctaluna，25.6 万下载）⭐ | **DiT 权重级（真有效）** | 25 步 CFG3（CFG1 快一倍） | 原生核心节点，int8 单文件 7.3GB（8-12GB 卡档） | **主选** |
| chatrlabs Omni-Quantized INT8 | DiT LoRA 合并（真，+4.6 分） | 35 步 CFG5 | 原生节点 0.39+ | 备选 |
| AtomicChat Turbo-Uncensored | ❌ 只改文本编码器（实测 45 提示词 0 变化） | 8 步 | 需 leejet fork | 淘汰 |

- "Turbo 8 步 + 真去审核"组合社区暂不存在——**接受 25 步的时间代价**（T4 估 2~4 min/张 ≈ 0.05~0.07 CU ≈ ¥0.04~0.05，仍低于 API ¥0.108）
- Noct-Q 权重清单：`models/diffusion_models` Noct-Q V4 int8 convrot 7.3GB + `models/text_encoders` qwen3vl_8b int8 convrot + 官方 VAE；ComfyUI ≥0.37，仓库附 `NoctQ_V4_workflow.json`（转 API 格式即可）
- 特性注意：25 步基座 ≠ Turbo——编辑/RGBA/16 参考仍可用（Noct-Q 官方说明同一文件可用于 Edit 模板），但 8 步加速特性放弃

## Phase 0 · 准备（本地，0 CU）

| # | 任务 | 产出 |
|---|---|---|
| 0.1 | 核对 Noct-Q 仓库文件树（DiT int8 7.3GB / 编码器 qwen3vl_8b int8 convrot / VAE 来源）并下载 `NoctQ_V4_workflow.json` | 权重清单定稿 |
| 0.2 | `bootstrap_h3.py` 加 `qwen21t` 档案（Noct-Q INT8 清单；L4 用同文件——int8 本身就是低显存档，无需双清单） | MODEL_PROFILES 新条目 |
| 0.3 | `workflows/qwen21t_t2i_api.json`（由 NoctQ_V4 工作流转 API 格式；预期 25 步 euler/simple CFG3，CFG1 做对照） | 注入点待运行时确认 |

## Phase 1 · T4 首测 ✅（2026-11-21 实测完成）

- **中段改道**：先跑 Noct-Q V4（25步 CFG3）——画质好、中文渲染好，但 **T4 纯推理 712s/张 ≈ 0.23 CU ≈ ¥0.16，贵过 API**；且首次模型装载触发守护循环 3s 健康检查误杀（坑⑰，已修：60s 超时+连续2次失败+90s 装载宽限）；symlink 又踩坑⑱（classify 的 "qwen" 关键字把 DiT 误归 text_encoders，已修：优先采信规范子目录）
- **用户拍板改 Turbo 官方 INT8**（8 步 CFG1；"Turbo+真去审核"社区不存在，AtomicChat 式编码器去审核实测图像零变化，故官方 Turbo 即其功能等价物；abenzerps 同款 INT8 留作备用源）
- **Turbo INT8 实测**：首张 91s（含 DiT 换载）/ 续出 **69s** ≈ 0.022 CU ≈ **¥0.016/张**（比 API ¥0.108 便宜 6.8×）；中文海报渲染完美；画质与 25 步版同级
- T4 会话全程（冷启动+4图+调试）共 1.09 CU

## Phase 2 · L4 对照测 ✅（2026-11-21 实测完成）

同 Turbo INT8 文件双机对照（唯一变量=机器）：

| 指标 | T4（15GB） | **L4（24GB）** |
|---|---|---|
| 首张（含全量装载 17.3GB） | 91s | **32s** |
| 续出（纯推理） | 69s | **16s** |
| 单张成本 | 0.022 CU ≈ ¥0.016 | **0.0074 CU ≈ ¥0.005** |
| 2K（2048²） | — | **42s** |

**结论：L4 定为 qwen21t 生产机型**——时薪贵 45% 但快 4.3×，每张便宜 3×；
24GB 显存全量常驻无 offload 是关键（T4 塞满后权重流送拖慢）。
画质/中文渲染两机一致。Phase 3 的 2K 已顺带验证 ✅（Edit/RGBA/16 参考留待后续会话）。

## Phase 3 · 特性扩展验证（~0.5 CU，穿插在上两会话）

选 Qwen 的核心理由逐个过：

1. **指令编辑工作流**（`qwen21t_edit_api.json`）——Z-Image 没有的能力
2. **RGBA 透明通道输出**（抠图/合成场景）
3. 16 张参考图注入（V2V 类参考）
4. 2K（2048²）出图——L4 上测（T4 大概率 OOM，分块解码兜底）

## Phase 4 ·（可选）chatrlabs 对照测

- 若 Noct-Q 质量不达预期：换 chatrlabs Omni INT8-comfy（同为原生节点，只换权重文件）
- AtomicChat（Turbo + 编码器去对齐）已淘汰：实测图像层 0 变化

## Phase 5 · 归档（v0.3.0）

- 样片 `samples/qwen21t/`（文字渲染 / 编辑 / RGBA 各一，含复现参数）
- `cost-analysis.md` 补 Qwen 行；`test-report.md` 补实测；`research-image.md` 状态更新"已落地"
- commit 策略：当前工作区改动（隧道 PORT / 成本文件 / GCS 收尾文档）并入 v0.2.0 补充提交；Qwen 改动单独 commit + tag **v0.2.1**（推送等用户指令）

## 风险清单

| 风险 | 概率 | 预案 |
|---|---|---|
| 25 步基座速度低于预期（T4 >5min/张） | 中 | CFG1 减半步数实测；不行转 L4（快 2 倍+） |
| Noct-Q 编码器/VAE 文件链接变动 | 低 | 编码器可换官方 qwen3vl int8 convrot，VAE 用 Comfy-Org |
| Edit/RGBA 工作流节点与预想不符 | 低 | 运行时从 /object_info 现建（同视频流程） |
| T4 显存峰值超 15GB | 低 | 官方标 8-12GB 档；分块解码兜底 |
