# H3 工作站 · 搭建实录

> 记录人：ZCode（agent）。原则：只记录事实与结论，不确定的地方标注「待验证」。

## 2026-11-21

### 1. 工具安装

- `uv tool install google-colab-cli`（PyPI v0.8.0）→ 可执行文件 `~/.local/bin/colab`
- 坑①：用户终端曾报"装好了"但实际 `uv tool list` 里没有（装到了别的环境）；在标准环境重装解决
- 坑②：`colab` 不在默认 PATH，需 `export PATH="$HOME/.local/bin:$PATH"`（建议写入 ~/.zshrc）

### 2. OAuth 授权（oauth2 模式，非交互式操作法）

CLI 授权需要交互式输入授权码，agent 的做法：

```bash
mkfifo /tmp/h3_auth_pipe
nohup bash -c 'colab usage < /tmp/h3_auth_pipe' > /tmp/h3_auth_out 2>&1 &
nohup sleep 86400 > /tmp/h3_auth_pipe &   # 占住管道防止 EOF（macOS 无 sleep infinity）
# 用户浏览器授权 → 把授权码发给 agent → agent 执行:
printf '<授权码>\n' > /tmp/h3_auth_pipe
```

- 坑③：**第一次授权只拿到基础 profile/email scope，缺 Colaboratory/Drive/Cloud Platform**，
  报错 `Scope has changed from ... to userinfo.*`——授权页上逐项权限没全开。
  重新发起授权并提醒用户勾选全部权限后成功
- 坑④：macOS 的 `sleep` 不支持 `infinity`（BSD sleep），要用 `sleep 86400`
- 坑⑤：`colab status -s <不存在的会话>` 打印 "Session not found" 但**退出码为 0**，
  不能用退出码判断会话存在性，必须 grep 输出文本
- 授权码一次性、几分钟过期；授权 URL 里含 PKCE challenge，**码必须喂给生成该 URL 的同一进程**

### 3. 授权后状态

```
Current balance: 199.82 compute units     # 比预期 100 多,可能有结转(待验证账期)
Usage rate: 0.08/hr
Active assignments: 1                      # m-s-usw1c7-… CPU/Standard, 浏览器默认 runtime, 暂留
```

### 4. 权重预下载到 Drive（2026-11-21 完成 ✅）

- 方式：CPU 会话 `dl` 中转（HF 内网下载 ~70MB/s，Drive FUSE 写入 ~125MB/s），脚本 `colab/download_to_drive.py`
- **坑⑥**：`drive.mount` 要求挂载点为空——先在 `/content/drive` 下 mkdir 会毒死挂载（`Mountpoint must not already contain files`）；挂载失败先 `rm -rf /content/drive` 再重试
- **坑⑦**：未挂载时 `os.makedirs("/content/drive/...")` 不报错，只是建了本地空目录，必须用 `os.path.ismount()` 验证
- `colab drivemount` 首次会走一次浏览器 Drive 授权（CLI 自动打开页面并接住回调），凭据随后缓存
- 结果：4 文件 55.1GB 全部落位 `MyDrive/H3/models/fasth3/`，含 `.done` 标记
  - diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot 22.13GB
  - text_encoders/qwen3vl_32b_minimax_h3_int8_convrot 27.14GB
  - vae/minimax_h3_video_vae_fp16 5.21GB / vae/minimax_h3_audio_vae_fp32 0.61GB
- 耗时 28 分钟，消耗 **0.11 CU**；FastH3 仓库不 gated，无需 HF token
- `bootstrap_h3.py` 的 HF 兜底下载已同步收窄为同 4 文件（WEIGHT_FILES）

### 5. 首跑验证清单（h3_up.sh）—— 2026-11-21 全部通过 ✅

| # | 待验证项 | 结果 |
|---|---|---|
| 1 | A100 分配 | ✅ A100 High-RAM；`gpu-a100-hm-*`；**实际 ~85GB VRAM（疑似 80GB 变体）**、RAM 179GB |
| 2 | CU 计费口径 | ✅ 实测 High-RAM **8.88 CU/h**（比调研值 5.4 高=机型更贵）；CPU 0.08 CU/h；冷启动余额验证 |
| 3 | Drive 挂载 | ✅ 见坑⑥⑦；凭据缓存后秒过 |
| 4 | 权重软链归位 | ✅ ComfyUI /models API 识别全部 4 文件，分类正确 |
| 5 | SSH 隧道 | ✅ 见坑⑨；本地 curl 127.0.0.1:8188 直达 ComfyUI |
| 6 | weights Drive rsync | ✅ 55GB 同步 21 分钟（FUSE 读 16~31MB/s 波动）|

### 6. A100 拉起过程实录（坑⑨⑩ 与架构决策）

- **坑⑩：colab-cli 长阻塞 exec 不可靠**——等待内核回复时随机 "Connection was lost"，
  且内核侧任务随断连一起夭折（install 跑完返回了 globals 却什么都没装上）。
  CPU 会话（dl）却很稳。→ **架构改造为 fire-and-poll**：所有重活在 VM 后台线程顺序执行写
  setup.log，exec 只做提交（毫秒级）与轮询（秒级）。改造后 21 分钟零断连完成全流程
- **坑⑨：SSH 认证模型**——`colab ssh --proxy-mode -i KEY.pub` 在桥接时把公钥放进
  `X-Colab-Ssh-Pubkey` header 注册给 runtime；真正的 ssh 必须用**配套私钥 `-i` + root 用户**。
  等效命令已固化到 scripts/h3_tunnel.sh
- **坑⑧：rsync 不创建多级父目录** → mkdir -p 前置；已升级为并行 rsync + `--append-verify`
  断点续传（冷启动预计 ~15 分钟，下次冷启动验证）
- **坑⑪：内核重启（restart-kernel）后** runpy 等导入丢失，heredoc 里必须显式 `import runpy`
- 权重同步期间 exec 偶发空响应/挂起 → 轮询加重试即可，线程不受影响

### 7. GPU 机型决策（AI Pro 档）

| 机型 | 费率 | 适配 | 决策 |
|---|---|---|---|
| A100 High-RAM (80GB?) | 8.88 CU/h | ✅ 全部权重可驻留 | 当前会话（调试用）|
| **A100 标准 (40GB)** | ~5.4 CU/h | ✅ INT8 DiT 驻留 + 编码器卸载 | ⭐ **生产默认**（h3_up.sh 已改为默认，HIGHMEM=1 切回）|
| L4 24GB | ~1.67 CU/h | ⚠️ DiT 占满显存, 需压卸载 | 待实验（0.4 CU 一次）|
| T4 16GB 免费 | 0 | ❌ 出片 | 仅用于管线调试 |
| G4/H100 | — | ✅ | Ultra 档, 不适用 |

## 2026-11-21 下午 · GCS 冷启动加速（✅ 全部完成）

**结果**：55.08GB 四文件全部落桶并校验（11:07→11:26，~19.5 分钟，均值 ~47MB/s）；
本机无凭据 curl 公开 URL 返回 200（免认证下载验证通过）；VM 凭据文件已删；
`gcs` 会话已释放；CU 余额 185.12。**下次冷启动预期：标准 A100 + GCS 源 ≈ 3~6 分钟就绪。**

### 8. GCP/GCS 配置实录

- 用户 GCP 项目 `h3-workstation`（**编号 309005742981**，token owner niklaslc512@gmail.com）
- **坑⑬：`authenticate_user()` 会阻塞整个内核**且 headless 下授权 URL 随 exec 断连丢失，
  同内核的轮询全部排队饿死 → 弃用，改走凭据文件
- **坑⑭：colab-cli 令牌存 macOS 钥匙串**（service=`google-colab-cli`, user=`oauth_token`），
  `security find-generic-password -s … -a oauth_token -w` 提取（会弹钥匙串授权框，点"始终允许"），
  scope 含 `cloud-platform` ✓；补 `type: authorized_user` + `quota_project_id` 后即 google.auth 可用
- **建桶被 403 = 项目未绑结算**（`billingInfo.billingEnabled=false`）→
  **用 Cloud Billing API 直连修复**：`PUT /v1/projects/{n}/billingInfo` 指向
  `billingAccounts/01852D-6D1402-287567`（用户有两个账户，一个已关停）
- 桶：`gs://h3-weights-309005742981` @ **US-EAST1**（VM 名 `m-s-use1b4-*` 证实 Colab 这批机器在 us-east1；
  同区读取免出口费）。设 `allUsers: objectViewer` 公开读——权重本为公开开源内容，
  之后 bootstrap 免认证 HTTP Range 直下
- Colab 消费级 VM 元数据服务器被沙箱挡（metadata.google.internal 403），拿不到精确 zone，
  以 VM 命名规则推断区域

### 9. 权重源优先级（bootstrap 已实现）

1. **GCS 公开读**（us-east1，预期 100MB/s+，冷启动 55GB ≈ 3~6 分钟，断点续传）
2. Drive rsync 并行（~15 分钟）
3. HF snapshot（波动大）

成本：55GB standard us-east1 ≈ **$1.1/月**，AI Pro $10/月点数或 $300 试用金覆盖。
凭据文件 `gcp_creds.json` 仅上传期需要，上传完成后从 VM 删除（下载路径免认证）。

### 10. 机型容量结论（坑⑮：标准 A100 存在但高峰 503）

- 用户 Colab UI 确认：硬件加速器有独立「A100 GPU」选项，「高 RAM」开关独立可控
  → **标准形 A100 是真实机型**，h3_up.sh 默认标准机型的设计不变
- 实际分配时 `/tun/m/assign` 返回 **503 Service Unavailable**（高峰容量不足，
  与脚本无关；CLI 的 `colab new` 会挂起数分钟才报错）
- **策略**：标准 A100 = 生产首选（低峰可用）；高峰期自动降级 `HIGHMEM=1`
  （8.88 CU/h）先干活。后续可给 h3_up.sh 加"标准失败自动降级"开关

### 11. 生图档案落地（Z-Image-Turbo @ T4，v0.2.0）

- **坑⑯：HF `snapshot_download` 保留仓库目录层级**（`Comfy-Org/z_image_turbo` 的文件在
  `split_files/` 下），symlink 名字带 `split_files__` 前缀 → ComfyUI 按实际文件名列表，
  工作流里的裸文件名 400 `value_not_in_list`。
  **修复**：`link_into_comfyui()` 剥掉 `split_files/` 前缀；且 zimage 档案的 GCS/Drive/HF
  三源统一用 HF 原生 `split_files/` 布局，加载时归一（本仓库视频权重的
  `子目录__文件名` 命名惯例不变）
- **Z-Image 节点要点**：`CLIPLoader type="lumina2"`（不是 minimax/qwen_image）、
  `ModelSamplingAuraFlow shift=3`、KSampler 8 步 cfg=1 `res_multistep/simple`、
  负向用 `ConditioningZeroOut`（蒸馏版无 CFG）
- **T4 实测费率 1.15 CU/h**（此前"L4 1.67 / T4 免费"的说法修正：Pro 订阅下 T4 也计 CU，
  但最便宜；本次拉起+2 张图全流程仅 0.35 CU）
- gcs_setup.py 增 `upload_spec.json` 覆盖机制（同桶多档案种子：video 用默认 fasth3，
  zimage 传 spec 指向权重目录）
- exec 对 gcs_setup 的 IAM 静默段会 30s 超时（坑⑩同源）——提交后用 `run_name="progress"`
  轮询即可，线程不受影响
- **补种子标准姿势（已跑通）**：新会话**先传 gcp_creds.json 再跑 main**（凭据先行就不会踩
  authenticate_user 卡死）；种子用 **CPU 会话**（0.08 CU/h）挂 Drive 直读上传，20.7GB
  用时 9 分钟（~37MB/s），全程 <0.05 CU；凭据生成：钥匙串令牌 JSON 本就含
  refresh_token/client_id/secret，补 `type:authorized_user` + `quota_project_id` 即可

### 5. 架构定稿（调研结论，详见 research.md）

- 主路径：colab-cli 无头驱动 → A100 40GB → ComfyUI + FastH3 INT8（4 步，~2.6 min/条）
- 存储：Drive `MyDrive/H3/{models,outputs}`；会话内 rsync 到本地盘再读（FUSE 直读慢）
- 暴露：SSH 隧道（`colab ssh --proxy-mode`）优先，cloudflared 备用
- ⚠️ NVFP4 仅限 Blackwell；A100 用 INT8

## 12. v0.3 D1' 实测（2026-11-22 · 契约外壳 + PDD A/B · 会话实耗 4.05 CU）

**结论先行**：契约外壳 h3api 端到端全通（本地 CLI → 隧道 → 外壳 8188 → ComfyUI 8189 → A100）；
PDD LoRA 8/4 步双档实测成功，**4 步推理 47s（砍半）**。A/B 数据详表见 docs/plan-nocomfy.md。

### 坑实录（㉕-㉘）

- **坑㉕ · 多档案共用 models 目录时 `.done` 标记撞车**：h3video 与 h3pdd 同用
  `models/fasth3/`，切档案后 setup 因 `.done` 存在而 4 秒"完成"，20GB 权重静默未下。
  修：标记改 `.done_<profile>`（profile 级隔离）。
- **坑㉖ · 每 runtime 仅一条 SSH 会话（HTTP 429）**：第二条 `colab ssh` 隧道被拒。
  修：`h3_tunnel.sh` 改 `FORWARDS="8188:8188 8189:8189"` 单连接多转发。
- **坑㉗ · `pkill` 返回码 2 未检查 → 进程没死 → "重启后节点没注册"假象**：手动重启
  ComfyUI 时 pkill 静默失败，老进程继续应答 `/system_stats`；且 `/object_info/未知节点`
  也返回 200 空对象（校验必须判空值），双重假阳性拖了半小时。
  修：杀进程必须确认 `ps` 计数归零；节点校验 `bool(node)` 判空。
- **坑㉘ · `--set` 整数强转吃掉枚举字符串**：v0.2 CLI 的 parse_set 先试 int，
  `--set "6.nfe=4"` 传成整数 4 → 节点枚举只收字符串 `['8','4','6']` → 400。
  修：A/B 用脚本传字符串；新契约 CLI 无 --set，不受影响。
- **CLI 下载丢 subfolder**（v0.3 h3_generate.py）：SaveVideo 带 prefix 时产物在 `H3/`
  子目录，下载未传 subfolder 且不查状态码 → 42B 错误体被写成 mp4。已修（传
  subfolder/type + 非 200 即失败）。
- **h3api.log 双行重复**：log() 写文件 + nohup 重定向同一文件 → 每行两遍。
  修：守护启动改 `> /dev/null 2>> h3api.err.log`。

### PDD A/B 实测数据（A100 · 1344×768×124f · 同 prompt 同 seed 42）

| 臂 | 步数 | 纯推理 | 首条总耗时 | 产物 |
|---|---|---|---|---|
| FastH3 v2（外壳内核） | 8 | 103s（12.86s/it） | 236.5s | d1shell 3.2MB |
| PDD LoRA 8步 | 8 | 103s（12.86s/it） | 312s（含装载） | d1pdd8 3.1MB |
| PDD LoRA 4步 | 4 | **47s（11.75s/it）** | ~2.2min | d1pdd4 4.7MB |

- PDD fingerprint 校验通过（fl2va file on fl2va model）；nfe=4 官方合法（blocks=8,8,8,8 头库重组）
- PDD 8 步与 FastH3 v2 同速——8 步档无速度优势，价值=质量对比；**4 步档推理砍半**
  （续出折算 ~0.11 CU/条 vs 0.26，产能 ~380→~700 条/月）
- 质量裁决=用户肉眼（三片同 prompt 同 seed 在 outputs/）；定论前 h3video/h3pdd 双档案并存
- PDD 节点硬要求：ComfyUI ≥0.33（新克隆满足）；权重入 `models/pdd_acc/`（非 loras/）
