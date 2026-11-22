# 落地规划：视频线 agent 闭环（v0.3 · 重定案 v2）

> 制定：2026-11-22 · **重定案：2026-11-22 D1 实测后（用户拍板弃 DiffSynth 路线）** · 目标版本 v0.3.0 · 分支 v0.3
> **新决策口径**：ComfyUI 视频线**保留为内核**（唯一能跑 8-step 蒸馏权重的运行时），
> v0.3 的全部产出改为「**agent 契约外壳**」——agent/控制台只面对干净契约，永不再碰节点图。
> 生图线（zimage/qwen21t）不动；v0.2 分支保留。
> 四条硬性要求不变：启动作为一个操作 / 记录启动时间 / 优先直接下载 / 多次生成只传参数。

## D1 实测结论（2026-11-22，A100 会话实耗 ~4.8 CU 换来的事实）

1. **FastH3 8-step v2 蒸馏权重只有 ComfyUI 能跑**：蒸馏期给每个注意力块新增 `attn.to_gate_compress`
   模块（50 块 × Unexpected keys），DiffSynth 2.2.3（已是最新）无此实现 → strict 加载失败。
   ComfyUI H3 节点实现了它 → **8-step 3min/条 ≈ 0.26 CU/条 仍仅 ComfyUI 可达**
2. **DiffSynth hash 识别可 hack 但无意义**：model_hash = 状态字典 key 串 md5（非文件字节，
   `diffsynth.core.loader.file.hash_model_file`），四文件 alias 注册补丁已打通
   （`colab/server_h3.py` 归档保留此逻辑），但架构缺模块，注册了也加载不了
3. **FastVideo 原生运行时不适配 A100**：文档化路径仅 Blackwell(NVFP4)/Ada(FP8 分层流送)，
   Ampere 未提及；原生 `FastH3-8-Step-V2` 仅 T2V（模型卡明说 FL2VA/Ref2VA 未蒸馏）；
   Triton 后备未验证。反而 `FastH3-Comfy` 重打包版是蒸馏最全的（T2V/FL2VA 皆有）
4. **DiffSynth 可跑的替代 = 底模 INT8 50 步**（Comfy-Org 重打包，hash 原生注册零补丁），
   ~15-18min/条 ≈ 1.0-1.6 CU/条，比 8-step 贵 4-6× → **经济上否决**
5. **基建全部验证通过**：server 骨架（9 端点）、bootstrap 双运行时、agent 契约 CLI、
   守护重启、session.json 四阶段时间戳、DRYRUN 冒烟全绿——唯独模型加载层死（已拆机）
6. lightx2v Turbo = LoRA 768p BF16 **8 步**（DiffSynth 文档"4步"与模型卡不符），套 INT8 底模未知数多，缓
7. 工程坑重犯记录：colab exec 静默 30s 超时（坑①同源）——长活必须 nohup+轮询；
   DiffSynth hash 算法见 #2
8. 后续可选专项：移植 ComfyUI 的 `to_gate_compress` 到 DiffSynth（保 8-step 且去 ComfyUI），
   或盯 DiffSynth release；L4(Ada) 试 FastVideo FP8 流送当低成本档

## v0.3 新路线：agent 契约外壳（ComfyUI 为内核）

```
控制层（本机 macOS）:  colab-cli → SSH 隧道（本地 8188）→ 契约外壳（VM 8188）
VM:                    契约外壳 h3api（stdlib ~250 行, 对外唯一口 8188）
                         ├─ /generate(mode/prompt/seed/…) → 注入 workflows/*.json 模板 → ComfyUI(8189) /prompt
                         ├─ /state /status /result /files → 翻译 ComfyUI /queue /history /view
                         ├─ /events SSE → ComfyUI 轮询翻译成事件流（快照+流沿用）
                         └─ session.json 四阶段时间戳（外壳统一写）
                       ComfyUI + 8-step INT8 权重 + 守护循环（v0.2 原样, 端口改 8189 内部）
```

- **端口反转**：外壳占 VM 8188（对外唯一口，隧道/CLI 零改动），ComfyUI 内部改 8189
- 节点寻址（`--set "5.text=..."`）封装进外壳模板，agent 契约参数不变
- 启动时间记录：bootstrap 已有时序 + 外壳补 session.json（ready_at = 外壳+ComfyUI 双探活通过）
- 崩溃恢复：ComfyUI 守护沿用（坑⑰修复版）；外壳自身极简、挂了 daemon 一并拉起

### 站点矩阵（配置化启动，2026-11-22 用户拍板："不同的方案做成不同配置，起 VM 按需做"）

> 三条事实地基：①权重直连 HF 最快（CDN 52-156MB/s；GCS 跨区 13-25、Drive 最慢已移除）——
> 档案即"按需下载清单"，冷启动只拉本档案增量；②MODEL_PROFILES + MACHINE=/WEIGHTS= 底座已有，
> D1' 验证过同 VM 双档案热切换；③补三件套：机型×档案硬校验（gate）、新档案登记、磁盘安全。

| 档案 | 机型 | 用途 | 内核方案 | 增量下载 | 单条成本 |
|---|---|---|---|---|---|
| `h3video` | A100 | 视频·基线 | FastH3 v2 8步 | ~55GB（含共享 TE/VAE） | ~0.26 CU |
| `h3pdd` | A100 | 视频·**t2v 快产**（47s/条 ⭐D1'） | PDD Acc 4步 | DiT+LoRA ~25GB | **~0.11 CU** |
| `h3omni` | A100 | 视频·**全能轻量档**（单 DiT 通吃 t2v+ref2v，下载最少） | OmniRef 8步 | Omni DiT 22.2GB | ~0.26 CU |
| `h3pika` | A100 | 视频·**ref2v 人物参考**（112s/条 ⭐D2'） | Pika Flex 6步 | DiT 22GB + LoRA 2GB | ~0.2 CU |
| `zimage` | T4/L4 | 生图 | Z-Image Turbo 8步 | ~19GB | ~0.025 CU/张 |
| `qwen21t` | T4/L4 | 生图编辑 | Qwen Turbo INT8 8步 | ~17.3GB | ~0.03 CU/张 |

- **启动一行字**：`MACHINE=A100 WEIGHTS=h3omni bash scripts/h3_up.sh h3ref`（生图：`MACHINE=T4 WEIGHTS=zimage … h3img`）
  或 Go 版：`h3ctl up h3omni h3ref`（机型自动跟矩阵，无需传）
- **gate（bootstrap stage_gate）**：视频档案 min 70GB 显存——不匹配直接止步省 CU；
  **L4 量化账全查实（2026-11-22 追问"量化版本"，HF blobs 实测 + comfy-quants 文档）**：

  | 格式 | Omni DiT | Trim DiT | TE | L4(Ada 24G) 判定 |
  |---|---|---|---|---|
  | INT8 convrot | 22.2GB | 18.9GB | 27.1GB | DiT 勉强/TE 超长 → 流送 |
  | FP8 | 23.0GB（FastH3-Omni-FP8-Comfy） | 19.7GB | 无 FP8 TE | **同为 1 字节/权重，不省显存**（价值=Ada FP8 算力提速）；官方 4090 档=FP8+layerwise offload |
  | NVFP4 | **13.8GB**（Omni QAD-Comfy，14.2万下载） | 11.9GB | 15.7GB | **真能装下**，但 comfy-quants 实锤：非 Blackwell 走 dequant 回退 → **速度与显存双失效**（展开 bf16 计算），L4 上白搭 |

  结论：L4 死结不变——1 字节量化救速度不救显存，半字节量化（NVFP4）被 Blackwell 独占锁死。
  L4 实验档 = `--force`/`H3_GATE_OVERRIDE` + FP8/Omni-Comfy + ComfyUI 自带 offload，预期流送，速度未测。
  Trim（砍 8/50 块）INT8 18.9GB 是 L4 上唯一 DiT 有激活余量的官方权重，但 Trim 无 Ref2VA 保证。
- **磁盘安全**：视频 DiT ~22GB/个、四档案同目录可堆 ~130GB → stage_weights 空间 <45GB 自动清非本档案 DiT
  （可重下，换档案重跑 setup 本就会拉回）；`.done_<档案>` 标记配套加文件齐全校验，防清理后标记失真
- **模板接线（D2' 实施）**：h3api TEMPLATES 按档案选表（config.json 已带 weights_profile）——
  h3omni/h3pika 的 ref2v 模板建好后挂进各自的表，agent 契约（mode/prompt/seed…）不变
- **TaoMate-H3 3步复检（2026-11-22 用户问"能否做进去"，推翻旧"判死"表述）**：
  社区转化已成熟（Robert1212star/TaoMate-H3-3Step-ComfyUI，11.3k 下载）：官方 T2AV(2.48GB) + FL2VA(1.24GB BF16)
  两个 checkpoint 均转成标准 ComfyUI LoRA（rank128/alpha128、208 A/B 对、含 token_refiner，纯格式转换零训练）→
  放 models/loras/ + LoraLoaderModelOnly 即用。**接入成本极低**：h3tao 实验档 = h3pdd 的 fl2va 底模+TE/VAE 复用
  + 1.24GB LoRA + fl2v 两段式模板（社区配方：前段高步数 + 后 3 步 TaoMate 收尾）。
  **三个诚实警示**：①官方语义绑流式 runtime（adapter_config 带 runtime_contract：分块 KV 缓存状态机），
  转化者自认"单条 3 步≠官方去噪流程、未独立验证"；②base=全量 BF16，配 pruned INT8 未验证（Pika 同构先例可跑）；
  ③社区口碑"无 turbo 过火感"但"静态/慢速远好于快速运动"。**战略错位**：官方 Ref2VA 适配器仍未发布
  （9/8 预告 on the way）→ ref2v 主场景零收益；3 步省时同样撞 ref2v 参考开销墙。
  **结论：h3tao 实验档低优先级**，等 fl2v 提速需求或官方 Ref2VA 发布再实测（~2-3 CU）。
- **内核选型定案（2026-11-22 D3'，质量三案同档后的路由规则）**：
  **t2v → h3pdd**（4步 47s/0.11CU）· **ref2v → h3pika**（6步 112s 最快）· **Omni=轻量全能档**（单 DiT 22GB 通吃）；
  同站多方案共存由三层架构承载，任务级切换 = `h3ctl load`（盘上已有文件时 ~40s 纯换表）
- GCS 桶（93GB，~$2/月）已休眠为兜底；是否删桶由用户拍板（账单侧）

### 三层架构（v0.3.1，2026-11-22 用户拍板"启动/加载/生成各自独立，换方案按缺失增量补"）

```
层1 VM 生命周期    h3ctl vm <会话> --machine A100      裸 VM: 建站+引导+ComfyUI+外壳, profile=none 不装权重 (~2-3min)
层2 方案按需加载   h3ctl load <档案> [会话]            差异增量: HF snapshot 只拉缺的 + 档案级 marker 秒过已有
                                                      → symlink → ComfyUI 重扫 → 外壳换模板表 → READY
                   h3ctl loaded [会话]                 盘上方案清单(dirs 占用 + .done markers)
层3 生成           h3ctl gen（不变, 经外壳契约）
糖  h3ctl up <档案> [会话]                             = vm + load 一键流（入口保留）
```

- **切换实测依据**：switch 增量 24GB 仅 1m39s（vs 新站 7m29s）——load 是 switch 的正规化（weights 单阶段跑，
  不动 daemon，不碰全量 setup）
- **裸 VM 模式**：bootstrap `_PROFILE="none"` → stage_weights 直接跳过；外壳以 v2 兜底模板表启动，
  load 后换真表（config 启动时一次性读取 → 外壳必须重启）
- **多档案共存**：同站视频(55GB)+生图(19/17GB) 天然兼容（236GB 盘）；生图走 ComfyUI 直连(8189)不经外壳；
  磁盘 <45GB 时 _prune_foreign_dits 自动清非活跃 DiT（可重下）
- **zimage / qwen21t 接入**：档案已在 MODEL_PROFILES，load 机制零改动可用——后续生图线 = `h3ctl load zimage`
  → v0.2 生图 CLI 直连；h3ctl gen 的生图契约（--workflow 模式）留待生图站实测时补

### h3ctl（Go 本地控制端，v0.4 提前落地 v0.1.0，2026-11-22 用户拍板"脚本体感不好"）

> 定位不变（本地侧=胶水代码：subprocess 编排 colab-cli/ssh + HTTP 客户端/反代 + go:embed 面板页，
> 纯 stdlib 零依赖）；`h3ctl/` 目录 ~1100 行 8 文件，`go build` 单二进制，GOOS=windows 可交叉编译。

- **命令**：`stations`（站点矩阵）/ `up <档案> [会话]`（建VM→传引导→提交setup→轮询READY→自动后台隧道，
  全程分段计时）/ `status` / `tunnel`（前台）/ `gen`（对齐 h3_generate.py 全参数，含 --keyframe/--ref/--json）/
  `down`（停隧道+释放）/ `serve`（网页薄视图 :8090，反代契约端点+SSE 透传+token 鉴权预留）
- **守卫三件套**（2026-11-22 真实事故换来的）：①`guardPositional` —— Go flag 包遇首个位置参数即停解析，
  `--machine L4` 放档案后面会被吃成会话名绕过预检（本次事故把它当会话名误建了一个 A100，即时发现即时释放，
  损失 ~0.1-0.2 CU）→ 所有命令 Parse 后强制过闸；②`validSession` 会话名白名单；③`--dry-run` 只出计划不建资源
- **隧道管理**：pidfile（`~/.h3ctl/<会话>.tunnel.json`）+ signal 0 探活，up 自动后台开、down 统一停，
  幂等复用活隧道（对齐坑㉖：单会话双 -L 转发 8188+8189）
- **面板页**：薄视图落地版 —— 外壳 phase 卡 + T2V 快捷生成表单 + 任务表（进度条/耗时）2s 轮询；
  fl2v/ref2v 素材上传留 CLI（agent 优先原则），收工按钮不进面板（危险操作）
- 冒烟记录：build ✅ / stations ✅ / gen --help ✅ / 守卫 4 场景 ✅ / serve 页面+反代+token ✅；
  未冒烟（需真会话）：up 全链路、gen 端到端 —— 随 D2' 实战验收

### 文件改动清单（v0.3 新路线）

| 文件 | 动作 |
|---|---|
| `colab/h3api.py` | **新增**：契约外壳（stdlib，翻译层 + 事件流 + session.json） |
| `colab/server_h3.py` | **归档保留**：DiffSynth 直驱版（含 hash 注册补丁逻辑，DiffSynth 未来支持 v2 时可复用） |
| `colab/bootstrap_h3.py` | h3video 回归 comfyui 运行时（server 分支保留归档；ComfyUI 端口 8188→8189） |
| `scripts/h3_generate.py` | **不变**（同一契约；今天已写好的 agent CLI 直接用） |
| `scripts/h3_tunnel.sh` 等 | 不变（8188 = 外壳） |
| `workflows/*.json` | 回归为外壳内部模板（t2v/fl2v/ref2v 三张） |

### 里程碑（重排，预算重置 ~3.5 CU）

| Phase | 内容 | 产出 | CU 预估 | 状态 |
|---|---|---|---|---|
| D1' | 外壳骨架 + T2V 打通（ComfyUI 内核 8-step） | agent 闭环首条出片 | ~1.5 | ✅ 2026-11-22（236.5s 全链路，3.2MB 有效 MP4） |
| D1'+ | PDD 8/4 步 A/B（h3pdd 档案） | 内核升级候选数据 | ~2.5 | ✅ 2026-11-22（4 步推理 47s 砍半） |
| D2' | **Ref2VA 双臂单档案分实测（2026-11-22 重排，用户拍板：v2 基线裁撤、Omni/Pika 不合站、分会话跑）**
      h3ctl up h3omni → ref2v 出片 → h3ctl down → h3ctl up h3pika → ref2v 出片 → h3ctl down；
      统一条件：同人物参考图 + 场景图 · seed 42 · 1344×768 · 124f · 同提示词；
      数据入 docs/test-log.md（时长/CU/产物逐 run 留档） | 两臂身份保持对照 + CU/条 | ~6（两会话） | 待办（前置：用户生成参考图） |
| D3' | 质量定论 → 内核选型 + 文档回填 + 合并决策 | 定稿 | ~0.5 | **✅ 2026-11-22：ref2v 三片（PDD/Omni/Pika）用户裁决=三案同档；内核定案=按任务路由**（t2v→PDD 4步 47s / ref2v→Pika 112s / Omni=单DiT全能轻量档；多方案共存由 v0.3.1 三层架构承载）；合并决策等用户指令 |

### D1' 实测数据（2026-11-22 · 会话实耗 4.05 CU · 余额 168.32）

| 臂 | 步数 | 纯推理 | 首条总耗时 | 产物 |
|---|---|---|---|---|
| FastH3 v2（外壳内核） | 8 | 103s（12.86s/it） | 236.5s | d1shell 3.2MB |
| PDD LoRA 8步 | 8 | 103s | 312s（含装载） | d1pdd8 3.1MB |
| **PDD LoRA 4步** | **4** | **47s** | ~2.2min | d1pdd4 4.7MB |

- **PDD 4 步 = 推理砍半**（blocks=8,8,8,8 头库重组，fingerprint 校验过）→ 续出折算
  **~0.11 CU/条 vs 0.26**，月产能 ~380 → ~700 条
- PDD 8 步与 FastH3 v2 同速 → 8 步档无速度优势，价值在质量对比；**质量裁决等用户肉眼**
- 今日修坑 6 个（坑㉕-㉘ + CLI subfolder + 日志双行），实录见 setup-log §12
- 基建验收：外壳 9 端点契约全通；agent CLI 零改动即用；双进程守护正常；
  同 VM 双档案热切换（h3video→h3pdd 仅重跑 setup）

### D2' 前置调研：Ref2VA 双方案（2026-11-22 · 用户点名 pika h3 flex + fasth3 omni）

> 背景：T2V 三臂肉眼难分（用户裁决），质量验收改走 ref2v 人物参考。
> 两个方案都比"v2 仅 T2V 蒸馏"（D1 结论 #3）更新，都补上了 Ref2VA 专项训练。

**1. Pika H3 Flex**（Pika Labs 对 H3 后训练 · ComfyUI 官方模板，需 ComfyUI ≥0.44.0）

- 定位：fine-tune 增强而非纯加速；两个任务 LoRA（FL2VA / Ref2VA），**6 步**
- 资产（Comfy-Org/MiniMax-H3 重发布，文件名已核实）：
  `minimax_h3_ref2va_pruned_int8_convrot.safetensors`（base DiT ≈22GB）
  + `loras/pika_h3_flex_ref2va_v1_bf16.safetensors`（LoraLoaderModelOnly 强度 1.0，勿动）
- 采样：KSamplerSelect **lcm** + **ManualSigmas** `1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0`
  （7 值 = knots 1000/750/500/250/100/40/0；官方警告：通用 Euler 不等价，sigma 少于 7 值直接破坏调度，**不得叠加任何 turbo LoRA**）
- 参考上限 12（9 图 + 3 视频 + 3 音频；视频/音频各 2-15s 且同类型合计 ≤15s）；帧网格 17n+5
- 提示词要求结构化：`subject_definitions:\n<Subject 1> is ... in <Picture 2>`（官方模板实文如此）
- 社区口碑：Ref2VA 身份保持被点名（"strong identity carryover"）；~8s/step ≈ 60s/条；1080p 比 30 步底模快 380% 且细节更好；长片（>8s）耗时显著上升
- 许可：MiniMax H3 Community License（NOTICE 要求）；官方文档 `docs.comfy.org/tutorials/video/pika/pika-h3-flex`

**2. FastH3 OmniRef**（FastVideo 2026-10-20 发布 · ComfyUI day-0 支持，月下载 69.9 万）

- 定位：**官方 Ref2VA 蒸馏答案**——同 8 步预算补上 reference path（推翻"D1 结论 #3 的旧世界"）
- 资产（FastVideo/FastH3-Omni-Comfy）：`fasth3_omniref_8step_pruned_int8_convrot.safetensors`（22.2GB 单 DiT，零参考=T2V 也通用）
- 采样：euler 8 步 CFG1 + SigmaShift(12,3)——**与我们现役链完全同构**，模板仅多两个可选加速节点：
  ModelAttentionBackend("comfy kitchen attention") + BlockSparseAttention("vsa" 10%)
  （VSA 在 1344×768/362 帧 ~2× 提速，短片收益小；A100 无官方背书但 INT8 convrot=现役格式，首跑可先不挂）
- 参考寻址：提示词位置标记 `<Picture 1>`/`<Video 1>`/`<Audio 1>` + subject_definitions；12 参考上限同 Pika
- 参考尺寸模式：`match`（压到生成像素面积，快）vs `max`（保 2048 短边保身份，代价数倍）——**人物一致性场景建议试 max**
- L40 48GB 实测博客（hexgridcloud）五个多参考案例全成立：跨参考换装、双人对话口型、五参考三镜头
- ⚠️ 模板 TE 是 nvfp4_awq（Blackwell 加速档）→ **A100 须换回现役 `qwen3vl_32b_minimax_h3_int8_convrot`**（同一仓库有此文件）

**四臂对比（ref2v · 5s · 1344×768 · 124 帧 · 同 seed 同人物参考）**

| 维度 | OmniRef | Pika Flex | FastH3 v2（基线） | PDD Acc |
|---|---|---|---|---|
| 步数 | 8 euler | 6 lcm+专用 sigma | 8 euler | 4 |
| 新下载 | 22.2GB | 22GB DiT + ~2GB LoRA | 0 | 0 |
| Ref2VA 专项 | ✅ 蒸馏+后训 | ✅ 后训练 | ⚠️ 架构兼容（v2 仅 T2V 蒸馏） | ✅ Acc LoRA |
| A100 风险 | 低（格式同现役；VSA 后端首跑不挂） | 低（INT8 base + bf16 LoRA） | 0 已验证 | 0 |
| 预估速度 | ~103-120s | ~75-90s | 103s | 47s |
| 需建模板 | 改 ref2v_api.json 换 DiT 名 | 新建（手动 sigma 表） | 现成 | 复制 ref2v + PDD 链 |

- D2' 执行顺序：一次 A100 会话先并下 46GB（坑①纪律：nohup+标记轮询）→ 建 Pika/Omni 两张模板
  （从 Comfy-Org/workflow_templates 官方 JSON 抄节点链，节点寻址封进 TEMPLATES）→
  核验节点存在（`bool(/object_info/ManualSigmas)`，坑⑦纪律）→ 四臂同参出片 → 用户裁决
- 成本：下载 ~8min + 4 模板 ×(TE 编码~60s + 推理 47-120s) + 启动装载 ≈ 1h ≈ **~5.5 CU**

---

# 以下为旧路线（去 ComfyUI 常驻 DiffSynth server）· 归档保留作决策记录

> 制定：2026-11-22 · 执行：D1 实测后按结论 #1-#4 判死归档 · 四条硬性要求与基建产出已被新路线继承

## 背景要点（调研结论，2026-11-22）

- ComfyUI 只是官方四条推理路径之一（SGLang / vLLM / **diffusers** / ComfyUI），非必需；SGLang/vLLM 官方示例要 4 GPU，Colab 单卡不适合
- 现有 ~3min/条 的速度来自 **FastH3 8-step 蒸馏权重 + INT8**，不是 ComfyUI 本身；换框架的硬前提是蒸馏加速能保留（base 模型 50 步会慢 ~6×）
- **DiffSynth-Studio**（ModelScope）：
  - `MiniMaxH3Pipeline` 纯 Python ~30 行；T2VA / FL2VA（首尾帧）/ Ref2VA（参考）三模式 + 原生音频全支持
  - 支持 **Comfy-Org INT8 convrot 权重**（`pip install "diffsynth[quant]"`，comfy-kitchen 后端）与 **NF4**（官方量化包 `DiffSynth-Studio/MiniMax-H3-NF4`，最低 ~7GB 显存，offload/tiled VAE 自动管理）
  - 支持 **LoRA 训练**（pruned 模型单卡单阶段）——与 v0.3 路线图的 LoRA 规划天然协同
- 备选：**FastVideo**（现用 FastH3 权重的娘家，`VideoGenerator` API，零质量回归但框架依赖更重）；**LightX2V**（turbo 4-step，比现 8-step 少一半，理论 ~1.5min/条，质量待验证，作为后续加速项）
- 不用换的：`--set "5.text=..."` 节点寻址、`workflows/*.json` 节点图、守护循环坑⑰那类问题——新管线里整类消失

## 目标 UX（四条硬性要求 → 落法）

| 要求 | 落法 |
|---|---|
| 启动作为一个操作 | `h3_up.sh` 外层 UX 不变；内部：装 DiffSynth → HF 直连下载权重 → 起常驻 server → READY |
| 记录启动时间 | `session.json` 记 vm_at / deps_at / weights_at（含字节数、续传与否）/ ready_at 四阶段时间戳；up 结束打印分解 + CU 成本换算（A100 5.4 CU/h × 会话时长） |
| 优先直接下载 | HF `snapshot_download` 主源（断点续传 + 并行）；GCS 仅兜底；Drive 不回归 |
| 多次生成只传参数 | 常驻 server 模型加载进显存后待命；`h3_generate.py` 只传 `--mode/--prompt/--seed/--seconds` 等普通参数；模型不重载，续出每条 ≈ 纯推理时间 |

## 消费方优先级（2026-11-22 拍板）：AI agent 为主，控制台为辅

本管线的**主用户是 AI agent**（colab-cli exec + CLI/API 直驱，fire-and-poll——现状即如此，README 架构"agent 全程接管"）；人类控制台只是薄视图（一键启动/停止/生成 + 状态展示，实际操作很少）。三个直接影响：

1. **v0.3 server API 按"agent 好用"设计**：轮询优先——`GET /state` / `GET /status` 是主契约（agent 天然 fire-and-poll），SSE 是给人类 UI 的实时视图，接口两栖支持；结构化状态机（queued/running/done/failed + 字符串错误码）；响应全 JSON、时间戳机器可读；操作幂等、无交互步骤
2. **h3ctl 范围缩小**：agent 不经过它（直驱 colab-cli + SSH 隧道 + server API），它只服务人类面板的一键操作（VM up/down 需要进程跑 colab-cli）——几百行级别，Go 定案更稳
3. **控制台降为 v0.4 低优先级薄视图**：先有 API/CLI 后有界面；CLI（h3_generate.py 等）保持为 agent 的主要操作面

## 架构（v0.3 视频线）

```
控制层（本机 macOS）:  google-colab-cli（不变）→ SSH 隧道 → server HTTP API
运行层（Colab GPU）:  常驻 server（纯 stdlib 先起，零依赖，装 DiffSynth 前就能跑）
                       ├─ 子进程编排: pip install DiffSynth → HF snapshot_download（进度→SSE 事件）
                       ├─ 同进程加载 MiniMaxH3Pipeline 进显存 → READY（时间戳由 server 统一写 session.json）
                       └─ HTTP 端点 + FIFO job 队列 + SSE /events；产出本地即时下载（不变）
生图线（T4/L4）:      ComfyUI 现状原样保留（zimage/qwen21t 档案不动）
```

### API 设计（server_h3.py，纯 stdlib ~150 行，零第三方依赖）

| 端点 | 作用 |
|---|---|
| `POST /generate` | `{mode, prompt, seed, seconds, width, height, fps, keyframes, refs}`；单卡 FIFO 排队 |
| `GET /status?job_id` | 单 job 状态 + 进度 |
| `GET /result?job_id` | 成片文件列表（下载沿用现有产出目录逻辑） |
| `POST /cancel?job_id` | 取消排队/进行中的 job（POST 即可，无需双向通道） |
| `POST /upload` | 首/尾帧、参考图（对齐现有 `colab upload` 用法） |
| `GET /state` | **全量快照**：会话状态、session.json、job 队列与历史（重连即取） |
| `GET /events` | **SSE 事件流**：启动阶段 / job 进度 / 日志尾部，事件驱动推送 |
| `GET /health` | 探活（重启循环用；READY 判据从 ComfyUI /health 换成此端点） |

- **崩溃恢复**：server 挂 → 权重已在盘 → 重启只重载模型（~1-2min），不重下载；守护逻辑降级为简单 while-restart 循环
- **批量模式**：`--prompts file` 一条命令排队 N 条，适合生产批次
- **逐 job 计时**：每条记录提交/首帧/完成时间，回填到 session.json（成本与产能数据自动积累）

### 可视化面板数据通道（v0.3 预埋，面板直接吃）

**结论：细粒度数据不用轮询——SSE 推送（快照 + 流）；轮询只留给本来就只能拉的源。**

| 数据 | 来源 | 通道 |
|---|---|---|
| 启动阶段进度（deps / 权重下载 % / 模型加载） | server | `/events` SSE，事件驱动 |
| 生成 job 进度（排队 / 步数 / 完成 / 逐 job 计时） | server | `/events` SSE（步级事件节流 ≤1s，防高频重绘） |
| 会话全量快照（状态、session.json、CU 估算） | server | `GET /state` 连接即取 + SSE 心跳 |
| VM 生命周期（up/down/错）、CU 余量 | colab-cli | 面板本地低频轮询 30-60s（Colab 侧无推送源，拉取不可避免） |

- **为什么 SSE 不用 WebSocket**：单向推送为主；SSE 就是普通 HTTP，过 SSH 隧道零配置；浏览器 `EventSource` 原生自带断线重连 + `Last-Event-ID`；取消任务用 `POST /cancel` 即可，不需要双向通道。真出现双向需求（交互式预览帧等）再升 WebSocket，端口与安全姿态不变（仍绑 127.0.0.1）
- **agent 消费方式**：不走 SSE——`GET /state` / `GET /status` 轮询即 agent 主契约（fire-and-poll 模式），SSE 仅为人类 UI 实时视图服务
- **快照 + 流**（防断线"卡在旧状态"）：连上先 `GET /state` 拿全量，再订阅 `/events` 收增量；断线重连 = 重新快照 + 续流，SSE 偶断也不丢状态
- **启动归属反转（关键架构决定）**：把「启动」从 bootstrap 脚本收编进 server 进程——server 纯 stdlib 先起来，子进程跑 pip install / 权重下载（progress 回调转成 SSE 事件），最后同进程加载模型 → READY。面板从第 0 秒起就有阶段进度流，不再"黑盒等 7-10 分钟"；session.json 由 server 统一写，口径一致。`h3_up.sh` 相应简化为：起 VM → 隧道 → spawn server → 等 READY

### 本地控制后端（v0.4 面板，选型预埋 2026-11-22）

**选型（2026-11-22 两轮修订）：初定 Python 默认 → 用户倾向 Go/Rust（客户端扩展性）→ 定案推荐 Go**

先说清事实：本地后端是**胶水代码**（subprocess 编排 + HTTP 反代 + SSE + 静态文件），语言无关——API 契约（`/state /events /generate`）已定，colab-cli / ssh 是 CLI 子进程，任何语言都能调。此前"CLI 与面板共用 lib 防漂移"的 Python 论据权重下调：本地侧就是薄胶水，真正逻辑都在 Colab server，防漂移靠 API 契约而非同语言。

| 方案 | 开发成本 | 单二进制（零环境分发） | 与前端同语言 | 仓库一致性 | 定位 |
|---|---|---|---|---|---|
| **Python FastAPI**（默认） | 最低 | ✗（PyInstaller 可补但粗糙） | ✗ | ✅ 全仓 Python | 自用网页版最省 |
| **TS + Bun**（Hono） | 低 | ✅ `bun build --compile` | ✅ API 类型前后端共享 | ✗ 第二栈 | 要分发 / 要同语言时的首选 |
| **Go**（stdlib 为主） | 中低 | ✅ GOOS 交叉编译一条命令 | ✗ | ✗ | 分发性价比最高（~400 行） |
| **Rust**（Axum） | 高 | ✅ 体积/体验最优 | ✗ | ✗ | 有学习目标才选 |
| Electron 壳 | 高 | — | ✅ | ✗ | 不推荐起步 |

**定案（2026-11-22）：h3ctl 用 Go（推荐，待最终点头）**

- **为什么 Go 优于 Rust（本场景）**：这层是胶水代码（subprocess + HTTP 反代 + SSE + 静态文件），Go stdlib 全覆盖（net/http + exec + http.Flusher）；`go:embed` 把面板静态页打进同一个二进制 → **客户端 = 一个 exe，双击即用**；`GOOS=windows go build` 一条命令交叉编译；迭代速度明显快于 Rust
- **Rust 备选条件**：将来走 Tauri 且想把后端并进 Tauri 进程（Rust 原生命令，省 sidecar），或把学 Rust 当目标——否则日常迭代成本不划算
- **Python 降级 fallback**：v0.4 若 Go 不顺手可回退，API 契约不变
- **诚实边界**：VM 生命周期靠 colab-cli（外部 Python CLI），Go 子进程调用无碍；但真做"发给别人的完整客户端"时它是要处理的尾巴（PyInstaller 打包或 Go 重实现其 API 调用），v0.5+ 议题
- **范围再收缩（消费方定位后）**：agent 不经过 h3ctl（直驱 colab-cli + 隧道 + server API），h3ctl 只服务人类面板一键操作 → ~几百行，复杂度天花板极低
- 对 v0.3 零影响（D1-D4 只动 Colab 侧）

**架构分层与核心原则：CLI 与面板共用同一本地 lib，不搞两套实现**

```
面板前端（静态 HTML/JS，本地后端 serve）
   │ HTTP / SSE
本地控制后端（h3ctl，Go 单二进制 ~400 行，go:embed 内嵌面板页，API 契约不变）
   ├─ 控制: 封装 VM 生命周期（colab-cli 子进程）+ ssh -L 隧道管理
   ├─ 转发: 反代 Colab server /state /events /generate（消费 v0.3 已定接口，不重实现生成逻辑）
   ├─ 落地: 产出文件归档 outputs/
   └─ serve: 面板静态页
scripts/h3_up.sh / h3_generate.py 等 → 退化为 h3ctl 的薄壳（CLI 与面板同源，防逻辑漂移）
```

- 本地后端端点草案：`POST /api/vm/up|down`、`GET /api/vm/status`（轮询 colab-cli）、`POST /api/session/up`、`GET /api/events`（SSE 反代）、`POST /api/generate`、`GET /api/outputs`
- **范围纪律**：本地后端属 v0.4 面板阶段；v0.3 只需保证 Colab 侧 `/state` `/events` 接口稳定（已预埋），本地侧不动。唯一可提前做的：把 scripts 里重复逻辑收编为 `h3ctl` lib（D4 文档回填时顺手，可选）
- **不用 Electron 全家桶起步**：Web 面板够用且轻；真要独立 app 再套 pywebview（Python 全栈最短桌面路径）或 Tauri sidecar，后端零改动

### 客户端形态演进（2026-11-22 拍板）

**状态**：手机暂不考虑（用户拍板，2026-11-22）；以下结论保留备查。Windows 兼容靠"网页版"天然解决，见下。

**原则：API 层不动，新形态 = 新前端。** 分层已解耦，以下全部是"套壳/加前端"级别的工作量，无架构返工。

**Windows 场景（2026-11-22 补充）**

- 控制台本身就是网页（FastAPI serve 静态页）→ **Windows 天然兼容，零安装**：Windows 机器浏览器开 `http://<Mac-ip>:8xxx` 即用（局域网或 Tailscale 私网），控制节点仍只有 Mac 一台
- 原生壳（pywebview/Tauri/Electron）本身都跨平台（pywebview 在 Windows 用 WebView2），但壳必须携带后端环境（Python + uv + colab-cli + ssh）——**这才是 Windows 兼容的真实成本，不是壳的问题**。自用不做双平台后端环境
- "零环境"Windows 客户端 = PyInstaller 打包后端成 exe + Tauri sidecar——可行但属分发级工程（面向"给没有 Python 环境的人用"），自用不值，v0.5+ 再议
- 为多机访问预留的唯一设计：h3ctl 后端支持 `--host 0.0.0.0` + token 鉴权（Tailscale 网内可豁免 token，局域网裸奔不建议）

**桌面客户端（按需，工作量半天级）**

| 路径 | 说明 |
|---|---|
| **Tauri sidecar** ⭐ | h3ctl（Go 单二进制）作 sidecar，前端复用面板静态页；体积小、体验好；Tauri 2 还能顺手出 iOS/Android |
| h3ctl 裸跑 | go:embed 内嵌前端，`h3ctl serve` 双击/开机自启即用，浏览器开 localhost——最省的"客户端" |
| pywebview | 随 Python 方案降级备查（原 Python 全栈路径） |
| Electron | 不推荐（重） |

**手机（真正的决策在连接层，不在前端）**

- 手机跑不了 colab-cli（本机 OAuth）和 SSH 隧道 → **手机不能当控制节点**，管不了 VM up/down
- **定案 A（推荐）：Mac 是唯一控制节点，手机是遥控器**——Mac 跑 h3ctl 后端 + 装 Tailscale；手机装 Tailscale 入同网后，PWA 直访 Mac 的 API。零公网暴露原则不破（Tailscale 是 WireGuard 私网，非公网映射），免费档够个人用
- 方案 B（否决）：VM 侧 cloudflared tunnel 公网暴露 + token 手机直连——破坏"零公网暴露"安全姿态，且 VM 启动仍依赖 Mac 在线，覆盖不完整
- 手机前端：**PWA 先行**——同一套静态前端做响应式 + 添加到主屏幕，SSE 原生支持；将来要上架应用商店/推送通知再评估 Tauri 2 mobile / Expo
- 现实评估：手机端需求 ≈ "在外面看进度 / 触发一条生成 / 收完成提醒"，PWA + Tailscale 半天覆盖 100%，**不建议先做原生 app**

**时序纪律**：v0.4 Web 面板 → 用起来发现真需要壳/移动端再演进（v0.5+）。不要提前建壳工程。

## 权重策略（第一期决策）

- **首选：复用现有 INT8 convrot 文件**（FastH3 8-step pruned DiT + qwen3vl_32b TE INT8 + 双 VAE）——零质量变量、下载量不变、可与 v0.2 输出**同 prompt 同 seed 直接对比**
- **待验证（D1 第一验证项）**：DiffSynth comfy-kitchen 后端能否加载 FastVideo 蒸馏 pruned INT8（命名/结构兼容性未实测）
  - 若不行 → 备选 A：lightx2v turbo 4-step（DiffSynth 文档明确支持，更少步数）
  - 备选 B：NF4 base 50 步（慢 ~6×，不推荐作主路径，仅兜底）
- 第二期：NF4 全套（更小更轻）与 4-step turbo A/B，用数据定去留
- 蒸馏步数对齐：8 步 + `shift_video 12.0 / shift_audio 3.0`（对应现 t2v_api.json 的 MiniMaxH3SigmaShift）

## 文件改动清单（v0.3 分支）

| 文件 | 动作 |
|---|---|
| `colab/bootstrap_h3.py` | 拆双运行时：视频档案 → 启动编排移交 server（装 DiffSynth/下载/加载都在 server 内做）；生图档案 → ComfyUI 现状不动 |
| `colab/server_h3.py` | **新增**：纯 stdlib 常驻 server（启动编排 + 模型加载 + job 队列 + `/events` SSE + `/state` + session.json 时间戳） |
| `scripts/h3_generate.py` | 改造：去 `--workflow/--set`，改 `--mode/--prompt/--seed/--seconds/--name`；走新 API |
| `scripts/h3_up.sh / h3_down.sh / h3_status.sh` | 小改：READY 判据换 `/health`，其余不动 |
| `workflows/t2v_api.json` 等视频工作流 | 废弃（留分支历史）；生图 workflows 保留 |
| `docs/plan-nocomfy.md` | 本文档；各 Phase 完成后回填实测数据 |

## 里程碑与预算

| Phase | 内容 | 产出 | CU 预估 |
|---|---|---|---|
| D1 | bootstrap 双运行时改造 + server 骨架（`/state` + `/events` SSE 一起落，面板预埋）+ **T2V 打通**（蒸馏 INT8 兼容性验证在此） | 首条 DiffSynth 出片 | ~1.5 |
| D2 | **同 prompt 同 seed 对比**（vs v0.2 ComfyUI 输出）+ FL2VA / Ref2VA 打通 + 生图线回归测试 | 质量对比定案 | ~1.5 |
| D3 | 批量模式 + 崩溃恢复演练（kill server → 重启只重载）+ session.json 成本回填 | 三模式全通 | ~1 |
| D4 | 文档回填（README / CHANGELOG / manual）+ 合并决策 | 定稿 | 0 |

预期冷启动：deps ~2min + 权重下载 4-6min + 模型加载 1-2min ≈ **7-10min**（少装 ComfyUI/克隆，与现 3-15min 同级或略优）；续出每条 ≈ 纯推理（与现 ~2.9min 持平，换框架不加价；4-step turbo 若验证通过可降至 ~1.5min）

## 风险清单

1. FastH3 蒸馏 INT8 在 DiffSynth 的兼容性未实测（D1 第一验证项，备选已列）
2. DiffSynth 在 A100 40GB 上 INT8 后端的稳定性；NF4 作显存 fallback
3. 蒸馏 8 步的 sigma shift / scheduler 对齐（对不齐会出现模糊/拖影，对照 v0.2 输出可快速发现）
4. bootstrap 双运行时改动不得影响 zimage/qwen21t 生图线（D2 回归测试硬门槛）
5. 路线图关系：v0.3.0 原规划（可视化面板 / LoRA / 教程）——本迁移与 LoRA 协同（DiffSynth 内置训练），面板/教程顺延；版本号归属待合并决策时定
6. DiffSynth `pipe()` 步级进度回调是否可用（D1 验证项）：有 → `/events` 推真实 step x/N；无 → 以首条实测耗时为基线估算进度（面板够用；较 ComfyUI 真实节点进度属小退化，文档注明）
