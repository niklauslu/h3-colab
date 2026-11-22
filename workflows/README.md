# 工作流模板（v0.1.1）

> 全部为 API 格式 JSON，由外壳 `colab/h3api.py` 的 TEMPLATES 表引用并注入——agent 不直接使用本目录。
> 节点寻址已封装；契约见 `docs/agent-manual.md`。

## H3 视频（h3turbo / h3pika / h3omni 档案）

| 文件 | 配方 | 档案 |
|---|---|---|
| `turbo_t2v_api.json` | 官方 turbo 8步 LoRA @ fl2va | h3turbo |
| `turbo_fl2v_api.json` | 官方 turbo 4步/8步（lora_name 可注入切档） | h3turbo |
| `turbo_ref2v_api.json` | 官方 ref2v turbo 4步 @ ref2va | h3turbo |
| `pika_t2v_api.json` | Pika fl2va LoRA（lcm+专用 sigma） | h3turbo/h3pika |
| `pika_fl2v_api.json` | 同上 | h3turbo/h3pika |
| `pika_ref2v_api.json` | Pika ref2va LoRA | h3turbo/h3pika |
| `omni_ref2v_api.json` | FastH3 OmniRef 单 DiT | h3omni |
| `fl2v_tao_api.json` | TaoMate 3步（实验挂起） | h3tao |

## H3 生图（h3image 档案，6步 CFG-free）

- `image_t2i_api.json` — 文生图（`--mode t2i`）
- `image_edit_api.json` — 编辑（`--mode edit`；参考图入 conditioning，源图先压 ~1024）

## 异家族生图（zimage / qwen21t 档案，脚本流直连）

- `zimage_t2i_api.json`、`qwen21t_{t2i,edit_official,compose,rgba}_api.json`
