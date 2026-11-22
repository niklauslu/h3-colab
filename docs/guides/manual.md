> ⚠️ **v0.1.1 迁移说明（2026-11-22）**：本文档部分章节仍描述 v0.1.0 的 `--workflow/--set` 节点寻址语法
> 与旧工作流文件（t2v_api.json 等已退役）。**现行契约见 [`agent-manual.md`](agent-manual.md)**（h3ctl 三命令 +
> 配方机制 + ComfyUI 透传 8189）。本手册的架构原则、坑排查、成本模型仍然有效；工作流细节章节以外壳
> `colab/h3api.py` TEMPLATES 为唯一事实源。

# H3 出片工作站 · 详细使用手册

> 面向接手本项目的任何人。不依赖任何对话上下文，按本手册可从零跑通并日常使用。
> 配套文档：`docs/research.md`（选型调研）、`docs/setup-log.md`（坑①-⑭实录）、`docs/agent-manual.md`（给 AI agent 的指令模板）。
> 最后更新：2026-11-21。环境：macOS + Google AI Pro 订阅 + google-colab-cli。

---

## 0. 30 秒速览（TL;DR）

```bash
bash scripts/h3_up.sh          # 开工：拉起 A100 工作站（冷启动 3~15 分钟，自动到 READY）
bash scripts/h3_tunnel.sh      # 建隧道（前台挂着；本地出片全走它）
python3 scripts/h3_generate.py \
  --workflow workflows/t2v_api.json \
  --set "5.text=提示词" --name demo    # 出一条（~3 分钟）
python3 scripts/h3_generate.py \
  --workflow workflows/fl2v_api.json \
  --set "9.prompt=提示词" --name fl2v  # 首尾帧模式（需先上传首/尾帧图）
bash scripts/h3_status.sh      # 查状态
bash scripts/h3_usage.sh       # 查 CU 余额
bash scripts/h3_down.sh        # 收工：释放 VM（务必！否则 5.4 CU/h 白烧）
```

产出自动三处归档：本地 `outputs/` → VM `/content/ComfyUI/output/` → Drive `MyDrive/H3/outputs/`。

---

## 1. 系统架构

```
┌─ 控制层（本机 macOS）────────────────────────────────┐
│ google-colab-cli（Bash 直调，无 MCP 依赖）             │
│  · colab new/exec/upload/ssh/drivemount/usage/stop    │
│  · h3_generate.py → ComfyUI HTTP API（经隧道）        │
└──────────────┬───────────────────────────────────────┘
               │ SSH -L 8188（ProxyCommand = WebSocket 桥）
┌─ 运行层（Colab VM）──────────────────────────────────┐
│ 标准 A100 40GB（5.4 CU/h）                            │
│ ComfyUI(127.0.0.1:8188) + FastH3 INT8 + 守护循环      │
│ （comfy 崩溃自动重启；产出每 60s 回写 Drive）          │
└──────────────┬───────────────────────────────────────┘
               │ 权重三级源（自动选择）
┌─ 存储层 ─────────────────────────────────────────────┐
│ ① GCS gs://h3-weights-309005742981（US-EAST1 公开读）│
│ ② Drive MyDrive/H3/{models,outputs}（2TB 额度内）    │
│ ③ HuggingFace FastVideo/FastVideo-FastH3-Comfy 兜底  │
└──────────────────────────────────────────────────────┘
```

设计原则（为什么长这样）：
- **fire-and-poll**：colab-cli 的 exec 对长任务会随机断连并连累内核任务（坑⑩），所以一切重活在 VM 后台线程执行、只写日志；exec 只做"提交"和"轮询"两个秒级动作。
- **权重不直读 Drive**：FUSE 随机读极慢（坑⑥/#4909），启动时同步到 VM 本地盘再读。
- **零公网暴露**：ComfyUI 只绑 127.0.0.1，访问全走 SSH 隧道。
- **量化选型**：A100(Ampere) 用 INT8；NVFP4 仅限 Blackwell（G4/Ultra 机型）。

---

## 2. 前置条件与一次性准备

### 2.1 账号与订阅
- Google AI Pro 订阅（含 Colab CU ≈100/月、2TB Google One、$10/月 GCP 点数）
- GCP 项目 `h3-workstation`（编号 `309005742981`）+ 结算账户 `h3-billing`（`01852D-6D1402-287567`，已关联项目）
- HuggingFace 账号（当前权重仓库不 gated，暂不需要 token；换 gated 权重时把 token 放 `colab/hf_token.txt`）

### 2.2 本机安装 colab-cli 并授权
```bash
uv tool install google-colab-cli      # 或 pip install google-colab-cli
export PATH="$HOME/.local/bin:$PATH"  # 建议写入 ~/.zshrc
colab usage                           # 首次触发 OAuth：浏览器登录→允许→把授权码贴回终端
```
- 授权页权限**必须全勾**（Colaboratory/Drive/Cloud Platform），漏勾会报 `Scope has changed`（坑③，需重新授权）
- 授权码一次性、几分钟过期

### 2.3 SSH 密钥（隧道认证依赖）
```bash
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C colab-ssh
```
原理：`h3_tunnel.sh` 里 ProxyCommand 把**公钥**经 `X-Colab-Ssh-Pubkey` header 注册给 runtime，真正的 ssh 用**配套私钥 + root 用户**（坑⑨）。

### 2.4 首次初始化权重库（已完成的记录）
历史顺序：① `colab/download_to_drive.py` 把 4 个权重文件下到 Drive（当时耗 0.11 CU）② `colab/gcs_setup.py` 建 GCS 桶并从 Drive 推到 GCS。**新接手人无需重做**——权重已在 GCS+Drive 双份，直接进入第 3 节日常操作。仅当换权重版本时参考第 5 节。

### 2.5 权重清单（当前生产集，~55GB）
| 文件（相对 `models/fasth3/`）| 大小 | 角色 |
|---|---|---|
| `diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors` | 22.13GB | DiT（INT8，8 步蒸馏）|
| `text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 27.14GB | 文本编码器（Qwen3-VL-32B 截断 INT8）|
| `vae/minimax_h3_video_vae_fp16.safetensors` | 5.21GB | 视频 VAE |
| `vae/minimax_h3_audio_vae_fp32.safetensors` | 0.61GB | 音频 VAE（出片自带声音的关键）|

---

## 3. 日常操作 SOP

### 3.1 开工：拉起工作站
```bash
bash scripts/h3_up.sh            # 会话名默认 h3
# HIGHMEM=1 bash scripts/h3_up.sh   # 切 A100 High-RAM(80GB, 8.88 CU/h)，一般不用
# EXPOSE=cloudflared bash scripts/h3_up.sh   # SSH 隧道不通时的备用暴露方式
```
自动流程：创建会话（已存在则跳过）→ 挂 Drive（首次弹浏览器授权，点允许）→ 上传引导脚本 → VM 后台线程执行【装 ComfyUI → 三级源拉权重 → 启动守护循环】→ 轮询至 `READY-CHECK: PASS`。
- 首次会弹 1~2 次浏览器授权页，**点允许**（坑⑥：授权没点完会 400，脚本会自动重试）
- 冷启动耗时：GCS 源 ≈3~6 分钟；Drive 并行 rsync ≈15 分钟（首次部署实测串行 21 分钟，已升级并行）
- 幂等：会话已存在会跳过创建；`/content/h3/.setup_done` 存在时 setup 直接跳过（删该文件可强制重跑）

### 3.2 建立隧道
```bash
bash scripts/h3_tunnel.sh        # 前台运行，Ctrl-C 断开；自带 keepalive
```
验证：本地浏览器开 **http://127.0.0.1:8188** 看到 ComfyUI 界面即通。
故障换备用：`EXPOSE=cloudflared bash scripts/h3_up.sh h3` 重拉，到 `h3_status.sh` 输出里取 `*.trycloudflare.com` 地址。

### 3.3 出片（T2V 文生视频）
```bash
python3 scripts/h3_generate.py \
  --workflow workflows/t2v_api.json \
  --set "5.text=一只橘猫在雪地里奔跑，电影感，黄昏光" \
  --set "12.seed=12345" \
  --name demo
```
- `--set 节点ID.输入键=值` 可重复；值自动识别 int/float/bool
- 常用注入点见附录 C
- 输出：`outputs/<name>_<时间戳>/xxx.mp4`，同时自动回写 Drive
- 首条含模型加载 ~3.5 分钟，之后每条 ~2.9 分钟；同工作流改参数重交走节点缓存（秒级）

**批量出片**：写个循环即可，ComfyUI 队列会顺序执行：
```bash
for p in "提示词一" "提示词二" "提示词三"; do
  python3 scripts/h3_generate.py --workflow workflows/t2v_api.json \
    --set "5.text=$p" --name batch --timeout 900
done
```

### 3.4 首尾帧（FL2VA）
```bash
# ① 上传首/尾帧图到 VM 输入目录（本地图片任意尺寸，工作流会归一到 1344×768）
colab upload -s h3 inputs/frame_first.jpg /content/ComfyUI/input/frame_first.jpg
colab upload -s h3 inputs/frame_last.jpg  /content/ComfyUI/input/frame_last.jpg
# ② 出片
python3 scripts/h3_generate.py --workflow workflows/fl2v_api.json \
  --set "9.prompt=镜头从第一帧缓推到最后一帧，电影感" --name fl2v
```
帧图文件名固定为 `frame_first.jpg` / `frame_last.jpg`（要换图就重新 upload 同名覆盖，或改 JSON 里 LoadImage 的 image 值）。

### 3.5 全参考（Ref2VA）— 未实测
本地节点已就位：`MiniMaxH3ReferenceToVideo`（多参考图/视频/音频）+ `MiniMaxH3AddGuide`（按 frame_idx 挂 image/audio guide）。需要时仿照 fl2v 工作流构造即可；2K Regenerate 属官方 API 功能（`MinimaxHailuo03*` 前缀节点），本地权重做不了。

### 3.6 状态与额度
```bash
bash scripts/h3_status.sh   # GPU/权重/守护循环/ComfyUI/队列/日志尾
bash scripts/h3_usage.sh    # CU 余额与费率
```

### 3.7 收工（重要！）
```bash
bash scripts/h3_down.sh     # 杀本地隧道 + colab stop
```
A100 挂机也烧 CU（5.4/h），**批次结束即停**。权重/产出都在 Drive+GCS，无损失。

---

## 4. 工作流参考

### 4.1 `workflows/t2v_api.json`（文生视频最小图）
| 节点ID | class_type | 作用 | 可调 |
|---|---|---|---|
| 1 | UNETLoader | 载入 DiT INT8 | `weight_dtype` 默认即可 |
| 2 | CLIPLoader | 载入编码器，`type:"minimax"` 固定 | — |
| 3/4 | VAELoader | 视频 VAE / 音频 VAE | — |
| **5** | CLIPTextEncode | **正向提示词（主注入点）** | `text` |
| 6 | CLIPTextEncode | 负向词（cfg=1 时不生效，留空）| — |
| 7 | EmptyMiniMaxH3LatentAV | 画布：默认 1344×768×124帧 | width/height/length（32 的倍数；124帧≈5.2s@24fps）|
| 8 | MiniMaxH3SigmaShift | sigma 位移（默认 12/3，勿乱动）| — |
| 9 | KSampler | **seed/steps/cfg/sampler** | steps=8（蒸馏模型 4~8）、cfg=1 固定 |
| 10/11 | VAEDecode / VAEDecodeAudio | 解出画面与音频 | — |
| 12/13 | CreateVideo + SaveVideo | 合成 mp4（24fps）| filename_prefix |

### 4.2 `workflows/fl2v_api.json`（首尾帧）
同上骨架，但 5/6 是 LoadImage+ImageScale（归一 1344×768 crop:center），**节点 9 `MiniMaxH3ImageToVideo` 同时输出 positive 和 latent**（替代 T2V 的 5+7），`first_frame/last_frame` 为可选输入；注入点是 `9.prompt`。
> 想只要首帧不要尾帧：删掉 `last_frame` 键即可（该输入可选）。

### 4.3 如何获得新的工作流
本地浏览器开 http://127.0.0.1:8188 → ComfyUI 界面 → 载入模板/搭好 → 设置开 Dev Mode → **Workflow → Export (API)** → 存入 `workflows/`。UI 格式 JSON 不能直接给脚本用。

---

## 5. 权重与存储管理

### 5.1 三级源与优先级（bootstrap 自动选择，无需人工干预）
1. **GCS 公开读** `https://storage.googleapis.com/h3-weights-309005742981/<相对路径>`（HTTP Range 断点续传，免认证）
2. **Drive** `MyDrive/H3/models/fasth3/`（并行 rsync + `--append-verify` 断点续传）
3. **HF** `FastVideo/FastVideo-FastH3-Comfy`（`allow_patterns` 限定 4 文件）

`bootstrap_h3.py` 顶部 `GCS_BUCKET = None` 可关闭 GCS 源；`WEIGHT_FILES` 列表控制要哪些文件。

### 5.2 换权重/加 LoRA 的标准动作
1. 新文件下载到 Drive 对应子目录（diffusion_models/ text_encoders/ vae/ loras/）
2. 推一份到 GCS：`colab/gcs_setup.py` 的 FILES 列表加一行重跑（或控制台拖拽）
3. VM 上删 `/content/models/.done` 强制重同步，或直接 `bash scripts/h3_up.sh`（setup 检测标记）
4. 文件名含 lora/vae/qwen 关键词会自动软链到对应 ComfyUI 目录（`classify()` 规则见 bootstrap）

### 5.3 成本
| 项 | 金额 | 覆盖 |
|---|---|---|
| GCS 55GB standard us-east1 | ~$1.1/月 | $300 试用金 / AI Pro $10 月度点数（已领）|
| Colab CU | 100 CU/月 | A100 余额见 `h3_usage.sh` |
| 出片 | ~0.24-0.4 CU/条（标准/High-RAM）| — |

---

## 6. 故障排查（症状 → 处置）

| 症状 | 原因 | 处置 |
|---|---|---|
| `colab exec` 报 `Connection was lost` | 坑⑩：长阻塞必断连 | 属正常；重试即可，VM 线程不受影响 |
| 装环境"成功"但 ComfyUI 缺失 | 断连连累内核任务（坑⑩旧行为）| 已由 fire-and-poll 架构根治；确认 setup.log |
| `Session not found` 但脚本认为存在 | 坑⑤：status 退出码恒 0 | 已修复（grep 文本判断）|
| drivemount `Error propagating: 400` | 浏览器授权没点/超时 | 自动重试 3 次；弹出页面尽快点允许 |
| `Mountpoint must not already contain files` | 挂载点有残留（坑⑥）| `rm -rf /content/drive` 后重挂 |
| `Scope has changed` | OAuth 权限没全勾（坑③）| 重新授权，全部勾选 |
| `NameError: runpy` | 内核重启后（坑⑪）| heredoc 里显式 `import runpy` |
| rsync `mkdir failed` | 父目录不存在（坑⑧）| 已修复（mkdir -p 前置）|
| SaveVideo 报 missing `format` | API 需显式格式 | workflow 里 `"format": "mp4"` |
| 生成报缺模型文件 | 权重没同步完 | `h3_status.sh` 看 setup.log；重跑 `h3_up.sh` |
| ComfyUI 起不来 | 看 `/content/h3/comfyui.log`（exec tail）| 守护循环会自动拉起；持续失败查显存/文件 |
| 隧道 `Permission denied` | 私钥/用户不对（坑⑨）| 用 `h3_tunnel.sh`，勿手拼命令 |
| 速度突然变慢 | VM 被换机型 | `colab status` 确认 Hardware=A100，否则 stop 重拉 |

**通用法则**：exec 断连/空响应 = 重试；内核卡死 = `colab restart-kernel -s h3`（注意坑⑪）。

---

## 7. 维护与升级

- **换 ComfyUI 版本**：bootstrap 每次 install 阶段自动 `git pull --ff-only`
- **加自定义节点**：VM 上 `colab exec` 进 `/content/ComfyUI/custom_nodes` clone + pip install，然后 `h3_status` 里重启 comfy（或重启会话）
- **升 2K**：本地权重不可行（官方未开源 Regenerate-2K）；要么控制台用 Hailuo AI，要么调官方 API（platform.minimax.io），要么 Ultra 档 G4 跑 NVFP4 + 放大工作流
- **备胎方案**：CU 紧张时 L4（1.67 CU/h，需验证 INT8 DiT 22GB 能否压进 24GB）或 T4 免费档（仅调试）；WanGP 整合包为低配兜底
- **许可**：MiniMax H3 Community License；美/欧/英/韩部署需申请；模型内置内容审核

---

## 附录 A · 文件清单

```
README.md                 快速开始（先看这个）
docs/manual.md            本手册
docs/research.md          选型调研与速度基准
docs/setup-log.md         搭建实录 + 坑①-⑭
docs/agent-manual.md      给 AI agent 的指令模板
workflows/t2v_api.json    文生视频工作流（API 格式）
workflows/fl2v_api.json   首尾帧工作流（API 格式）
workflows/README.md       工作流获取/导出方法
colab/bootstrap_h3.py     VM 引导（setup/status 两阶段，fire-and-poll）
colab/gcs_setup.py        GCS 建桶+上传（一次性工具）
colab/download_to_drive.py Drive 权重种子（一次性工具）
colab/hf_token.txt        HF token（可选，gitignore）
scripts/h3_up.sh          开工
scripts/h3_tunnel.sh      隧道
scripts/h3_generate.py    出片
scripts/h3_status.sh      状态
scripts/h3_usage.sh       额度
scripts/h3_down.sh        收工
inputs/                   首尾帧等输入素材
outputs/                  本地成片
```

## 附录 B · 命令速查

```bash
# 生命周期
bash scripts/h3_up.sh [会话名]                # 拉起（HIGHMEM=1/EXPOSE=cloudflared 可选）
bash scripts/h3_tunnel.sh [会话名]            # 隧道（前台）
bash scripts/h3_status.sh [会话名]            # 状态
bash scripts/h3_down.sh [会话名]              # 收工
bash scripts/h3_usage.sh                      # CU
# colab-cli 原生
colab sessions / status -s NAME / stop -s NAME
echo 'print(1)' | colab exec -s NAME          # 免上传执行
colab upload -s NAME 本地 远程                 # 注意顺序
colab download -s NAME 远程 本地
colab restart-kernel -s NAME                  # 内核卡死时（之后 exec 要显式 import runpy）
# 出片
python3 scripts/h3_generate.py --workflow workflows/t2v_api.json \
  --set "5.text=提示词" --set "12.seed=N" --name 名字 --timeout 900
```

## 附录 C · 注入点速查（`--set 节点.键=值`）

| 想改什么 | t2v | fl2v | ref2v |
|---|---|---|---|
| 提示词 | `5.text` | `9.prompt` | `7.prompt` |
| 种子 | `12.seed` | `12.seed` | `10.seed` |
| 分辨率（32 倍数）| `7.width` / `7.height` | `9.width` / `9.height` | `7.width` / `7.height` |
| 时长（帧，24fps）| `7.length` | `9.length` | `7.length`（≤3600，即 ≤150s）|
| 步数 | `9.steps`（4~8）| `12.steps` | `10.steps` |
| 参考图 | — | 重传 `frame_first/last.jpg` | 重传 `ref_char_front.jpg` / `ref_scene.jpg`（改 JSON 中 LoadImage 的 image 值可自定义名）|
| 输出前缀 | `13.filename_prefix` | `16.filename_prefix` | `14.filename_prefix` |

> 提示词技巧：官方 prompt 指南在 HF 仓库 `MiniMaxAI/MiniMax-H3/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`；主体+动作+镜头+光线+氛围 结构化描述效果最好。
