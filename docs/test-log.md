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
<Subject 1> is the person in <Picture 1>: fully_preserved face, black hair in an elegant updo with a silver phoenix hairpin, ivory-white hanfu robe with crimson waist sash.
<Subject 2> is the setting in <Picture 2>: background_reference only, colossal oriental megastructure at golden hour.
shot: The woman walks slowly forward along the wet stone steps, camera pushes in past her shoulder toward the pagoda, golden hour, cinematic.
```

### 会话 A：h3omni（OmniRef 8步 euler）· 2026-11-22 · gpu-a100-s-usc1c0-5eoi79afjnpa（A100-40GB）

**启动分解（h3ctl up --wait h3omni h3ref）**：①建VM 45s → ②上传 15s → ③提交 3s →
④服务就绪 7m04s（权重 55.2GB @ ~190MB/s，与装环境并行）→ ⑤隧道 20s → **总启动 8m06s**；
gate 40GB 放行（门槛 34GB 修正后）；磁盘余量 236GB。
（前置两次尝试失败：Colab 分配接口 503 三连 ×2 轮 + gate 70GB 误拦一次——已修，日志直出当场抓出）

| run | 模式 | 参数 | 纯推理 | 端到端 | 会话 CU | 产物 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | ref2v | seed42 · 1344×768 · 124f · 2 参考图 | 采样至 95% 用 169s（含 Omni DiT 装载） | **4m00s**（job 238s） | 会话估 2.6 CU* | `outputs/__20261122_111623/omni_00001_.mp4` 3.56MB | 95%→100% 平台 70s = VAE 首装载+解码+编码段 |
| 2 | ref2v | 同 seed42 重交 | —（同图缓存命中） | **7s** | 同上 | `outputs/omni_20261122_111715/` | ⚠️ ComfyUI 同图缓存：同 seed 同输入秒回，非真实速度 |
| 3 | ref2v | seed43（真实热跑） | 采样 138s | **2m19s**（139s） | 同上 | `outputs/omni_20261122_111959/omni_00002_.mp4` 3.16MB | 热跑基线 |

### 会话 B：h3pika（Pika Flex 6步 lcm+ManualSigmas）· 同 VM 热切换（用户要求不关站对比）

**切换分解（h3ctl switch h3pika h3ref）**：清 .setup_done → 增量下载 24GB（ref2va DiT 21.0GB +
Pika LoRA 2.0GB，共享 TE/VAE 不重拉）→ symlink → 外壳重启 → **总切换 1m39s**，VM 不重建。
（首版 switch 假成功一次：不清 marker 则 setup skip 不拉权重——已修）

| run | 模式 | 参数 | 纯推理 | 端到端 | 会话 CU | 产物 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | ref2v | seed42 · 同上全同 | 采样至 95% 用 165s（含 ref2va DiT+LoRA 装载；VAE 已热=Omni 首跑遗产） | **2m56s**（176s） | 同会话总计 | `outputs/pika_20261122_112729/pika_00001_.mp4` 2.36MB | 解码段仅 10s（VAE 显存缓存命中） |
| 2 | ref2v | seed43（真实热跑） | 采样 111s | **1m52s**（112s） | 同上 | `outputs/pika_20261122_112939/pika_00002_.mp4` 2.23MB | 6步 LCM vs Omni 8步：采样快 ~20% |

> \* 会话 CU 估算：11:01–11:30 共 29min × 5.4 CU/h ≈ 2.6 CU（CLI 无余额接口，待用户网页核对回填；
> 启动 8m + 切换 2m + 生成 10m + 余为调试/等待）。

### 会话 C：h3pdd（PDD Ref2VA 4步）· 新 VM gpu-a100-s-usw4b0-2yakgvyye27x5（A100-40GB）

**启动分解**：①建VM 19s → ②上传 45s → ③提交 3s → ④服务就绪 6m23s（权重 79.5GB：
fl2va+ref2va 双底模 + PDD LoRA×2 + TE/VAE）→ ⑤隧道 7s → **总启动 7m29s**
（⚠️ 首跑前修一个 bootstrap 缺陷：symlink 线程异常被静默吞掉导致模型目录空——已加线程异常落日志+SETUP FAILED，e5f21ff）

| run | 模式 | 参数 | 管线耗时(ComfyUI 口径) | 端到端 | 产物 | 备注 |
|---|---|---|---|---|---|---|
| 1 | ref2v | seed42 · 同 Omni/Pika 全同 | 289.66s（全链冷装载） | **4m57s** | `outputs/pdd_20261122_115106/pdd_00001_.mp4` 2.86MB | fingerprint ✓ "ref2va file on ref2va model"；nfe=4 → blocks=4×8, 258 lora modules, heads fused |
| 2 | ref2v | seed43 热跑 | **130.94s** | **2m18s**（138s） | `outputs/pdd_20261122_115348/pdd_00002_.mp4` 3.21MB | 4 步未比 8 步快——参考条件开销吃掉步数优势 |

**三方对照速览（ref2v · 真实热跑口径）**

| | PDD 4步 | OmniRef 8步 | Pika Flex 6步 |
|---|---|---|---|
| 管线耗时（ComfyUI executed） | **131s** | 139s | **112s** |
| CLI 端到端 | 138s | 139s | **112s** |
| 冷启动/切换 | 总启动 7m29s（79.5GB） | 总启动 8m06s（55.2GB） | 热切换 1m39s（增量 24GB） |
| 成片体积 | 2.86/3.21 MB | 3.56/3.16 MB | 2.36/2.23 MB |
| 关键事实 | t2v 47s 的步数优势**无法迁移到 ref2v**（参考条件 ~84s 开销） | 官方 Ref2VA 蒸馏 | 最快，社区口碑身份保持 |

> ref2v 的参考开销 ≈ 84s（PDD t2v 47s → ref2v 131s）：双参考图 TE 编码 + 参考序列过 DiT，
> 步数从 8→4 只省采样段，省不回参考段。

| | OmniRef 8步 | Pika Flex 6步 | 差 |
|---|---|---|---|
| 热跑端到端 | 139s | **112s** | Pika 快 19% |
| 首条端到端 | 240s | 176s | Pika 快 27%（含装载口径不完全对称：Omni 首条含 VAE 冷装载） |
| 成片体积 | 3.56/3.16 MB | 2.36/2.23 MB | Pika 偏小 ~30%（待用户裁决是否细节/码率差异） |
| 质量 | 待用户裁决 | 待用户裁决 | 人物身份保持为第一判据 |

## 成本模型折算（随数据更新）

| 臂 | CU/条（推算公式） | 100 CU 月产能 | 状态 |
|---|---|---|---|
| v2 8步 t2v | 0.26 | ~380 | 实测 |
| pdd 4步 t2v | 0.11 | ~700 | 实测 |
| omni ref2v | 待测 | 待测 | D2' |
| pika ref2v | 待测 | 待测 | D2' |
