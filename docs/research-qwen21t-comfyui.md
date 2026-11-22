# ComfyUI × Qwen-Image-2.1-Turbo 官方资料精读(2026-11-22)

> 背景:昨日 img2img 四形态(编辑/合成/mask/抠图)质量不达终审。本调研以官方模板源码 + 官方文档 + 社区实测为据,找出与官方配方的全部差异。
> 信源:Comfy-Org/workflow_templates 官方模板 JSON(本地已下 3 份)、docs.comfy.org 官方教程、qwen-image-2-1-prompter skill(与官方 PE system prompt 同源)、Reddit 社区实测。

## 一、结论摘要

我们的采样链(sigma/CFG/负向/seed/latent 接线)与官方模板**完全一致**,已排查项全部排除。剩余真实差异有三:

| # | 差异 | 影响 | 状态 |
|---|------|------|------|
| ① | **compose 画布错位**:官方语义 `image_1 = 编辑目标(画布)`,输出构图跟随 image_1;我们把人物放 image_1、场景放 image_2 → 场景被当作"材料"重新想象,"背景不还原/人脸糊"的直接嫌疑 | 合成形态 | **已修**(scene↔char 对调) |
| ② | **resolution=0 的风险**:0 = 参考图原生尺寸(仅 32 倍数对齐)。一张 3000×4000 手机原图 → 12MP 潜空间,远超 2K 训练分布,官方文档明说"超 2K 提示词遵循度下降"。**官方节点默认 1024**(总像素预算) | 编辑/合成糊的第二嫌疑 | 待 E2 实验定夺 |
| ③ | **INT8 convrot 量化损失**:社区实测"BF16 提示词遵循度明显好于 INT8";官方模板出厂 INT8 是为低配机,质量上限是 BF16(13.25GiB,L4 可装) | 全形态 | 待 E3(bf16 对照) |

另发现两个增益开关未用:**Prompt Enhancer**(官方称"通常效果更好")与 **texture_fix VAE**(官方承认 stock 解码器有棋盘纹)。

## 二、官方模板精读(源码级)

模板:`Comfy-Org/workflow_templates` → `templates/image_qwen_image_2_1_{image_edit,turbo_image_edit,turbo_t2i,background_removal}.json`(本地副本 /tmp/qwen_research/)。

### Turbo Edit 出厂参数(外层注入值,逐项核对)

```
resolution      = 0        # 总像素预算; 0=参考图原生仅对齐32倍数 (官方节点默认1024)
negative_prompt = ""       # cfg=1 时负向无效
cfg             = 1        # CFGGuider widgets=[8] 是 subgraph 默认, 外层1覆盖
seed            = randomize
unet            = qwen_image_2.1_turbo_int8_convrot (6.76GiB)   # bf16 版 13.25GiB
clip            = qwen3vl_8b_int8_convrot (8.71GiB)             # bf16 版 16.33GiB
vae             = qwen_image_2.1_vae_bf16 (644MB)
PE clip         = qwen3.5_9b_..._pe_i2i.int8_convrot (8.82GiB)  # 默认关闭
```

### 接线实锤(与我们的修复逐一对上)

```
TextEncodeQwenImage21.out2  → ComfySwitchNode(false) → SamplerCustomAdvanced.latent_image
                             （slot2=参考图编码潜空间; EmptyLatentImage 是 switch=true 的备选分支）
ManualSigmas "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
KSamplerSelect(euler) + RandomNoise(randomize) + CFGGuider(cfg=1) + QwenImage21Cache(auto,default)
images.image_1 ~ image_10   → TextEncodeQwenImage21 十个独立输入端口（官方 API 就是点号键）
```

**基础版 Edit 官方配方**:KSampler `25步 / cfg 1 / euler / simple / denoise 1.0`,latent 同样走 slot2(ComfySwitch 同结构)。官方注释:"官方管线 40-50 步 euler,模板出厂 25;更高级的采样器可以更少"。

### 官方模板自带注释(要点)

- **image_1 是编辑目标(edit target)**,输出构图跟随 image_1;image_2~image_10 是参考材料,提示词用 `<image1>`...`<image10>` 指认。
- resolution 是**总像素预算**(非宽高),保持宽高比;官方默认 1024(≈1MP),模型上限 2048;模板出厂 0。
- custom_size 开时画布来自 ResolutionSelector,须接近缩放后的 image_1 尺寸,否则编辑漂移。
- cfg 保持 1(官方路径);**只有配负向提示词时才提 cfg**。
- 参考图最多 10 张(edit 模板前 10 slot 全接)。

### Prompt Enhancer(官方增强器,模板默认关)

- `refine_prompt` on = 用 qwen3.5-9B PE(i2i)模型重写指令后再进编辑模型,官方原话 **"usually better results"**;off = 快。
- `thinking` 必须与 enhancer 同开(PE checkpoint 用 reasoning block 训练,无 reasoning 掉质量)。
- `max_length` 官方设定 24000,模板压到 4096(防单次重写跑数分钟);**预算耗尽时 reasoning 块闭合失败 → 空提示词进编辑模型(编辑出近原图)**。
- 采样:temperature 1.0 / top_p 0.95 / top_k 20 / repetition 1.0。

### 指哪改哪的官方姿势(与我们 mask 路线并行有效)

官方不用 SetLatentNoiseMask,而是在 LoadImage 上**画笔涂色到 RGB 层** + 提示词点名颜色("change the jacket in the red area")+ 说明目标色;涂鸦保持区域内。我们的 mask_edit(用户已判 OK)与之互为补充。

## 三、resolution 语义(本次最大技术收获)

- `resolution=0`:每张参考图保持自身尺寸(取 32 倍数)。3000×4000 照片 → 3008×4000 ≈ 12MP。
- `resolution=N`(>0):image_1 宽高比缩放到 N×N **总像素预算**:1024 → ≈896×1184(1MP)。
- 模型训练上限 2048;**>2K 提示词遵循度下降**(官方原话)。
- 推论:昨日测试若用高分辨率原图,resolution=0 会把 Turbo 8 步推到远超训练分布的序列长度上 —— "编辑糊"的第二嫌疑。E2 实验裁决。

## 四、INT8 vs BF16 与已知质量问题(社区实测)

- **BF16 fidelity 明显更好**(r/StableDiffusion 发布帖多用户证实,尤其提示词遵循与细节)。
- 官方模板出厂 INT8 是为低显存机器;**质量上限是 BF16**。
- abenzerps 量化帖佐证:普通 25 步 KSampler 配蒸馏模型 = 严重 artifact(印证坑㉓:蒸馏模型必须 ManualSigmas 链)。
- **BF16 Turbo 也有已知问题**:皮肤过处理/过锐(独立帖子讨论);有人"换一个采样器后 8 步 Turbo BF16 好于全量模型 25 步"。
- 官方承认 stock VAE 解码有**棋盘纹理 artifact**;社区替代品 `texture_fix_vae_for_qwen_image_2.1_bf16.safetensors`(madebyollin)可经 Load VAE 换入,轻微牺牲重构精度换更干净的纹理。
- Lightning LoRA(lightx2v,非官方)4 步 cfg1:卡片标注 "1.0, 0.75, 0.5, 0.25" 是 pre-shift,1024×1024 实际采样 sigma ≈ "1.0, 0.857, 0.667, 0.4"。

## 五、与我们的工作流差异矩阵

| 项 | 官方 | 我们(修复前) | 修复后 |
|----|------|----------------|--------|
| ManualSigmas 8 值链 | 同 | 同 ✅ | — |
| CFGGuider cfg | 1 | 1 ✅ | — |
| negative_prompt | "" | "" ✅ | — |
| seed | randomize | 42(可复现,等价) | — |
| latent 来源 | TextEncode slot2 | slot2 ✅(坑㉔已修) | — |
| 参考图接线 | images.image_N 点号键 | 点号键 ✅(坑⑳已修) | — |
| QwenImage21Cache | auto/default | auto/default ✅ | — |
| **compose 图序** | image_1=画布 | 人物=image_1 ✗ | **已修:scene=image_1, char=image_2** |
| **compose prompt** | <image1>=画布被指认 | 旧样例按人物=image1 写 ✗ | **已修:画布语义重写** |
| edit resolution | 0(出厂)/1024(节点默认) | 0 | 待 E2 |
| base 版 edit | KSampler 25/cfg1/euler/simple, latent=slot2 | latent=EmptyLatent ✗ | **已修:["4",2]** |
| base 版 negative | "" | 写了字(cfg=1 无效但噪音) | **已修:""** |
| PE enhancer | 模板自带(默认关,开则 usually better) | 未用 | E4 候选 |
| texture_fix VAE | 官方承认棋盘纹 | 未用 | E6 候选 |

## 六、明日实验方案(按优先级)

| # | 实验 | 变量 | 预期 | 成本 |
|---|------|------|------|------|
| E1 | **compose 图序对照**(已修好的工作流直接跑) | scene=image_1 vs 昨日 char=image_1 | 若场景还原/人脸显著改善 → 根因坐实 | 0(VM 已有 INT8 全套) |
| E2 | **resolution 0 vs 1024** | edit + compose 各一组 | 若 1024 更干净 → 生产默认 1024 | ~0 |
| E3 | **INT8 vs BF16** | 换 `qwen_image_2.1_turbo_bf16`(13.25GiB) | 若 bf16 干净 → 生产=L4+bf16 | 下载 ~5min;L4 显存 DiT13.25+TE8.71+VAE0.64≈22.6GB 勉强单跑,TE 可留 int8 |
| E4 | PE enhancer on(thinking on,max_length 先 4096) | 同工作流开/关对照 | 指令类编辑增益 | 下载 8.82GB;+几十秒/次 |
| E5 | base 版 25 步质量路线(已修 latent) | Turbo 8 步 vs base 25 步 | 质量上限参照系 | L4 ~1-2min/张 |
| E6 | texture_fix VAE | stock vs fix | 皮肤/棋盘纹 | 下载 ~1GB |

**执行顺序建议:E1 → E2(零下载,当场裁决两个嫌疑)→ E3(定生产精度)→ 视结果 E4/E5/E6。**

## 七、信源

- 官方模板源码:https://github.com/Comfy-Org/workflow_templates (`templates/image_qwen_image_2_1_*.json`)
- 官方教程:https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1
- 权重仓:https://huggingface.co/Comfy-Org/Qwen-Image-2.1
- 社区:[Turbo 发布帖](https://www.reddit.com/r/StableDiffusion/comments/1x1lcau/)、[BF16 皮肤质感帖](https://www.reddit.com/r/StableDiffusion/comments/1x1s37j/)、[abenzerps 量化帖](https://huggingface.co/abenzerps/Qwen-Image-2.1-Turbo-Quantized)
- 本地 skill:`~/.zcode/skills/qwen-image-2-1-prompter/`(edit_rules.md 与官方 PE system prompt 同源)
