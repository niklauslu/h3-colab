# 样片库（v0.1.2 · 2026-11-22 更新）

> 全部为实测产出，复现参数见各目录与 `docs/data/test-log.md`。
> 生图三档：A100（h3image）/ L4（qwen21t ~0.01 CU/条）/ T4（zimage ~0.02 CU/条）。

## 视频三模式（H3 · h3turbo/h3pika 配方 · A100 · 1344×768·124f·5s 带音频）

- `t2v/t2v_pika_seed43.mp4` — 文生视频·Pika 6步（热跑 102s ⭐）
- `fl2v/` — 首尾帧·官方 turbo 4步 768p（92s ⭐）+ 帧图源（无水印版）
- `ref2v/` — 人物参考·Pika 6步（107s ⭐）+ 参考图源（汉服少女 + 东方巨构场景）

## 生图·H3 全模态（h3image 配方 · A100 · 6步 CFG-free 免 LoRA）

- `image/t2i_hanfu_1024.png` — 文生图 1024²（95s）
- `image/edit_beach_before.jpg → edit_beach_after.png` — 编辑：雪地→沙滩（猫主体保持）
- `image/edit_neon_before.png → edit_neon_after.png` — 编辑：汉服→雨夜霓虹街

## 生图便宜档·Qwen-Image-2.1-Turbo（qwen21t 配方 · L4 · 8步 ~21s/条）

- `qwen21t/t2i_cat_snow_1024.png` — 文生图（冷跑 21s，含全部模型装载）
- `qwen21t/t2i_poster_cn_text.png` — 中文海报「雪夜归人」+ H3 STUDIO 2026（文字渲染零错字 ⭐）
- `qwen21t/edit_beach.png` — 编辑：汉服女→金色沙滩（人物保持）
- `qwen21t/bgremove_rgba.png` — 透明抠图（RGBA PNG，透明区 RGB 噪点=正常）
- `qwen21t/compose_scene_person.png` — 多参考合成：画布巨构 + 人物材料 ⚠️ 已知限制=人物面部还原有限（调优抓手见 test-log）
- `qwen21t/inpaint_marked_crane.png` — 局部编辑（标记式）：红色标记区→白鹭群，标记外零扰动
- `qwen21t/t2i_transparent_cup.png` — t2i 直接透明生成（茶杯，10s）

## 生图便宜档·Z-Image-Turbo（zimage 配方 · T4 · 8步）

- `zimage/t2i_cat_snow_1024.png` — 文生图 1024²（冷跑 2m07s）
- `zimage/t2i_hanfu_832x1216.png` — 竖版 832×1216（热 53s ≈0.02 CU）
