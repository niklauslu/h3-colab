# Changelog

所有显著变更记录于此。格式参考 Keep a Changelog；版本号遵循语义化版本。

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
