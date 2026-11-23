# H3 · MiniMax H3 Colab 生产管线

![version](https://img.shields.io/badge/version-0.1.2-blue) ![license](https://img.shields.io/badge/license-Apache--2.0-green) ![platform](https://img.shields.io/badge/platform-Google%20Colab-lightgrey)

**v0.1.2**（2026-11-22）· 变更记录见 [docs/CHANGELOG.md](docs/CHANGELOG.md)

在 Google Colab（AI Pro 订阅，标准 A100 / 每月 CU 包）上部署开源的 MiniMax H3 视频/图像生成模型：
**站点矩阵配置化启动、方案按需加载、视频三模式 ~100s/条、生图便宜档（L4/T4）~0.01-0.02 CU/条** 的生产管线。

> 📖 **接手必读：[`docs/guides/agent-manual.md`](docs/guides/agent-manual.md)**（agent 契约速查）·
> 详细手册 [`docs/guides/manual.md`](docs/guides/manual.md) · 实测数据 [`docs/data/test-log.md`](docs/data/test-log.md) · 坑实录 [`docs/data/setup-log.md`](docs/data/setup-log.md)

## 架构（v0.1.2 · 三层）

```
层1 VM 生命周期   h3ctl vm <会话>            裸 VM: 建站+引导+ComfyUI+契约外壳(不装权重)
层2 方案按需加载  h3ctl load <档案> [会话]   差异增量下载(HF 只拉缺的)→symlink→重扫→换模板表
层3 生成          h3ctl gen …                统一契约: t2v / fl2v / ref2v / t2i / edit
糖  h3ctl up <档案> [会话]                   = vm + load 一键流
```

- **控制层（本机）**：`h3ctl`（Go 单二进制，纯 stdlib）编排 colab-cli / ssh / HTTP；agent 也可直连隧道+HTTP 契约
- **运行层（Colab A100）**：ComfyUI + 共享底座（TE+双VAE）+ 双底模（fl2va/ref2va）+ LoRA 抽屉 + 守护循环
- **权重**：HF CDN 直连（52~156MB/s），按档案增量；`h3ctl loaded` 查盘上方案
- **控制台**：无需自建——隧道自带 **ComfyUI 原生界面透传** `http://127.0.0.1:8189`

## 站点矩阵（机型×档案）

| 档案 | 机型 | 用途 | 热跑实测 |
|---|---|---|---|
| `h3pika` | A100 | 视频·ref2v 主力（Pika 6步，1-3 张参考，3-15s 官方帧格点） | ref2v 107s ⭐ / t2v 102s ⭐ |
| `h3turbo` | A100 | 视频·官方 turbo 抽屉（t2v 8步 / fl2v 4步 / ref2v 4步）+ Pika 双 LoRA | fl2v 92s ⭐ / t2v 134s / ref2v 134s |
| `h3omni` | A100 | 视频·全能单 DiT（OmniRef，t2v+ref2v） | ref2v 139s |
| `h3image` | A100 | 生图·H3 全模态（6步 CFG-free + 编辑 + 16 参考） | t2i 1024² 95s / edit 16s ⭐ |
| `qwen21t` | L4 | 生图便宜档·Qwen2.1-Turbo 20B int8（8步+编辑+中文文字） | t2i/edit 21s ⭐ ~0.01 CU |
| `zimage` | T4 | 生图最便宜档·Z-Image-Turbo 6B（8步，官方 16GB 可跑） | t2i 热 53s |
| `h3core` | A100 | 共享底座预载（TE+双VAE） | — |
| `h3tao` | A100 | fl2v 实验（TaoMate 3步，挂起） | — |

**任务路由**（速度实测定）：t2v → pika · fl2v → turbo 4步 · ref2v → pika ·
生图/中文文字/编辑 → qwen21t × L4（批量最省）· 备选 h3image × A100 / zimage × T4

## 快速开始

```bash
# 0) 一次性准备
uv tool install google-colab-cli && export PATH="$HOME/.local/bin:$PATH"
colab status -s h3     # 首次触发 OAuth（授权页权限全勾）；余额随时 `colab usage`
brew install go && cd h3ctl && go build -o /usr/local/bin/h3ctl .   # 或 go run . 直接跑

# 1) 开站（一键流: 裸 VM + 按需加载, 全冷 ~13min; 热切换配方 ~1-2min）
h3ctl up h3turbo h3        # = h3ctl vm h3 + h3ctl load h3turbo

# 2) 出片（视频三模式 / 生图, 经隧道 8188; ComfyUI 原生界面在 8189）
h3ctl gen --mode t2v  --prompt "一只橘猫在雪地里奔跑，电影感" --seed 42 --name demo
h3ctl gen --mode fl2v --keyframe first.jpg=0 --keyframe last.jpg=-1 --prompt "…"
h3ctl gen --mode ref2v --ref person.jpg=image --ref scene.jpg=image --prompt "subject_definitions: …"
h3ctl gen --mode t2i  --prompt "…" --width 1024 --height 1024          # 需 load h3image/qwen21t/zimage
h3ctl gen --mode edit --ref source.png=image --prompt "把背景换成…保持主体不变"
h3ctl gen --mode ref2v --width 768 --height 1344 --seconds 12.25 \
    --ref 人物板.jpg=image --ref 女主板.png=image --ref 场景板.jpg=image --prompt "subject_definitions: …"
#   竖屏 9:16 长镜头: 时长 3~15s 任填, 外壳自动对齐官方帧格点 17k+5; ref2v 最多 3 张参考

# 生图便宜档（L4/T4, 单条 ~0.01-0.02 CU, 不占 A100）:
h3ctl up qwen21t h3q21                          # 第二台 VM: --port 8288 --comfy-port 8289 错开隧道
h3ctl gen --port 8288 --mode t2i --prompt "海报「雪夜归人」…"   # 中文文字渲染是强项
h3ctl gen --port 8288 --mode edit --ref 人.png=image --prompt "Remove the background, output a PNG image"   # 透明抠图(RGBA)
h3ctl gen --port 8288 --mode edit --ref 场景.jpg=image --ref 人物.jpg=image \
    --prompt "把 <image2> 的人放进 <image1> 的场景, 保持外观不变"   # 多参考: 第1张=画布, 第2张=材料
# 局部编辑: 给画布涂颜色标记(PIL/Paint Pen), 提示词点名颜色区域即可
# ⚠️ 多参考合成的人物面部还原有限(衣着/构图好); 敏感场景见 docs/data/test-log.md 调优抓手(resolution/PE)

# 3) 配方变体（同一档案内切 LoRA/采样, 零下载）
h3ctl gen --mode t2v --recipe pika …      # h3turbo 内: 官方 turbo ↔ pika

# 4) 观察 / 收工（销毁只听你的显式指令）
h3ctl status h3 && h3ctl logs h3 --follow && h3ctl down h3
```

脚本流兜底（不用 Go 时）：`MACHINE=A100 WEIGHTS=h3turbo bash scripts/h3_up.sh h3` →
`bash scripts/h3_tunnel.sh h3` → `python3 scripts/h3_generate.py --mode t2v --prompt …`

## 项目结构

```
h3ctl/      主程序（Go 单二进制: vm/load/gen/logs/down…）— 持续完善的主战场
colab/      VM 端两件: bootstrap_h3.py(引导+档案) + h3api.py(契约外壳) [archived/ = 退役归档]
scripts/    脚本流兜底(h3_up/tunnel/down/generate) + 批量示例 [archived/]
workflows/  外壳引用的 API 模板（agent 不直接碰）[archived/ = 异家族退役]
samples/    v0.4 实测样片（三模式 mp4 + 生图/编辑 before-after）
docs/       guides/(上手) data/(实测+成本) archive/(历史) + CHANGELOG
inputs/     测试资产（已裁水印）
```

## 设计原则

fire-and-poll（exec 只提交/轮询）、HF 直连不用 Drive/GCS、ComfyUI 只绑 127.0.0.1 零公网暴露、
A100 INT8（NVFP4 仅 Blackwell）、机型×档案 gate 硬校验（选错止步省 CU）、VM 销毁只听显式指令。

## 成本速查（实测，A100 5.4 CU/h）

> 余额唯一权威口径：`colab usage`（CLI 原生支持；估算仅供过程参考，idle 挂机烧 CU 不可忽视）。

| 项 | 数据 |
|---|---|
| **生图便宜档** | **qwen21t × L4 21s ≈ 0.01 CU/条** · zimage × T4 53s ≈ 0.02 CU/条 |
| 视频热跑 | pika ~102-107s（t2v/ref2v）· turbo4 fl2v 92s ≈ 0.14-0.20 CU/条 |
| 竖屏长镜头 ref2v | 8-13s/条：384×704 草稿档 39-90s · **768×1344 HD 档 3.5-8min**（294帧 ≈ 0.6 CU）|
| 全新冷启动 | A100 视频站 ≈1.2 CU（裸 VM 7.5min + load 85GB 6.5min）· T4/L4 生图站 ≈0.4-0.5 CU |
| 换配方（盘上已有） | ~1-3min ≈ 0.1 CU |
| 生图(A100) | t2i 1024² 95s / 编辑 16s（缓存命中） |
