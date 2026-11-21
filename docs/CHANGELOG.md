# Changelog

所有显著变更记录于此。格式参考 Keep a Changelog；版本号遵循语义化版本。

## [0.2.1] - 2026-11-21

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

## [0.2.0] - 2026-11-21

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
