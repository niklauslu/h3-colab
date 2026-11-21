# Agent 操作手册（给 ZCode / 日常指令模板）

本手册描述日常如何用一句话指挥 agent 完成出片全流程。所有命令都在项目根目录执行。

## 环境前置（只做一次）

```bash
pip install uv
uv tool install google-colab-cli
colab sessions   # 首次：浏览器 OAuth 授权一次
```

HF token 放入 `colab/hf_token.txt`（gated 权重下载用）。

## 日常指令 → 实际动作

| 对 agent 说 | agent 执行 |
|---|---|
| 「把 H3 工作站拉起来」 | `bash scripts/h3_up.sh`（标准 A100 → 挂 Drive → 装环境 → GCS 三级源拉权重 → 起 ComfyUI）|
| 「A100 没位怎么办」 | `bash scripts/h3_up_wait.sh`（轮试标准/High-RAM 抢位，抢到自动拉起）|
| 「建隧道」 | `bash scripts/h3_tunnel.sh`（前台；本地 http://127.0.0.1:8188）|
| 「H3 现在什么状态」 | `bash scripts/h3_status.sh`（GPU/ComfyUI/队列/权重/日志）+ 本地 `curl -s localhost:8188/system_stats` 验隧道 |
| 「出一条视频：〈提示词〉」 | `python3 scripts/h3_generate.py --workflow workflows/t2v_api.json --set "5.text=<提示词>" --name <短名>` |
| 「用首尾帧出片」 | 上传帧图后：`--workflow workflows/fl2v_api.json --set "9.prompt=<提示词>"` |
| 「用参考图出片（Ref2VA）」 | 上传参考图后：`--workflow workflows/ref2v_api.json --set "7.prompt=<提示词>"` |
| 「批量出这 5 条」 | 循环调用 h3_generate.py（ComfyUI 队列本身可并发排队，脚本顺序提交即可）|
| 「今天用了多少 CU」 | `bash scripts/h3_usage.sh` |
| 「收工」 | `bash scripts/h3_down.sh`（释放 VM，权重在 GCS+Drive、产出在 Drive 不动）|
| 「把刚才的片子归档」 | 产出已由 VM 守护循环自动回写 Drive；本地补拉：`colab download -s h3 /content/ComfyUI/output/<file> ./outputs/` |

## 出片脚本参数（h3_generate.py）

```bash
python3 scripts/h3_generate.py \
  --workflow workflows/t2v_api.json \   # API 格式工作流 JSON（t2v/fl2v/ref2v 已内置）
  --set "5.text=提示词" \                # 注入节点输入：节点ID.输入键=值，可多次
  --set "12.seed=12345" \               # 固定种子复现
  --name demo \                          # 输出文件名前缀
  --timeout 1800                         # 单条最长等待（默认 30 分钟）
```

产出默认存 `outputs/<name>_<时间戳>.mp4`（本地），同时 VM 守护循环已把
`/content/ComfyUI/output` 每 60s 回写到 `Drive:/MyDrive/H3/outputs/`。

## 常见问题排查（agent 排查顺序）

1. **隧道不通**：`curl -s localhost:8188/system_stats` 无响应 → 重跑 h3_up.sh 的隧道段，或检查 `colab status -s h3`
2. **VM 被回收**（闲置 ~180 分钟）：重新 `bash scripts/h3_up.sh`，权重会从 Drive 快速恢复
3. **生成报缺模型**：VM 端 `colab exec -s h3 -f` 跑 bootstrap 的 `weights` 阶段补同步
4. **速度突然变慢**：`colab status` 确认还在 A100；若被换到 L4/T4，stop 后重新 up
5. **CU 不足**：`colab usage`；或退到 L4 档跑 WanGP 路线（见 research.md §4）

## 权限与安全

- hf_token.txt 与 OAuth 凭据（~/.colab-cli-oauth-config.json、~/.config/colab-cli/）**不要**提交到任何仓库
- ComfyUI 只绑 127.0.0.1，经 SSH 隧道访问，无公网暴露（cloudflared 仅为备用方案）
- 模型内置内容审核；遵守 Community License（美/欧/英/韩需申请表）
