# 样片说明（samples/）

每个样片 = 一套可复现参数（工作流 JSON + 注入参数）。复现方法见 `docs/manual.md` §3。

## t2v/smoke_00001_.mp4 — 文生视频冒烟样片
- 工作流：`workflows/t2v_api.json`（seed 42，8 步，cfg 1，1344×768×124帧）
- 提示词：`A majestic orange cat walking through fresh snow at golden hour, cinematic lighting, slow motion, fluffy fur detailed, snowflakes sparkling`
- 规格：1344×768 · 24fps · 5.17s · 4.0MB · 含同步音频
- 生成耗时：首条 3.5 分钟（含模型加载，A100-80GB）

## fl2v/ — 首尾帧（FL2VA）
- 输入：`frame_first.jpg`（黄昏正面）、`frame_last.jpg`（暮色背影），均为 AI 生成 16:9 静帧
- 工作流：`workflows/fl2v_api.json`（seed 77，ImageScale lanczos 归一）
- 提示词：`An orange tabby cat walks through deep snow from dusk into twilight, camera slowly follows behind it…`
- 规格：fl2v_00001_.mp4 · 2.9MB
- 生成耗时：2 分 51 秒

## ref2va/ — 全能参考（Ref2VA）
- 输入：`ref_char_front.jpg`（角色参考）+ `ref_scene.jpg`（场景参考）
- 工作流：`workflows/ref2v_api.json`（seed 2026；`MiniMaxH3ReferenceToVideo`，ref_images autogrow 列表注入，ref_image_size "match"）
- 提示词：`A young woman with short silver-purple hair wearing a mustard yellow raincoat walks slowly through a rainy neon-lit Tokyo alley at night, holding a transparent umbrella…`
- 规格：ref2v_00001_.mp4 · 3.6MB · 209 秒生成
- 扩展：`ref_videos` / `ref_audios` 入口支持参考视频与音频（同一 autogrow 格式）

## zimage/ — 文生图（Z-Image-Turbo，T4）
- 模型：Tongyi-MAI Z-Image-Turbo（6B，Apache-2.0，8 步无 CFG），机型 **T4**（1.15 CU/h 实测）
- `zimage_smoke.png`：`workflows/zimage_t2i_api.json`，seed 42，1024×1024
  - 提示词：`一只橘猫戴着宇航员头盔漂浮在太空中，远处是蓝色地球，电影感光线，细节丰富`
  - 耗时 127s（含首次模型加载）；续出 61s/张
- `zimage_text_poster.png`：同工作流，seed 7 —— **中英文字渲染演示**
  - 提示词：`电影海报设计，深蓝色星空背景，金色大字标题「海螺影像」，下方一行小字「MINIMAX H3 · COLAB PIPELINE」，底部小字「v0.2 · AI 生成」，构图居中，简洁大气，金属质感字体`
  - 中文与英文文字全部渲染准确（Z-Image 招牌能力）
- 许可：Apache-2.0，可商用

## qwen21t/ — 文生图 + 编辑（Qwen-Image-2.1-Turbo INT8，T4/L4）
- 模型：Qwen-Image-2.1-Turbo INT8 convrot（7B，8 步 CFG1，Qwen Research License **非商用**）
- 工作流：`workflows/qwen21t_t2i_api.json`（`TextEncodeQwenImage21` 双输出正/负条件）
- `turbo_t4_smoke.png`：T4，seed 42，1024×1536 —— 首张 91s（含装载），续出 69s
- `turbo_l4_smoke.png`：**L4**（生产机型），同题同 seed —— 首张 32s（含全量装载），续出 **16s**
- `turbo_l4_text_poster.png`：L4 seed 7 —— 中文海报渲染准确
- `turbo_l4_2k.png`：L4 **原生 2048×2048** —— 42s/张（Qwen 独有能力，Z-Image 无）
- 成本：L4 ≈ 0.0074 CU ≈ **¥0.005/张**（比 API ¥0.108 便宜 20×）
- 说明：曾测 Noct-Q V4 Uncensored（基础版 25 步）——画质好但 T4 712s/张贵过 API，弃；
  "Turbo+真去审核"社区不存在（AtomicChat 仅改编码器，实测图像零变化）
