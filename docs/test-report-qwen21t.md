# 测试报告：Qwen-Image-2.1-Turbo（qwen21t 档案）

> 测试日期：2026-11-21 · 机型：T4（对照）/ **L4（生产推荐）** · 工作流：`workflows/qwen21t_*.json`
> CU 基价：$9.99/100 CU ≈ ¥0.72/CU；T4 费率 1.15 CU/h，L4 1.67 CU/h（均实测）

## 一、结论速览

| 能力 | 状态 | L4 实测 | 备注 |
|---|---|---|---|
| 文生图（8 步 CFG1） | ✅ | **16s/张** | 画质优秀，写实质感 |
| 中文文字渲染 | ✅ | 16s | 「海螺影像」海报级准确 |
| **原生 2K**（2048²） | ✅ | **42s** | 6.8MB PNG，细节无损 |
| **改图**（指令编辑） | ✅ | 16~26s | CFG3 + 明确措辞为甜点档（见 §4） |
| **指哪改哪**（颜色标记区域） | ✅ | 16~26s | 标记区被精确重绘；全局保持需强措辞（见 §4.3） |
| **参考图合成**（人物+场景） | ✅ | **16s** | `<image1>/<image2>` 标签引用，电影级融合（见 §4.4） |
| **透明图生成**（原生 RGBA） | ✅ | 16s | alpha 由 VAE 原生输出，透明占比 73.6% |
| **抠图**（改图模式去背景） | ✅ | **11s** | 69.6% 透明，边缘羽化自然 |
| 16 张参考图合成 | 🔜 | — | `images` autogrow 入口已就绪（image_1~16），待测 |

**生产推荐：Qwen-Image-2.1 一律用 L4**（理由见 §3）。单张全成本 **≈ ¥0.005，比官方 API（¥0.108）便宜 20×**。

## 二、选型过程（一次有价值的弯路）

1. **Noct-Q V4 Uncensored**（基础版 2.1 + DiT 级去审核）：画质好、中文好，但 **25 步在 T4 上 712s/张 ≈ ¥0.16，贵过 API** → 弃
2. 用户拍板 Turbo 速度优先 → **官方 Turbo INT8** 定案
3. Uncensored 事实澄清："Turbo + 真去审核"社区不存在；AtomicChat "Turbo-Uncensored" 仅改文本编码器，实测 45 敏感提示词图像零变化 → **官方 Turbo 即其功能等价物**（无功能损失）
4. abenzerps/Qwen-Image-2.1-Turbo-Quantized 与 Comfy-Org 官方重打包**同一 INT8 文件**（平铺布局，留作备用源）；其 FP8/MLX/NVFP4 档分别需 Ada+/Apple Silicon/Blackwell，T4/L4 均不适用

## 三、T4 vs L4 对照（同 INT8 文件，唯一变量=机器）

| 指标 | T4（15GB，1.15 CU/h） | **L4（24GB，1.67 CU/h）✅** |
|---|---|---|
| 首张（含全量装载 17.3GB） | 91s | **32s** |
| 续出（纯推理，1024×1536） | 69s | **16s（4.3×）** |
| 原生 2K（2048²） | — | **42s** |
| 单张成本 | 0.022 CU ≈ ¥0.016 | **0.0074 CU ≈ ¥0.005（3×）** |
| 显存状态 | 塞满 → 权重流送拖慢 | 全量常驻，零 offload |

**判定：L4 时薪贵 45% 但快 4.3×，每张反而便宜 3×。Qwen-Image 系生产一律 L4。**
T4 保留价值：AI Pro 免费额度时段的兜底档。

## 四、能力实测明细

### 4.1 改图（指令编辑）——CFG 梯度实测

同源图（橘猫宇航员 1024×1536）、同 seed 42：

| 配置 | 耗时 | 结果 |
|---|---|---|
| CFG1 + "把头盔换成红帽" | 16s | ❌ 指令半服从：红帽**叠在头盔上**而非替换 |
| CFG8 + 同提示（官方模板值） | 26s | ❌ 过曝崩坏（官方模板还配专用 sigmas，单提 CFG 不成立） |
| **CFG3 + 明确措辞**（"移除头盔和面罩，给猫戴上红帽，露出猫脸"） | 26s | ✅ **猫脸露出、红帽戴正、身份/服装/背景保持** |

**改图配方**：CFG 3 + 正向措辞（说"戴上/添加"优于"换成"）+ 负向保留通用词条。样片：`samples/qwen21t/turbo_l4_edit_cfg*.png`（三档对照）。
工作流：`qwen21t_edit_api.json`——`TextEncodeQwenImage21` 加 `vae` + `images=[[LoadImage,0]]` 即为编辑模式，参考图槽位支持 16 张（`<image1>` 标签引用）。

### 4.2 图生图形式一：指哪改哪（颜色标记区域）

机制（官方 Mask Editor / Paint Pen 的 API 等价物）：在原图上涂色标记区域 → 连同原图喂给 `TextEncodeQwenImage21`，提示词指代 "the red-painted area"。

| 尝试 | 配置 | 结果 |
|---|---|---|
| v1 | CFG3 + 简短指代（"repaint the red-marked region"） | ❌ 漂移严重：整图重绘成另一只猫+噪点背景，红标被当作内容主体 |
| **v2** | **CFG1 + 结构化英文指代**（明示 mask 用途 + 逐项 keep 清单） | ✅ **标记区（头盔）被精确替换为棒球帽**，猫脸/宇航服保持 |

**配方**：CFG1 + 英文结构化提示（"The area painted in solid red is a selection mask covering X. Replace it with Y. Keep A/B/C completely unchanged."）。
**已知边界**：8 步全图再生成机制下背景可能轻微漂移（本次地球→暗墙）；要求严格锁定背景时需配 `SetLatentNoiseMask` 传统 inpaint 模式（待测）。
样片：`turbo_l4_maskedit_v2.png`（成功）/ `turbo_l4_maskedit_fail.png`（失败对照）。

### 4.3 图生图形式二：参考图合成（人物+场景）

双参考图注入 `images` autogrow（`[["10",0],["11",0]]`），提示词用 `<image1>`（人物）`<image2>`（场景）标签引用：

- 输入：`ref_char_front.jpg`（人物）+ `ref_scene.jpg`（雨夜东京小巷）
- 提示词（中文 prose）：`把 <image1> 中的人物放进 <image2> 的雨夜东京小巷场景中：人物撑着透明雨伞走在巷子中央…`
- **结果：16s，电影级**——人物服装发型保持、场景霓虹/湿地/构图完整融合、透明雨伞正确呈现
- 样片：`turbo_l4_compose_char_scene.png`；工作流：`qwen21t_compose_api.json`

**与视频侧 Ref2VA 呼应**：同一套素材，图生图合成 16s vs 视频生成 209s——可先用图合成定稿构图，再喂 FL2VA/Ref2VA 出视频，形成高效的"图定稿→视频"工作流。

### 4.4 透明图（RGBA）——两代方案对比

| 方案 | 结果 |
|---|---|
| ~~EmptyQwenImageLayeredLatentImage~~（分层潜空间） | ❌ 5D 潜空间 + 专用分层 DiT（`qwen_image_layered_bf16`，另一个模型）——**不是 2.1 的原生路径**，首次尝试走了弯路 |
| **标准 T2I + 提示词要求透明背景** | ✅ **16s，RGBA 直出**：alpha 范围 0~255、四角 ≈0、主体 255、透明占比 73.6%，棋盘格合成验证边缘干净（`samples/qwen21t/turbo_l4_rgba_checker.png`） |
| **改图模式抠图**（"Remove the background, output PNG"） | ✅ **11s，RGBA 直出**，透明占比 69.6% |

**机制**（官方文档确认）：2.1 的 VAE 解码即四通道，透明直接生成在 alpha，非后期抠图。
**注意事项**：下游三通道节点（如部分保存/合成节点）会因 4 通道报错 → 解码后接 `SplitImageWithAlpha` 取 RGB；`JoinImageWithAlpha` 掩码约定 1=透明。
**工作流**：`qwen21t_rgba_api.json`（= T2I + 透明提示词，已留档）；`qwen21t_edit_api.json` 改提示词即抠图模式。

### 4.5 待办

- 16 参考图合成（多图 COMFY_AUTOGROW_V3 注入）——入口已验证存在，需一次专项测试
- 提示词工程建议引入 `qwen-image-2-1-prompter` skill 规范（英文 prose、观察者视角、无双riting boosters、`wh_ratio` 不写入提示词）

## 五、成本模型（L4 生产口径）

| 场景 | 计算 | 单张 |
|---|---|---|
| 稳态（1024×1536） | 16s × 1.67 CU/h | 0.0074 CU ≈ **¥0.005** |
| 2K（2048²） | 42s × 1.67 CU/h | 0.019 CU ≈ ¥0.014 |
| 摊冷启动（1h 会话：装载+30 张） | ≈0.034 CU/张 | ≈ ¥0.025 |
| 官方 API 对照 | $0.015/张 | ¥0.108 |

100 CU 月包 ≈ **1.3 万张** 1024² 图（API 同量 ≈ ¥1400）。

## 六、过程中修复的坑（详见 setup-log §12）

- **坑⑰** 守护循环 3s 健康检查在模型装载期误杀 ComfyUI（8 轮循环）→ 60s 超时 + 连续 2 次失败 + 90s 宽限
- **坑⑱** `classify()` 的 `qwen` 关键字把 Turbo DiT 误归 text_encoders → 规范子目录优先
- **坑⑲（认知）** RGBA ≠ 分层模型：`EmptyQwenImageLayeredLatentImage` 属于配套的 Qwen-Image-Layered；2.1 原生 RGBA = VAE 四通道直出，提示词即可

## 七、样例库（samples/qwen21t/，按场景分目录，img2img 均含原图）

```
samples/qwen21t/
├── t2i/        文生图 4 张（T4/L4 对照、中文海报、2K）
├── edit/       指令编辑：source.png 原图 + CFG1/3/8 三档对照
├── mask_edit/  指哪改哪：source_masked.png 标记原图 + 失败/成功对照
├── compose/    参考图合成：ref_char.jpg + ref_scene.jpg 原图 → compose_result.png
├── rgba/       透明图：rgba_native + 棋盘格验证 + bgremove 原图/结果
└── README.md   每个案例的工作流 + 注入参数 + 提示词（可复现）
```
