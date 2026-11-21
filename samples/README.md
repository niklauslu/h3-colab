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
