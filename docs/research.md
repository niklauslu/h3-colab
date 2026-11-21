# MiniMax H3 × Colab 调研留档（2026-11-21）

## 1. 模型本身

**MiniMax H3 = 海螺 3.0（Hailuo 3.0）**，2026-07-31 发布，开源权重（非完全开源）。

- 官方博客：https://www.minimax.io/blog/minimax-h3
- HF 官方仓库：https://huggingface.co/MiniMaxAI/MiniMax-H3
- 官方整合列表：https://github.com/MiniMax-AI/awesome-minimax-h3-integration
- 官方 GitHub：https://github.com/MiniMax-AI/MiniMax-H3

| 项目 | 规格 |
|---|---|
| 结构 | 33B 稠密单流 Transformer DiT（~13B 在 AdaLN 分支，**推理可不加载** → 实际 ~20B）|
| 文本编码器 | Qwen3-VL-32B 截断版（50 层、无 final norm/lm_head，BF16 ~15.7GB）|
| 输出 | 4–15s @ 24fps，默认短边 768px，32kHz 立体声同步音频，21:9~9:16 全画幅 |
| 输入 | T2V / I2V / 首尾帧（FL2VA）/ 全参考（Ref2VA）|
| 权重变体 | FL2VA、Ref2VA 两套任务分支，CFG 蒸馏，BF16 分发 |
| 许可 | MiniMax H3 Community License；内置审核；**美/欧/英/韩用户需申请表**，其他地区直接可用 |
| 未开源 | H3-Regenerate-2K（2K 再生成）、H3-Context-IR（预处理编排）→ 只能走官方 API |

## 2. Colab（AI Pro）硬件账

- Pro 档 100 CU/月；A100 40GB ≈ 5.4 CU/h（≈18h/月）；L4 ≈ 60h；T4 ≈ 80–90h
- A100 机型磁盘约 200GB+；系统内存高配机型更充裕
- **AI Pro 附带 2TB Google One 存储** → 放权重库毫无压力
- ⚠️ **NVFP4 量化版面向 Blackwell（G4/Ultra 96GB 机型）**；A100 是 Ampere，用 INT8

## 3. 速度基准（决定生产方案）

A100 自部署实测（烁皓 @eternityspring）：

| 配置 | 步数 | 耗时 |
|---|---|---|
| H3 INT8 原版 | 20 | 805.5s（13.4 min）|
| H3 INT8 + Turbo | 4 | 210.2s（3.5 min，3.8×）|
| **FastH3 INT8 + 90% VSA** | 4 | **156.3s（2.6 min，~5×）**|

- Jarvis Labs：H200 上 Turbo 8 步 2.1×（5s 片段）
- **FastH3 官方 ComfyUI 支持**：`FastVideo/FastVideo-FastH3-Comfy`（HF，~22GB），带 T2V/首帧/首尾帧官方工作流 → **本方案默认权重**
- Turbo LoRA 另有社区版（Civitai，joker124zx 工作台 Cell 5 打包），可选
- 产能：100 CU ≈ 18h A100 ≈ 理论 200~300 条/月

## 4. 方案对比（社区扫描）

社区最热的一键包多面向 **Ultra 的 G4 96GB**，AI Pro 用户需改造：

| 项目 | GPU 适配 | 结论 |
|---|---|---|
| keboqi/minimax-h3（Prism Studio）| Blackwell/A100 80G | ❌ 需 Ultra |
| wy51ai/easycolab（330⭐）| G4 96G 主推 | ❌ 主路径不适配；亮点：Drive 权重持久化 + 跨平台客户端（非官方，有政策风险提示）|
| joker124zx/minimax-h3-qwen-comfyui-colab | 默认 G4，可切 A100/L4 | ⚠️ **可改造**：跳过 64G BF16 cell，用其 FastH3 NVFP4（13G）/ INT8 池；工程成熟（守护循环、防 OOM、免重下重启、36 套工作流）|
| WanGP（deepbeepmeep）| 8GB+ | ✅ 低配兜底（T4/L4 可跑，慢）|
| Comfy-Org INT8 + 官方模板 | ~19GB DiT | ✅ 基线方案 |
| Reddit T4 实测 | 16GB 免费档 | ✅ 证明 GGUF+顺序 offload 可行 → A100 40GB 余量大 |

关键链接：
- ComfyUI 封装权重：https://huggingface.co/Comfy-Org/MiniMax-H3
- ComfyUI 官方教程：https://docs.comfy.org/tutorials/video/minimax/minimax-h3
- 官方模板：https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_t2v.json
- GGUF（Q2–Q8）：https://huggingface.co/unsloth/MiniMax-H3-GGUF
- NF4（8GB 起）：https://huggingface.co/DiffSynth-Studio/MiniMax-H3-NF4
- WanGP：https://github.com/deepbeepmeep/WanGP
- 编码器 pruned GGUF：https://huggingface.co/joeygambino/MiniMax-H3-encoder-GGUF

## 5. Agent 管理方案对比

| | colab-mcp | **google-colab-cli（选定）** |
|---|---|---|
| 仓库 | github.com/googlecolab/colab-mcp | github.com/googlecolab/google-colab-cli |
| 前提 | 浏览器必须开着 Colab 页 | ✅ 无头，走 Colab 后端 API |
| 创建 GPU runtime | ❌ | ✅ `colab new --gpu A100 --high-mem` |
| 文件上传/下载 | ❌ | ✅ `upload/download/ls/rm` |
| Drive 挂载 | notebook cell | ✅ `colab drivemount` |
| 端口暴露 | Cloudflare 隧道 | ✅ `colab ssh --proxy-mode` → OpenSSH 本地转发 |
| CU 监控 | ❌ | ✅ `colab usage` |
| 接入方式 | MCP（需客户端支持 tools/list_changed）| ✅ Bash 直调，零 MCP 配置；自带 agent skill（skills/colab-operator）|

安装：`uv tool install google-colab-cli`；首次 `colab sessions` OAuth 授权；macOS/Linux only。

`colab exec` 细节：`-f FILE` 本地读取内容传给 kernel（无需先 upload）；单会话时可省 `-s`；
stdin 模式 `echo '...' | colab exec`。

## 6. Drive 持久化最佳实践

来源：ComfyUI 官方讨论 #4909（https://github.com/Comfy-Org/ComfyUI/discussions/4909）

- ❌ ComfyUI 直读挂载的 Drive（FUSE 随机读极慢，大权重必卡）
- ✅ 会话启动时把权重从 Drive **rsync 到本地盘**（Colab NVMe 快），ComfyUI 只读本地
- ✅ 产出定期从本地回写 Drive（守护循环内做，见 bootstrap）
- 目录约定：`MyDrive/H3/{models, outputs}`；本地镜像 `/content/models`、`/content/ComfyUI/output`

## 7. 踩坑清单（社区实测汇总）

1. **文本编码器是显存大户**（~15.7GB）：单卡装不下 DiT+编码器时按"编码→卸载→加载 DiT"顺序；或换 4B/8B 编码器（社区验证有效）
2. **别用 Q2_K 以下量化**：编码器 Q2_K 输出直接崩坏；DiT ≥ Q4/NVFP4/INT8
3. INT8 DiT ~18–19GB + VAE + 音频 VAE，A100 40GB 放得下，编码器 offload 处理
4. 下载 gated 权重要 HF token；首次文件名以 HF API 列表为准（脚本已做自动分类软链）
5. 低配卡 12s 视频可能 ~50 分钟（T4 GGUF 档）——这正是我们选 A100+FastH3 的原因
6. Colab 会话空闲 ~180 分钟被回收；长时间不用及时 `colab stop` 省 CU
7. 第三方客户端/隧道方案（easycolab 等）存在平台政策风险；官方 colab-cli 风险最低

## 8. 结论

**主方案（已落地为脚本）**：google-colab-cli 无头驱动 → A100 40GB → ComfyUI +
FastH3 INT8 4 步（~2.6min/条）→ SSH 隧道本地 API 出片 → Drive 权重库/产出持久化。

**兜底**：WanGP（L4/T4）；**可选加速**：Turbo LoRA（Civitai）；**2K 需求**：官方 API。
