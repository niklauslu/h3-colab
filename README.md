# H3 · MiniMax H3（海螺 3.0）Colab 生产管线

![version](https://img.shields.io/badge/version-0.1.0-blue) ![license](https://img.shields.io/badge/license-Apache--2.0-green) ![platform](https://img.shields.io/badge/platform-Google%20Colab-lightgrey)

**v0.1.0**（2026-11-21）· 变更记录见 [docs/CHANGELOG.md](docs/CHANGELOG.md)

在 Google Colab（AI Pro 订阅，标准 A100 / 每月 100 CU）上部署开源的 MiniMax H3
视频/图像生成模型，实现：**权重 HF/GCS 双源、agent 全程接管、视频 ~3 分钟/条、生图秒级** 的生产速度。

> 📖 **接手必读：[`docs/manual.md`](docs/manual.md)（详细使用手册）**——架构、SOP、工作流参考、故障排查、成本模型。
> 搭建过程中的坑①-⑭实录在 [`docs/setup-log.md`](docs/setup-log.md)。

## 架构

```
控制层（本机 macOS）:  google-colab-cli（Bash 直调）→ SSH 隧道 → ComfyUI HTTP API
运行层（Colab GPU）:  ComfyUI + 各模型档案 + 守护循环（自动重启）；产出本地即时下载
存储层（权重双源）:  ① HuggingFace CDN（Colab 全区 52~156MB/s，主源）
                       ② GCS 公开读桶（同区快/跨区兜底；断点续传）
```

设计原则：fire-and-poll（exec 只提交/轮询，重活在 VM 后台线程）、不用 Drive（免授权弹窗免挂载故障）、
ComfyUI 只绑 127.0.0.1 零公网暴露、A100 用 INT8（NVFP4 仅 Blackwell）。

## 快速开始

```bash
# 0) 一次性准备（详见 docs/manual.md §2）
uv tool install google-colab-cli && export PATH="$HOME/.local/bin:$PATH"
colab usage            # 首次触发 OAuth（授权页权限全勾，授权码贴回终端）

# 1) 开工
bash scripts/h3_up.sh          # 冷启动 3~15 分钟，自动到 READY
bash scripts/h3_tunnel.sh      # 隧道（前台挂着）

# 2) 出片（T2V）
python3 scripts/h3_generate.py \
  --workflow workflows/t2v_api.json \
  --set "5.text=一只橘猫在雪地里奔跑，电影感，黄昏光" \
  --name demo

# 3) 首尾帧（FL2VA）：先上传首/尾帧图，再走 fl2v_api.json
colab upload -s h3 inputs/frame_first.jpg /content/ComfyUI/input/frame_first.jpg
python3 scripts/h3_generate.py --workflow workflows/fl2v_api.json \
  --set "9.prompt=镜头缓推，电影感" --name fl2v

# 4) 生图（Z-Image-Turbo，T4/L4 便宜档，不动视频站）
MACHINE=T4 WEIGHTS=zimage bash scripts/h3_up.sh h3img
python3 scripts/h3_generate.py --workflow workflows/zimage_t2i_api.json \
  --set "4.text=一只橘猫宇航员，电影感" --name zimg

# 5) 收工（务必！A100 挂机也烧 5.4 CU/h）
bash scripts/h3_down.sh
```

工作流 JSON 已自建（`workflows/t2v_api.json`、`fl2v_api.json`），无需手动导出；
注入参数用 `--set 节点.键=值`，速查表见 manual 附录 C。

## 性能与成本（实测）

| 指标 | 数值 |
|---|---|
| 出片（会话内首条，含模型加载） | ~3.5 分钟 |
| 出片（续出每条） | ~2.9 分钟 |
| 节点缓存重交 | 秒级 |
| 产能 | ≈ 0.24~0.4 CU/条；100 CU/月 ≈ 150~260 条 |
| 冷启动 | HF 源 ~4-6 分钟（实测 T4 全程 6.2 分钟）|

机型：**标准 A100 5.4 CU/h**（视频生产默认）；**T4 1.15 CU/h 实测**（生图档，`MACHINE=T4`）；
L4 1.67 CU/h；High-RAM 8.88 CU/h（~80GB，HIGHMEM=1 切换）；NVFP4 只适用 Blackwell。
⚠️ 机型挂机都烧 CU，批次结束即 `h3_down.sh`。

生图（Z-Image-Turbo @ T4）：首张 127s（含加载）/ **续出 61s/张**，中英文字渲染准确（Apache-2.0 可商用）。

## 文件地图

| 文件 | 说明 |
|---|---|
| **`docs/manual.md`** | **详细使用手册（交接必读）** |
| **`docs/test-report.md`** | **测试报告：T2V / FL2VA / Ref2VA / 冷启动全部实测数据** |
| **`docs/credits.md`** | **引用项目与致谢（核心依赖 / 辅助 / 选型参考）** |
| `docs/research.md` | 调研留档：模型情况、方案对比、速度基准、全部来源链接 |
| `docs/setup-log.md` | 搭建实录 + 坑①-⑮（含修复方式） |
| `docs/agent-manual.md` | 给 AI agent 的指令模板 |
| `colab/bootstrap_h3.py` | VM 端引导（fire-and-poll：装环境 / GCS 三级权重 / 守护循环） |
| `colab/gcs_setup.py` | GCS 建桶 + 上传工具（一次性） |
| `scripts/h3_up.sh` / `h3_up_wait.sh` / `h3_tunnel.sh` | 开工 / 高峰抢位循环 / 隧道 |
| `scripts/h3_generate.py` | 出片（API 排队 → 轮询 → 下载成片） |
| `scripts/h3_status.sh` / `h3_usage.sh` / `h3_down.sh` | 状态 / 额度 / 收工 |
| `workflows/t2v_api.json` · `fl2v_api.json` · `ref2v_api.json` · `zimage_t2i_api.json` | 出片/生图工作流（API 格式，直接可用） |
| `samples/` | 测试样片 + 输入素材 + 可复现参数（开源展示用） |
| `LICENSE` | Apache-2.0（不含权重，权重归 MiniMax Community License） |
| `inputs/` · `outputs/` | 输入素材 · 本地成片（outputs 不入库） |

## 注意事项

- colab-cli 仅支持 macOS/Linux；exec 会随机断连——一切重活已设计为 VM 后台线程，断连重试即可（坑⑩）
- HF 社区许可证：美/欧/英/韩部署需提交申请表；模型内置内容审核
- 2K 再生成（H3-Regenerate-2K）与 Context-IR 未开源，需 2K 走官方 API（platform.minimax.io）
- 当前权重仓库不 gated，无需 HF token；换 gated 权重时 token 放 `colab/hf_token.txt`（勿外传）
