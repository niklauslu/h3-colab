# 调研：AI 生图选型（H3-Image / Qwen-Image-2.1-Turbo / Z-Image-Turbo）

> 调研日期：2026-11-21 · 结论先行：**三个都能在现有管线上跑通**。
> 推荐组合：**Z-Image-Turbo 做日常出图（T4/L4 便宜甚至免费）→ 需要编辑/RGBA/2K 时用 Qwen-2.1-Turbo → 追求画质上限/4K/与视频联动时用 H3-Image（A100）**。

## 一、对比总表

| | **Z-Image-Turbo** | **Qwen-Image-2.1-Turbo** | **MiniMax-H3-Image** |
|---|---|---|---|
| 出品方 | 阿里通义（Tongyi-MAI） | 阿里 Qwen 团队 | MiniMax（与我们视频模型同族） |
| 参数量 | 6B | 7B（MMDiT） | **33.1B** |
| 步数 | 8 步（无 CFG） | 8 步（基础版 25 步） | 6 步（无 CFG，固定） |
| 功能 | **仅文生图**（Edit 变体未发布） | 文生图 + **指令编辑** + **RGBA 透明通道** + **原生 2K** + 16 张参考图 | 文生图 + **编辑**（16 参考图）+ **原生 4K**（4096²，最大 6272×2688） |
| 中英文字渲染 | ✅ 双语强项（招牌能力） | ✅ 海报/信息图级 | ✅ |
| DiT 权重 | bf16 ~12GB（FP8 ~8GB / GGUF ~6GB） | bf16 ~15GB；社区 FP8 7.13GB / INT8 7.26GB / GGUF <4GB | bf16 66.3GB；社区 INT8 **~18GB**、GGUF（realrebelai/FastH3_GGUFs） |
| 文本编码器 | （小，Qwen3 系，待核对） | ~17.5GB bf16 | **Qwen3-VL（与视频 H3 同一个）→ GCS 已有 INT8 版可复用** |
| 显存门槛 | 16GB 舒适 / FP8 8GB | L4 需 offload，A100 无压力 | INT8 后 ~18GB + 编码器顺序加载，A100 40GB 可跑 |
| **许可证** | **Apache-2.0（可商用）** | ⚠️ **Qwen Research License：仅限非商用** + 须标注 "Built with Qwen" | MiniMax Community License（同视频模型，美/欧/英/韩部署需申请表） |
| ComfyUI | ✅ 原生（官方教程 + 模板） | ✅ 原生内置节点（TextEncodeQwenImage2.1） | ✅ 官方 T2I + Edit 两套模板 |
| 推荐机型 | **T4（免费）或 L4（1.67 CU/h）** | L4 / A100 | **A100（5.4 CU/h）** |
| 速度基准 | H800 亚秒级；消费卡数秒 | 8 步即时出图 | RTX 2070 8GB 也能跑（57s/1024²，靠 offload）；A100 预期秒级 |

## 二、逐个分析

### 1. Z-Image-Turbo —— 日常主力（首选落地）

- **最大优势是成本结构**：FP8/GGUF 量化后 8GB 级显存 → T4（AI Pro 常有免费配额）或 L4 都能跑，出图完全不必动用 A100。
- Apache-2.0，是三者中唯一无商用限制的，与我们开源项目最搭。
- 双语（尤其中文）文字渲染是官方主打——做海报、带字封面、标题卡最合适。
- 短板：**只有文生图**，Z-Image-Edit / Omni-Base 官方标注 "To be released"；蒸馏固定版多样性偏低。
- ComfyUI 原生支持：官方教程 [docs.comfy.org/tutorials/image/z-image/z-image-turbo](https://docs.comfy.org/tutorials/image/z-image/z-image-turbo)，模板已内置。
- 权重：HF [Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)（6B，F32 元数据，实跑 bf16）。

### 2. Qwen-Image-2.1-Turbo —— 编辑/RGBA/2K 特性担当

- 功能覆盖面三者最全：**8 步完成文生图 + 指令编辑**，RGBA 真透明通道输出、原生 2K（编辑最大 4096）、最多 16 张参考图（VAE latent 拼接）。
- ⚠️ **许可证是硬约束**：Qwen Research License 明确 **NON-COMMERCIAL ONLY**，商用需另行授权，产品文档须带 "Built with Qwen"。开源仓库里跑个人/展示没问题，但**产出物若商用要先解决授权**。
- ComfyUI 原生内置节点，无需额外包；Int8 官方示例可用；量化版齐全（FP8 7.13GB / INT8 7.26GB / GGUF <4GB）。
- 权重：官方 [Qwen/Qwen-Image-2.1-Turbo](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo)、ComfyUI 重打包 [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)、量化 [abenzerps/…-Quantized](https://huggingface.co/abenzerps/Qwen-Image-2.1-Turbo-Quantized)、[unsloth/…-GGUF](https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-GGUF)。
- 官方页面：[comfy.org/qwen-image-2.1](https://comfy.org/qwen-image-2.1)。

### 3. MiniMax-H3-Image —— 画质上限 + 与视频管线的联动王牌

- 33B 大模型但**社区 INT8 约 18GB**（[MindStudio 指南](https://www.mindstudio.ai/blog/minimax-h3-image-local)；GGUF 见 [realrebelai/FastH3_GGUFs](https://huggingface.co/realrebelai/FastH3_GGUFs)），A100 40GB 跑得动，连 RTX 2070 8GB 都有人跑通（57s/1024²，靠 offload）。
- **6 步无 CFG、原生 4K**（1:1 到 4096×4096，无需放大器）——正好补上我们视频侧的短板：**开源 H3 没有 2K Regenerate（仅官方 API），但可以用 H3-Image 先出 4K 高质量帧图，再喂 FL2VA/Ref2VA 出视频**，图→视频联动闭环。
- 文本编码器与视频 H3 **同一个 Qwen3-VL**（DiT 读第 50 层隐藏态）→ **GCS 桶里已有的 INT8 编码器文件很可能直接复用**，冷启动少拉 27GB（实现时验证加载器是否同一路径）。
- 注意：官方 PE 提示扩展器只有 NVFP4（17.9GB）→ Blackwell 专属，A100 上不可用（不影响基础 T2I/Edit）。
- 许可证与视频模型同族（Community License），我们已接受该条款。
- 权重：HF [MiniMaxAI/MiniMax-H3-Image](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image)，GitHub [MiniMax-AI/MiniMax-H3-Image](https://github.com/MiniMax-AI/MiniMax-H3-Image)。

## 三、与现有管线的整合（几乎零改造）

现有架构三件套全部直接复用：

1. **fire-and-poll 引导**：`colab/bootstrap_h3.py` 不区分图/视频，ComfyUI 起来后只是不同的 workflow JSON。
2. **GCS 三级权重**：`WEIGHT_FILES` 列表加图片模型条目（Z-Image ~12GB 或 H3-Image INT8 ~18GB + 可能复用的编码器），`classify()` 按文件名自动归目录；桶成本 ~$0.02/GB/月，20GB 级 ≈ $0.4/月，可忽略。
3. **出片脚本**：`h3_generate.py` 本来就是"提交 workflow → 轮询 → 下载"，出图同样适用（输出 jpg/png）。

机型策略（与视频共用 CU 池）：

| 场景 | 机型 | CU 成本 |
|---|---|---|
| 日常出图（Z-Image） | T4（实测 1.15 CU/h）/ L4（1.67 CU/h） | ~0.025 CU/张 |
| 编辑、RGBA、2K（Qwen） | L4（offload）或 A100 | 1.67~5.4 CU/h |
| 画质上限、4K、图→视频联动（H3-Image） | A100 | 5.4 CU/h，与视频同会话可省冷启动 |

## 四、建议路线

1. **先落地 Z-Image-Turbo**：T4/L4 便宜甚至免费，风险最低，先把「生图 = 单条命令」跑通（bootstrap + workflow + GCS 权重三步）。
2. 再加 Qwen-2.1-Turbo 的编辑/RGBA 工作流（注意商用授权边界）。
3. 最后上 H3-Image（A100 空闲时），打通 **H3-Image 4K 出图 → FL2VA/Ref2VA 出视频** 的高质量闭环，作为本仓库的亮点功能。

## 五、来源

- https://huggingface.co/MiniMaxAI/MiniMax-H3-Image · https://github.com/MiniMax-AI/MiniMax-H3-Image
- https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
- https://comfy.org/qwen-image-2.1 · https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo
- https://huggingface.co/abenzerps/Qwen-Image-2.1-Turbo-Quantized · https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-GGUF · https://huggingface.co/Comfy-Org/Qwen-Image-2.1
- https://www.mindstudio.ai/blog/minimax-h3-image-local
- https://huggingface.co/realrebelai/FastH3_GGUFs
- https://docs.comfy.org/tutorials/image/z-image/z-image-turbo
- https://www.thundercompute.com/blog/z-image-turbo-comfyui
- https://www.reddit.com/r/StableDiffusion/comments/1xvi9ko/（RTX 2070 8GB 跑 H3-Image INT8）
