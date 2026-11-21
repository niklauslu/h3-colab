# MiniMax H3 本地部署测试报告

> 测试日期：2026-11-21 ｜ 测试人：ZCode（agent）+ 项目所有者
> 环境：Google Colab（AI Pro）· ComfyUI 0.46.0 · 权重 FastVideo-FastH3-Comfy（INT8 convrot）
> 结论速览：T2V ✅ ｜ FL2VA 首尾帧 ✅ ｜ Ref2VA 全能参考 ✅（双图参考 209s）｜ 标准 A100 + GCS 冷启动 ✅（8.5min setup / 121MB/s）｜ 2K 再生成 ❌（仅官方 API）

## 1. 测试环境

| 项 | 配置 |
|---|---|
| 运行平台 | Google Colab（AI Pro 订阅，100 CU/月）|
| GPU | NVIDIA A100（High-RAM 形状，实测 ~80GB VRAM；标准 A100 高峰期 503 见 setup-log 坑⑮）|
| 推理框架 | ComfyUI 0.46.0（fire-and-poll 引导，守护循环自动重启）|
| 权重 | FastVideo-FastH3-Comfy：DiT INT8 convrot 22.13GB + Qwen3-VL-32B INT8 编码器 27.14GB + 视频 VAE fp16 + 音频 VAE fp32 |
| 精度 | INT8 DiT（CFG 蒸馏，KSampler 8 步 / cfg 1.0 / euler / simple，SigmaShift 12/3）|
| 输出规格 | 1344×768 · 124 帧 · 24fps · 5.17s · MP4（H.264 + AAC 立体声）|

## 2. 测试用例与结果

### 2.1 T2V 文生视频 ✅
- 工作流：`workflows/t2v_api.json`（最小图 13 节点，UNETLoader→SigmaShift→KSampler→双 VAE 解码→CreateVideo→SaveVideo）
- 提示词：`A majestic orange cat walking through fresh snow at golden hour, cinematic lighting…`
- 产物：`samples/t2v/smoke_00001_.mp4`（4.0MB）
- **耗时：首次 3.5 分钟（含模型加载进显存）；缓存重交秒级**
- 发现并修复：SaveVideo 节点 API 调用需显式 `format:"mp4"`（COMFY_DYNAMICCOMBO_V3 不给默认值）

### 2.2 FL2VA 首尾帧 ✅
- 输入：AI 生成两张 16:9 静帧（黄昏正面 `frame_first.jpg` / 暮色背影 `frame_last.jpg`），ImageScale(lanczos, crop:center) 归一 1344×768
- 工作流：`workflows/fl2v_api.json`——核心节点 `MiniMaxH3ImageToVideo` 同时输出 positive + latent（替代文本编码+空latent 两节点），支持 `first_frame`/`last_frame` 可选输入
- 产物：`samples/fl2v/fl2v_00001_.mp4`（2.9MB）+ 输入帧
- **耗时：171 秒（2 分 51 秒，模型已驻留）**
- 结论：首尾帧过渡连贯，无需更换 FL2VA 专用权重分支（FastH3-Omni 参考条件版本即支持）

### 2.3 Ref2VA 全能参考 ✅
- 输入：两张参考图——角色（`ref_char_front.jpg`，银紫发角色正面）+ 场景（`ref_scene.jpg`，雨夜霓虹巷）
- 工作流：`workflows/ref2v_api.json`——核心节点 `MiniMaxH3ReferenceToVideo`（同样双输出 positive+latent），`ref_image_size:"match"` 自动适配参考图，多参考以 autogrow 列表注入：`"ref_images": [["5",0],["6",0]]`
- 提示词：`A young woman with short silver-purple hair wearing a mustard yellow raincoat walks slowly through a rainy neon-lit Tokyo alley at night…`
- 产物：`samples/ref2v/ref2v_00001_.mp4`（3.6MB）+ 两张参考图
- **耗时：209 秒**（较 FL2VA +38s：两张参考图过视觉塔编码）
- 结论：**首发即中、零试错**；角色特征与场景氛围均被引导。参考视频/参考音频入口（ref_videos/ref_audios）同机制待用户供素材再测
- 提示：本地权重为 FastH3-Omni 参考条件版本；`ref_videos` 入口可挂参考视频提取运动/音频特征

### 2.3b 标准机 A100 + GCS 冷启动实测 ✅
- 机型确认：**NVIDIA A100-SXM4-40GB**（标准规格存在，见坑⑮——高峰 503 属容量问题非配置问题）
- 冷启动分解（VM 日志时戳）：

| 阶段 | 耗时 |
|---|---|
| 装环境（git clone + pip） | 52s |
| GCS 拉权重 55.08GB | **7min38s ≈ 121MB/s**（DiT 22.1GB@134MB/s、编码器 27.1GB@111MB/s，跨区 us-central1→us-east1）|
| 软链+守护循环启动 | <1s |
| **setup 合计** | **8min30s** |
| 分配→就绪全程（含排队/挂载/上传引导） | ≈13 分钟 |

- 结论：**GCS 源冷启动达标（<10 分钟）**，无需镜像桶到 us-central1（跨区依然 100MB/s+）
- Drive 授权页每个新 VM 弹一次但**自动完成无需点击**（dfs_ephemeral 凭据，Google 侧同意已记住；400 均为竞态，重试循环兜底）

### 2.4 明确不可用项 ❌
- **2K 再生成**（H3-Regenerate-2K）与 **Context-IR**：未开源，`MinimaxHailuo03*` 前缀节点是官方 API 包装，需 platform.minimax.io 密钥
- ultra-low 量化（Q2_K 以下）：社区实测输出崩坏，勿用

## 3. 性能汇总

| 场景 | 耗时 | 备注 |
|---|---|---|
| 会话内首条（含加载）| 3.5 min | DiT+编码器+双 VAE 进显存 |
| 续出每条 | 2.85 min | 纯采样+解码+合成 |
| 节点缓存重交 | <10s | 改参数复现 |
| 冷启动（权重就位后）| ~5 min | 装环境+GCS 拉取+启动 |
| 冷启动（GCS 权重 55GB）| 待本次实测 | Drive 串行历史值 21-30 分钟 |

## 4. 成本（AI Pro：100 CU/月）

| 机型 | 费率(实测/调研) | 单条成本 |
|---|---|---|
| A100 High-RAM (~80GB) | 8.88 CU/h | ~0.5 CU |
| 标准 A100 | ~5.4 CU/h | ~0.3 CU |
| 月产能估算（标准 A100）| — | 150~260 条 |

## 5. 风险与限制

1. Colab 会话最长 24h、闲置 ~180 分钟回收 → 长期挂机不可行，批次制工作
2. A100 高峰期容量不足（503）→ 降级 High-RAM 或错峰
3. 社区许可证限制（美/欧/英/韩部署需申请）；模型内置内容审核
4. colab-cli exec 随机断连 → 全链路已 fire-and-poll 化（坑⑩）

## 6. 复现指南

见 `docs/manual.md` §3（日常 SOP）与 `samples/README.md`（每个样片的完整参数）。
