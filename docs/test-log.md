# H3 测试数据册（test-log）

> 用途：每次实测的时长/消耗/产物留档，_kernel 选型与成本模型的唯一数据源_。
> 记录纪律：跑完一条立刻补一行；会话收工（h3ctl down）后回填"会话 CU"。
> CU 读数：colab status / 下站后余额差；耗时以 h3ctl gen 输出与 /status 为准。

## 字段说明

| 字段 | 含义 |
|---|---|
| run | 会话内序号（首条=含模型装载，续条=热跑） |
| 档案/臂 | weights_profile + 方案名（omni / pika / pdd / v2） |
| 模式 | t2v / fl2v / ref2v |
| 参数 | seed · 分辨率 · 帧数 · 参考图 |
| 纯推理 | ComfyUI 采样耗时（/status progress 或日志 it/s × 步数） |
| 端到端 | 提交→成片落地（含 TE 编码/VAE 解码） |
| 会话 CU | 本次会话总消耗（下站后读数） |

## D1' 已归档数据（2026-11-22，详见 plan-nocomfy.md D1' 节）

| run | 臂 | 模式 | 参数 | 纯推理 | 端到端 | 产物 |
|---|---|---|---|---|---|---|
| 1 | v2 | t2v | seed42 · 1344×768 · 124f | 103s | 236.5s（首条含装载） | outputs/d1shell_…/3.2MB |
| 2 | pdd8 | t2v | 同上 | 103s | 312s（首条含装载） | outputs/d1pdd8_…/3.1MB |
| 3 | pdd4 | t2v | 同上 | **47s** | ~2.2min | outputs/d1pdd4/4.7MB |
| 会话 CU | — | — | — | — | **4.05** | 余额 168.32 |

## D2'：ref2v 人物参考 A/B（Omni vs Pika，单档案分会话）

> 统一条件：同人物参考图（ref_char_front.jpg + 可选侧脸）+ 场景图 · seed 42 ·
> 1344×768 · 124f（5s）· ref_image_size=match · 同提示词。
> 裁决维度：人物身份保持 / 服装细节 / 场景还原 / 运动自然度 / 音频。

### 统一测试提示词（两臂逐字相同，2026-11-22 定稿 v2：国风人物 + 国风东方巨构）

```
subject_definitions:
<Subject 1> is the person in <Picture 1>: fully_preserved face, black high ponytail, ivory-white hanfu robe with crimson waist sash and a silver phoenix hairpin.
<Subject 2> is the setting in <Picture 2>: background_reference only, colossal oriental megastructure at golden hour.
shot: The woman walks slowly forward along the wet stone steps, camera pushes in past her shoulder toward the pagoda, golden hour, cinematic.
```

### 会话 A：h3omni（OmniRef 8步 euler）

| run | 模式 | 参数 | 纯推理 | 端到端 | 会话 CU | 产物 | 备注 |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | 待跑 |

### 会话 B：h3pika（Pika Flex 6步 lcm+ManualSigmas）

| run | 模式 | 参数 | 纯推理 | 端到端 | 会话 CU | 产物 | 备注 |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | 待跑 |

## 成本模型折算（随数据更新）

| 臂 | CU/条（推算公式） | 100 CU 月产能 | 状态 |
|---|---|---|---|
| v2 8步 t2v | 0.26 | ~380 | 实测 |
| pdd 4步 t2v | 0.11 | ~700 | 实测 |
| omni ref2v | 待测 | 待测 | D2' |
| pika ref2v | 待测 | 待测 | D2' |
