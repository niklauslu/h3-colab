# 样片库（v0.1.1 · 2026-11-22 更新）

> 全部为 A100-40GB 实测产出；复现参数见各目录与 `docs/test-log.md`。qwen21t/zimage 为异家族生图线（T4/L4 档）历史样片。

## 视频三模式（H3 · h3turbo/h3pika 配方，1344×768·124f·5s 带音频）

- `t2v/t2v_pika_seed43.mp4` — 文生视频·Pika 6步（热跑 102s ⭐）
- `fl2v/` — 首尾帧·官方 turbo 4步 768p（92s ⭐）+ 帧图源（无水印版）
- `ref2v/` — 人物参考·Pika 6步（107s ⭐）+ 参考图源（汉服少女 + 东方巨构场景）

## 生图（H3 · h3image 配方，6步 CFG-free 免 LoRA）

- `image/t2i_hanfu_1024.png` — 文生图 1024²（95s）
- `image/edit_beach_before.jpg → edit_beach_after.png` — 编辑：雪地→沙滩（猫主体保持）
- `image/edit_neon_before.png → edit_neon_after.png` — 编辑：汉服→雨夜霓虹街
> 异家族生图样片（qwen21t/zimage）已删除——该线退役待用 H3 生图重做。
