# Changelog

所有显著变更记录于此。格式参考 Keep a Changelog。

## [0.1.1] - 2026-11-22

> 内部开发线 v0.3 / v0.4 合入（分支 v0.3 · commits 737d09b..本版）。视频三模式 + 生图 + 编辑全通。

### 架构（v0.4 官方对齐 · 三层）

- **三层架构**：VM 生命周期 / 方案按需加载 / 生成 各自独立——`h3ctl vm`（裸 VM profile=none）、
  `h3ctl load`（HF 增量只拉缺失 + symlink + ComfyUI 重扫 + 外壳换模板表）、`h3ctl gen`（契约不变）；
  `h3ctl up` = vm+load 糖。热切换实测 1m39s（增量 24GB），全新冷启动 ~13min
- **站点矩阵 v0.4**：官方三教程（minimax-h3-native/fastvideo/pika-h3-flex）逐文件对齐——
  共享底座（TE+双VAE int8）+ 双底模（fl2va/ref2va pruned INT8）+ LoRA 抽屉（官方 turbo 3 件 + Pika 2 件）
  + 生图配方 h3image（TE 与视频栈同文件，共享省 ~25GB）；移除 h3video(FastH3 v2)/h3pdd(PDD)；
  **默认档案 h3pika**；机型×档案 gate 硬校验（A100-40GB 为 D1' 实测生产机，门槛 34GB）
- **配方机制**：`spec.recipe` → 模板表键 `mode@recipe`（同档案多 LoRA/采样变体，零下载切换）
- **新契约 mode=t2i / edit**：生图 6步 CFG-free 免 LoRA；编辑同 checkpoint（参考图入 conditioning，
  源图先压 ~1024——16MP 原生画布编辑在 40GB 卡十分钟级）

### h3ctl（Go 控制端 v0.1.1）

- 命令：stations/up/status/tunnel/gen/down/switch/load/loaded/logs；单二进制纯 stdlib
- 日志体系：模块化分级（中文+emoji）、colab 长命令 15s 心跳、轮询心跳前置、VM 权重下载 20s 落盘进度、
  `logs` 命令 VM 五日志直出（--follow 增量）
- 守卫三件套：guardPositional（Go flag 位置参数陷阱）/validSession/--dry-run；建 VM 503 自动重试+--wait 抢位
- 隧道 pidfile 管理（单会话双 -L 8188+8189）；**控制台由 ComfyUI 原生界面透传替代**（serve 命令与原型页移除）

### 外壳 h3api

- 按档案选模板表 + recipe 键 + t2i/edit 契约；inject 对未暴露参数容错
- **poll 线程异常保护 + 僵尸 running 20min 兜底**（ComfyUI 重启窗口内查询失败曾致线程静默死亡，
  所有 job 永远 queued 收不到结果——实测踩中后修复）

### 实测数据（docs/test-log.md · A100-40GB · 热跑口径）

- **视频路由终表**：t2v pika 102s ⭐ / fl2v 官方 turbo4 768p 92s ⭐ / ref2v pika 107s ⭐（turbo4 134s ≈ PDD 131s 打平，
  4 步省不回 ~84s 参考开销墙）
- **生图**：t2i 1024² 95s（6步免 LoRA）/ 编辑 16s（缓存命中）；文生图+编辑+16 参考能力确认
- 成本：热跑 0.14-0.20 CU/条；全新冷启动 ~1.2 CU；换配方 ~0.1 CU
- 量产验证：素材清单 9 条 pika t2v 一批 18 分钟（续跑稳定 1m47s/条）

### 整理（用户指令：全项目重新整理）

- **docs 分文件夹**：`guides/`（agent-manual/manual）、`data/`（test-log/cost/credits/setup-log）、
  `archive/`（qwen21t 报告）+ `docs/README.md` 索引
- **异家族生图线退役待重做**（用户拍板，统一走 H3 生图 h3image）：qwen21t×5+zimage×1 模板归档
  `workflows/archived/`，stations/h3_up/README 引用同步，bootstrap 档案定义保留（复活即用），
  samples 异家族样片删除
- workflows 清死文件；colab/scripts 退役代码归档（各自 archived/ 带说明）；samples 重建为 v0.4 产出

### 移除

- FastH3 v2 / PDD 全套（档案/模板/文档引用）；`docs/` 旧调研与规划 6 篇（panel-design/research×3/
  plan-nocomfy/plan-qwen21t/test-report——git 历史留存）；`原型/H3控制台.html`；`h3ctl serve`+console.html


## [0.2.1] - 2026-11-21（未发布 · 内部迭代，tag 已撤——该线 qwen21t 已于 0.1.1 退役待重做）

新增 **Qwen-Image-2.1-Turbo 生图编辑档案**（`qwen21t`），三模型多档架构成型。

### 新增

- **`qwen21t` 权重档案**：Qwen-Image-2.1-Turbo INT8 convrot（7.26GB DiT）+ qwen3vl_8b INT8 编码器（9.35GB）+ VAE（0.68GB），单仓库 Comfy-Org 重打包
  - `workflows/qwen21t_t2i_api.json`：`TextEncodeQwenImage21` 双输出（正/负条件一次编码）+ `images` autogrow 参考图入口（16 张）
  - **L4 生产机型实测**：首张 32s（含全量装载）/ 续出 **16s** / **原生 2K 42s**；单张 ≈ 0.0074 CU ≈ **¥0.005（比 API 便宜 20×）**
  - T4 对照实测：69s/张 ¥0.016（可跑但显存满载触发权重流送，慢 4.3×）
  - **图生图三形态全部验证**：指令编辑（CFG3 甜点档）、指哪改哪（颜色标记+CFG1 结构化指代）、参考图合成（人物+场景 `<image1>/<image2>`，16s 电影级）
  - **原生 RGBA 验证**：VAE 四通道直出（T2I 透明背景 16s / 改图抠图 11s）；`EmptyQwenImageLayeredLatentImage` 属于配套分层模型而非 2.1 路径（坑⑲认知修正）
  - 专门测试报告 `docs/test-report-qwen21t.md`（对比表 + 每案例配方）
  - 样例库 `samples/qwen21t/` 按场景分目录（t2i/edit/mask_edit/compose/rgba），img2img 案例均含原图，逐案例可复现
- **bootstrap 多仓库 HF 兜底**：`hf` 支持单仓库或 `[(repo, patterns), …]` 列表
- **守护循环防误杀**（坑⑰）：健康检查 60s 超时 + 连续 2 次失败才重启 + 重启后 90s 装载宽限（此前模型装载期被 3s 检查误杀 8 轮）
- **classify 规范子目录优先**（坑⑱）：HF 仓库自带 `diffusion_models/` 等子目录时直接采信，不再被 `qwen` 等关键字把 DiT 误归 text_encoders
- **样片** `samples/qwen21t/`（T4/L4/中文海报/2K 四张含复现参数）

### 选型记录

- Uncensored 调研结论：DiT 级真去审核（Noct-Q V4 25 步 / chatrlabs 35 步）只存在于基础版血统；
  AtomicChat "Turbo-Uncensored" 仅改文本编码器，实测 45 敏感提示词图像零变化 → **官方 Turbo INT8 即其功能等价物**（Noct-Q 曾实测 T4 712s/张贵过 API，弃）

### 移除

- **Google Drive 整体移除**：权重源改为 HF 优先（CDN 全区 52~156MB/s，实测远超跨区 GCS 13~25MB/s）+ GCS 兜底；
  产出本地即时下载。收益：冷启动免授权弹窗免挂载（根除 drivemount 挂死，坑㉑类故障清零），
  启动序列 6→5 步；代价：产出仅本地（用户目录有 iCloud 同步）

## [0.2.0] - 2026-11-21（未发布 · 内部迭代，tag 已撤）

管线从"H3 视频专用"升级为**多模型档案架构**，新增 AI 生图能力（Z-Image-Turbo @ T4）。

### 新增

- **权重档案机制**（`bootstrap_h3.py` `MODEL_PROFILES`）：`config.json` 的 `weights_profile` 决定拉哪套权重、放哪个子目录；视频 `h3video` 与生图 `zimage` 互不干扰，后续加模型（如 H3-Image）只需新增档案
- **Z-Image-Turbo 生图全链路**（阿里通义 6B，Apache-2.0 可商用，8 步无 CFG）
  - `workflows/zimage_t2i_api.json`（CLIPLoader type=lumina2 + ModelSamplingAuraFlow shift=3 + res_multistep/simple）
  - T4 实测：首张 127s（含模型加载）/ 续出 **61s/张**；中英文字渲染验证通过（海报级）
  - 权重 20.7GB 三级源齐备：HF 拉取 5.4 分钟 → Drive 回写 → **GCS 种子完成**（CPU 会话 0.08 CU/h 上传 9 分钟 @37MB/s，匿名 200 验证通过，冷启动预期 ~4 分钟）
- **机型参数化**：`h3_up.sh` 支持 `MACHINE=T4/L4`（T4 实测 1.15 CU/h，生图不必动 A100）与 `WEIGHTS=zimage`
- **生图样片与文档**：`samples/zimage/`（宇航猫 + 中文海报两张，含可复现参数）、生图选型调研 `docs/research-image.md`、**成本对比** `docs/cost-analysis.md`（自托管 vs API：视频 ~1/12、生图 ~1/7）

### 修复

- 坑⑯：HF 快照保留仓库 `split_files/` 目录层级 → symlink 带前缀导致 ComfyUI 找不到模型；现在 symlink 时自动剥前缀，三源（GCS/Drive/HF）文件名归一
- `gcs_setup.py` 支持外部 `upload_spec.json` 覆盖上传源（为任意权重档案做 GCS 种子）

### 实测指标（Z-Image @ T4）

| 指标 | 数值 |
|---|---|
| 出图（首张 / 续出） | 127 s / **61 s** |
| T4 费率 | 1.15 CU/h（本次全流程验证共 0.35 CU） |
| 权重拉取 | 19.3GB @ ~52MB/s（HF 源，5.4 分钟） |
| 冷启动（分配→就绪） | ~9.5 分钟（含 Drive 回写 3.3 分钟） |

## [0.1.0] - 2026-11-21

首个可用版本：在 Google Colab（AI Pro）上运行 MiniMax H3 视频生成的完整生产管线。

### 新增

- **三种生成模式全部验证通过**
  - T2V 文生视频（`workflows/t2v_api.json`）
  - FL2VA 首尾帧（`workflows/fl2v_api.json`，AI 生成静帧 → 连贯过渡视频）
  - Ref2VA 全能参考（`workflows/ref2v_api.json`，多参考图 autogrow 注入，首发即中）
- **一键运维脚本**（`scripts/`）
  - `h3_up.sh`：拉起工作站（标准 A100 默认，HIGHMEM=1 切 80GB；EXPOSE=cloudflared 备用出口）
  - `h3_up_wait.sh`：高峰期 A100 抢位循环（坑⑮：高峰 503 是容量问题）
  - `h3_tunnel.sh`：SSH 隧道（公钥经 X-Colab-Ssh-Pubkey 注册，root + 私钥）
  - `h3_generate.py`：出片客户端（`--set 节点.键=值` 注入 / 队列 / 轮询 / 自动下载）
  - `h3_status.sh` / `h3_usage.sh` / `h3_down.sh`
- **权重三级源**（`colab/bootstrap_h3.py` 自动选择，均断点续传）
  - GCS 公开读桶（实测 121MB/s 跨区，冷启动拉取 4~8 分钟，并行下载）
  - Google Drive（并行 rsync + append-verify）
  - HuggingFace（`allow_patterns` 限定 4 文件）
- **fire-and-poll 引导架构**：colab-cli exec 长阻塞必断连（坑⑩），重活全在 VM 后台线程，exec 只提交/轮询
- **产出自动归档**：VM 守护循环每 60s 回写 Drive；出片脚本即时下载本地
- **Google Drive 权重持久化 + GCS 加速**（`colab/gcs_setup.py`、`download_to_drive.py`）
- **交接文档全套**：`docs/manual.md`（使用手册）、`test-report.md`（实测数据）、`setup-log.md`（坑①-⑮）、`research.md`、`credits.md`（引用全景）、`agent-manual.md`
- **开源脚手架**：LICENSE（Apache-2.0，不含权重）、`.gitignore`、`samples/`（可复现样片）

### 实测指标

| 指标 | 数值 |
|---|---|
| 出片（会话内首条 / 续出） | 3.5 min / ~2.9 min |
| 冷启动（分配→就绪，GCS 源） | ~13 min（setup 本体 8.5 min） |
| 权重拉取 | 55.08GB @ ~121MB/s |
| 单条成本 | ~0.3 CU（标准 A100）/ ~0.5 CU（High-RAM） |

### 已知限制

- 2K 再生成与 Context-IR 未开源（仅 MiniMax 官方 API）
- 标准 A100 高峰期容量不足（503），需抢位或降级 High-RAM
- Colab 会话最长 24h、闲置 ~180 分钟回收 → 批次制工作
- 每个 VM 的 Drive 授权页会弹一次（自动完成，Google 侧同意已记住）
- NVFP4 量化仅限 Blackwell 机型；本管线 INT8 面向 Ampere（A100）
