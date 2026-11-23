# Agent 操作手册（给 ZCode / 日常指令模板）

本手册描述日常如何用一句话指挥 agent 完成出片/出图全流程。所有命令在项目根目录执行。
**v0.1.2**：视频三模式 + 生图全通（h3image × A100 / qwen21t × L4 / zimage × T4 便宜档）；架构=三层（VM 独立 / 方案按需加载 / 生成独立）。

## 环境前置（只做一次）

```bash
uv tool install google-colab-cli          # colab CLI（export PATH="$HOME/.local/bin:$PATH"）
brew install go && (cd h3ctl && go build -o /usr/local/bin/h3ctl .)   # h3ctl 控制端
```

HF token 放 `colab/hf_token.txt`（gated 权重）。`h3ctl stations` 随时看站点矩阵。

## 日常指令 → 实际动作（h3ctl 主路径）

| 对 agent 说 | agent 执行 |
|---|---|
| 「拉起视频站」 | `h3ctl up h3turbo h3`（裸 VM+按需加载 ~13min；自动开隧道；分段计时）|
| 「抢位拉起」（A100 503 时） | `h3ctl up --wait h3turbo h3`（20 次×60s 抢位）|
| 「H3 现在什么状态」 | `h3ctl status h3`（GPU/外壳 phase/队列/隧道）；详细 `h3ctl logs h3 --follow` |
| 「出一条视频：〈提示词〉」 | `h3ctl gen --mode t2v --recipe pika --prompt "<提示词>" --seed 42 --name <短名>` |
| 「用首尾帧出片」 | 上传帧图 → `h3ctl gen --mode fl2v --keyframe first.jpg=0 --keyframe last.jpg=-1 --prompt "…"`（配方 turbo4 最快 92s）|
| 「用参考图出片」 | `h3ctl gen --mode ref2v --ref 人物.jpg=image --ref 场景.jpg=image --prompt "subject_definitions: …"`（pika 配方；最多 3 张 --ref）|
| 「出一条长镜头」 | 同上加 `--seconds 12.25 --width 768 --height 1344`（9:16 竖屏；外壳自动对齐官方帧格点，294 帧 ≈ 7.5min）|
| 「生一张图」 | `h3ctl load h3image h3` → `h3ctl gen --mode t2i --prompt "…" --width 1024 --height 1024` |
| 「编辑这张图」 | `h3ctl gen --mode edit --ref 源图.png=image --prompt "改 X，保持主体不变"`（源图先压 ~1024）|
| 「批量出 N 条」 | 循环 `h3ctl gen`（换 seed 抽卡；同参重交=缓存命中秒回）|
| 「换个方案」 | `h3ctl load <档案> h3`（盘上已有文件时 ~1-3min 纯换表）|
| 「盘上有哪些方案」 | `h3ctl loaded h3` |
| 「VM 日志看看」 | `h3ctl logs h3 --file setup`（或 comfy/api/err/daemon/all；--follow 跟踪）|
| 「收工」 | **只听用户显式指令** → `h3ctl down h3`（VM 盘销毁，产出已即时下载本地）|
| 「VM 还开着呢」 | 闲置 ~10min 无动静时主动提醒用户（铁律：销毁永不擅自执行）|

## 生成契约（h3api 外壳, 8188；ComfyUI 原生界面 8189）

```
h3ctl gen --mode t2v|fl2v|ref2v|t2i|edit   模式（档案决定可用集）
          [--recipe pika]                   配方变体（如 h3turbo 内官方 turbo ↔ pika）
          --prompt "…" --seed N --seconds 3~15  时长；外壳自动向上取整到官方帧格点 17k+5
                                           （73/90/107/124 … 362 帧 = 3.04 … 15.08s，改时长不必算帧数）
          --keyframe 图=0|-1（fl2v） --ref 图=image|video|audio（ref2v 1~3 张 / edit 1~2 张，可重复）
          [--lora 文件名] [--steps N] [--lora-strength 1.0]（turbo fl2v 切档）
          [--width/--height] [--json]       产出自动下载 outputs/<name>_<时间戳>/
```

节点寻址/工作流 JSON 全部封装在外壳 TEMPLATES 内，agent 永不接触。
提示词建议（ref2v/edit）：`subject_definitions:` + `<Picture N>` 位置标记（官方格式）。
ref2v 参考图顺序即 `<Picture 1..3>`（人物板在前、场景板在后是实测稳定写法）；单参考时外壳自动拆掉空槽。

**ref2v 人物一致性实操（短剧量产沉淀，2026-11-23 守夜人 EP01 九条长场次）**：
- 服装细节必须在 `subject_definitions` 里**逐件写死颜色**（只写外层风衣+手套，内搭会随机漂成白衬衫/蓝马甲）
- 场景背景若与参考图空间逻辑对不上（门外走廊 vs 房内），**直接写成暗场**："corridor swallowed in deep darkness, only silhouettes and a faint distant glow"——看不见即不穿帮
- 远景有人脸的镜头用 **768×1344**（384×704 远景脸约 20px 必崩）；全近景/特写草稿档 384×704 够用且快 4~5 倍
- 道具朝向要显式锁（看表镜头加 "dial facing the camera dead-on with the XII upright"）
- 同 seed 换分辨率=**重新生成的新 take**（非放大），需要重新过目

## 常见问题排查（agent 排查顺序）

1. **隧道不通**：`curl -s localhost:8188/health` → `h3ctl tunnel h3` 重开（8188+8189 双转发）
2. **A100 分配 503**：`h3ctl up --wait` 抢位模式；高峰期常态
3. **生成报缺模型/节点**：`h3ctl logs h3 --file err` 看外壳报错；档案不对就 `h3ctl load` 正确档案
4. **速度突然变慢**：`h3ctl status h3` 确认 A100-40GB；L4/T4 会被 gate 直接拦（视频档案）。长镜头（>124 帧）与 768×1344 都会线性拉长采样（294 帧 ≈ 7.5min 属正常，不是故障）
5. **CU 不足**：`colab usage` 取权威余额（README 成本速查同口径）；省钱=批量出片摊薄冷启动 ~1.2 CU
6. **ComfyUI 卡死**：`h3ctl down` 后重开（外壳 poll 已有异常保护+僵尸 20min 兜底，不会堵死收结果）

## 权限与安全

- hf_token.txt 与 OAuth 凭据（~/.colab-cli-oauth-config.json、~/.config/colab-cli/）**不要**提交到任何仓库
- ComfyUI 只绑 127.0.0.1，经 SSH 隧道访问，无公网暴露
- 模型内置内容审核；遵守 Community License（美/欧/英/韩需申请表）
