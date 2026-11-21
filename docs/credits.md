# 引用项目与致谢（Credits & Acknowledgements）

本管线站在这些项目的肩膀上。分三档：**核心依赖**（运行必需）、**辅助工具**、**选型参考**（影响设计但未直接使用）。开源复用本仓库时，请保留本页署名。

---

## 一、核心依赖

### 1. MiniMax H3（模型本体）
- GitHub: https://github.com/MiniMax-AI/MiniMax-H3
- HuggingFace: https://huggingface.co/MiniMaxAI/MiniMax-H3
- 官方博客: https://www.minimax.io/blog/minimax-h3
- 贡献：33B 全模态 DiT 权重（T2V/FL2VA/Ref2VA）、Qwen3-VL-32B 截断版文本编码器、双 VAE、prompt 写作指南（`docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`）
- 许可：**MiniMax H3 Community License**（非 Apache；美/欧/英/韩部署需申请表；内置内容审核）。**本仓库不含权重**，使用权重请遵其许可证

### 2. FastVideo-FastH3（实际部署的加速权重）
- HuggingFace: https://huggingface.co/FastVideo/FastVideo-FastH3-Comfy
- 贡献：8 步蒸馏 pruned 版 DiT + INT8 convrot 量化（本管线默认权重集），使我们能在 40GB A100 上跑出 ~3min/条
- 同实验室还有 FastVideo 通用视频加速研究

### 3. ComfyUI（推理运行时）
- GitHub: https://github.com/comfyanonymous/ComfyUI
- 贡献：执行引擎 + **H3 原生节点家族**（`MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo` / `MiniMaxH3SigmaShift` / `EmptyMiniMaxH3LatentAV` / `MiniMaxH3AddGuide` 等，本管线工作流全部基于它们）；Dynamic VRAM 管理（编码器→DiT 顺序加载）
- 注意：节点表中 `ComfyCloud*`、`MinimaxHailuo03*`、`Creatify*/Pika*` 前缀是**云端 API 包装节点**，与本地权重无关

### 4. google-colab-cli（控制层基础）
- GitHub: https://github.com/googlecolab/google-colab-cli （Apache-2.0，Google 官方）
- 贡献：无头创建/驱动 Colab runtime（`new/exec/upload/ssh/drivemount/usage/stop`）、SSH ProxyCommand 桥、agent skill `colab-operator`
- 本管线的 `scripts/*.sh` 全部构建在它之上

### 5. Google Colab 平台
- AI Pro 订阅（CU 算力、2TB Drive）；Colab 后端的 assign/exec/drivemount 接口（经 colab-cli 使用）

---

## 二、辅助工具

| 项目 | 用途 | 链接 |
|---|---|---|
| Comfy-Org/MiniMax-H3 | ComfyUI 官方封装 INT8 权重（备选源）| https://huggingface.co/Comfy-Org/MiniMax-H3 |
| Comfy-Org/workflow_templates | 官方模板（工作流参考）| https://github.com/Comfy-Org/workflow_templates |
| cloudflared | 备用隧道（EXPOSE=cloudflared 时）| https://github.com/cloudflare/cloudflared |
| huggingface_hub | 权重三级源之 HF 兜底下载 | https://github.com/huggingface/huggingface_hub |
| google-cloud-storage | GCS 权重桶上传/公开读 | pip 包 |
| uv / keyring（macOS）| colab-cli 安装与令牌提取 | astral.sh / pypi |

---

## 三、选型参考（影响设计，未直接运行）

| 来源 | 我们吸收了什么 |
|---|---|
| [MiniMax-AI/awesome-minimax-h3-integration](https://github.com/MiniMax-AI/awesome-minimax-h3-integration) | 官方整合索引：FastH3、NVFP4、WanGP、DiffSynth、FastVideo 的发现入口 |
| [ComfyUI 讨论 #4909](https://github.com/Comfy-Org/ComfyUI/discussions/4909) | "Drive FUSE 随机读慢"官方结论 → 本管线的 **rsync 到本地盘**策略 |
| [烁皓 @eternityspring A100 基准](https://x.com/eternityspring) | INT8 20步 805s / Turbo 4步 210s / FastH3 4步 156s → **FastH3 选型依据** |
| [Reddit T4 跑通帖](https://www.reddit.com/r/StableDiffusion/comments/1vrgtfo/) | 低配可行性证明 → 编码器 offload 策略信心 |
| [unsloth/MiniMax-H3-GGUF](https://huggingface.co/unsloth/MiniMax-H3-GGUF)、[DmitryDB ComfyUI 量化](https://huggingface.co/DmitryDB/MiniMax-H3-ComfyUI-Quants)、[DiffSynth NF4](https://huggingface.co/DiffSynth-Studio/MiniMax-H3-NF4) | 量化梯度备选（8GB~16GB 显存路线）|
| [deepbeepmeep/WanGP](https://github.com/deepbeepmeep/WanGP) | 低显存整合包备胎方案 |
| [Qwen3-VL](https://huggingface.co/Qwen/Qwen3-VL-32B) | 文本编码器原始来源（随 H3 权重分发截断版，此处致谢上游）|
| 社区 Colab 教程（bytenote 等） | G4/Ultra 生态、colab-mcp 思路对比（本管线最终选择 colab-cli）|

## 四、平台

- Google **Colab / Drive / Cloud Storage / Cloud Billing** — AI Pro 订阅内使用
- **阿里云 OSS** — 本文档部分测试图由图像生成服务产出（与管线运行无关）

---

> 署名格式建议：`本管线基于 MiniMax H3（MiniMax-AI）+ FastH3（FastVideo）+ ComfyUI，经 google-colab-cli 驱动 Colab 运行。`
