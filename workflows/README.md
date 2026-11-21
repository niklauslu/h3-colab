# 工作流 JSON 说明

本目录的 API 格式工作流**已预构建可直接使用**（`h3_generate.py` 的输入）：

| 文件 | 模式 | 注入点（`--set 节点.键=值`）|
|---|---|---|
| `t2v_api.json` | 文生视频 | `5.text` 提示词 / `12.seed` / `7.width,7.height,7.length` |
| `fl2v_api.json` | 首尾帧 | `9.prompt` / `12.seed`；帧图 `frame_first.jpg`、`frame_last.jpg` 传到 VM input 目录 |
| `ref2v_api.json` | 全能参考 | `7.prompt` / `10.seed`；参考图 `ref_char_front.jpg`、`ref_scene.jpg` |

三个工作流共用同一尾段：KSampler(8步 cfg1) → 双 VAE 解码 → CreateVideo(24fps, 音频) → SaveVideo(mp4)。

## 要自定义新工作流时（可选）

本地浏览器开 http://127.0.0.1:8188（隧道内）→ 载入模板或搭图 → 设置开 Dev Mode →
**Workflow → Export (API)** → 存入本目录。注意 **UI 格式 JSON 不能直接给脚本用**，必须 API 格式。

## 参考模板（官方）

- MiniMax H3: https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_t2v.json
- FastH3 三件套随权重仓库分发：https://huggingface.co/FastVideo/FastVideo-FastH3-Comfy
