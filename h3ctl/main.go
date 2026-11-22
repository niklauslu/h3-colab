// h3ctl — H3 管线本地控制端(v0.4 提前开工, 用户拍板"脚本体感不好, 用 Go 做")
//
// 定位(plan-nocomfy.md 本地控制后端节): 本地侧是胶水代码 —— subprocess 编排
// colab-cli/ssh + HTTP 客户端/反代 + 内嵌面板页, 纯 stdlib 零第三方依赖。
// agent 主契约不经过 h3ctl(直连隧道+HTTP); h3ctl 服务两方:
//   1. 命令行人机界面(替代 scripts/h3_up.sh 等 shell 脚本)
//   2. `h3ctl serve` 本地网页控制台(薄视图: 一键开站/生成/状态)
//
// 站点矩阵(机型×档案)是单一事实源: stations.go, 与 VM 端 bootstrap MACHINE_RULES
// 互为校验(本地挡一道, VM gate 挡第二道, 两边都拦错配)。
package main

import (
	"fmt"
	"os"
)

const version = "0.1.2"

const usage = `h3ctl — H3 生产管线控制端 v` + version + `

  h3ctl stations                       站点矩阵(机型×档案)一览
  ── 三层架构(v0.3.1): VM 独立 / 方案按需加载 / 生成独立 ──
  h3ctl vm <会话> --machine A100       层1 裸 VM(建站+环境+服务, 不装权重)
  h3ctl load <档案> [会话]             层2 按需挂载(差异增量下载→重扫→换模板表)
  h3ctl loaded [会话]                  层2 盘上方案清单
  h3ctl up <档案> [会话]               糖 = vm + load 一键流
                                       例: h3ctl up h3omni h3ref
                                       ⚠ flag 必须放在位置参数之前(flag 包遇位置参数即停解析)
                                       选项: --machine A100 覆盖矩阵默认机型 | --high-mem
                                            --force 跳过本地机型预检(VM gate 仍会拦)
                                            --dry-run 只出计划不建资源
  h3ctl status [会话]                  站内状态(setup 进度/外壳 phase/队列)
  h3ctl tunnel [会话]                  前台开 SSH 隧道(Ctrl-C 断开; up 会自动后台开)
  h3ctl gen --mode t2v --prompt "…"    生成(经隧道): 同 h3_generate.py 全部参数
  h3ctl switch <档案> [会话]           同 VM 热切换权重档案(免重建, 共享 TE/VAE 不重拉)
  h3ctl down [会话]                    收工: 停隧道 + 释放 runtime
  h3ctl logs [会话] -n 30              VM 侧日志直出(setup|comfy|api|err|daemon|all)
                                       --follow 持续跟踪; h3ctl -v <命令> 开 DEBUG
  ※ 控制台已由 ComfyUI 原生界面透传替代: 隧道自带 http://127.0.0.1:8189 (v0.1.1)
  h3ctl version                        版本
`

func main() {
	initVerbose() // 全局 -v/--verbose: 开 DEBUG 日志(并从 args 剥离)
	if len(os.Args) < 2 {
		fmt.Fprint(os.Stderr, usage)
		os.Exit(1)
	}
	rest := os.Args[2:]
	var err error
	switch os.Args[1] {
	case "stations":
		err = cmdStations(rest)
	case "up":
		err = cmdUp(rest)
	case "status":
		err = cmdStatus(rest)
	case "tunnel":
		err = cmdTunnel(rest)
	case "gen":
		err = cmdGen(rest)
	case "down":
		err = cmdDown(rest)
	case "switch":
		err = cmdSwitch(rest)
	case "vm":
		err = cmdVm(rest)
	case "load":
		err = cmdLoad(rest)
	case "loaded":
		err = cmdLoaded(rest)
	case "logs":
		err = cmdLogs(rest)
	case "version", "-v", "--version":
		fmt.Println("h3ctl", version)
	case "help", "-h", "--help":
		fmt.Print(usage)
	default:
		fmt.Fprintf(os.Stderr, "未知命令: %s\n\n%s", os.Args[1], usage)
		os.Exit(1)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "✗", err)
		os.Exit(1)
	}
}
