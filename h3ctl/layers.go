package main

// 三层架构(v0.3.1, 用户拍板"VM 独立 / 方案按需加载 / 生成独立"):
//
//	层1  h3ctl vm <会话> --machine A100   裸 VM: 建 VM+引导+ComfyUI+外壳, 权重 profile=none
//	层2  h3ctl load <档案> [会话]         按需挂载: 差异增量下载→symlink→ComfyUI 重扫→外壳换模板表
//	     h3ctl loaded [会话]              盘上方案清单(含缺失文件)
//	糖   h3ctl up <档案> [会话]           = vm + load(一键流入口, 保留)
//
// 同站可共存多档案(视频 55GB + 生图 19/17GB, 盘 236GB); 换方案=增量, 今天实测 1m39s。

import (
	"flag"
	"fmt"
	"strings"
	"time"
)

// vmUp: 层1 —— 裸 VM(不含权重)。返回 session 名。port/comfyPort=本地隧道端口(多会话并行错开)。
func vmUp(session, machine string, highMem, dryRun bool, port, comfyPort int) error {
	if err := hasBinary("colab"); err != nil {
		return err
	}
	if err := hasBinary("ssh-keygen"); err != nil {
		return err
	}
	t0 := time.Now()
	startf("VM", "裸 VM 模式(profile=none, 权重由 load 按需挂载)")
	exists, err := colabSessionExists(session)
	if err != nil {
		return err
	}
	if dryRun {
		okf("VM", "dry-run: 会话=%s 机型=%s 已存在=%v — 未创建资源", session, machine, exists)
		return nil
	}
	if exists {
		infof("VM", "会话已存在, 复用")
	} else {
		startf("VM", "创建 %s 中…", machine)
		newArgs := []string{"new", "-s", session, "--gpu", machine}
		if highMem && machine == "A100" {
			newArgs = append(newArgs, "--high-mem")
		}
		var out string
		var err error
		for attempt := 1; attempt <= 3; attempt++ {
			out, err = colabOutHB("VM", newArgs...)
			if err == nil {
				break
			}
			warnf("VM", "第 %d/3 次创建失败: %s", attempt, lastLine(strings.TrimSpace(out)))
			if attempt < 3 {
				waitf("VM", "20s 后重试…")
				time.Sleep(20 * time.Second)
			}
		}
		if err != nil {
			return fmt.Errorf("建 VM 失败: %s | %s", err, lastLine(strings.TrimSpace(out)))
		}
		waitf("VM", "等待 runtime 可用…")
		for i := 1; i <= 24; i++ {
			time.Sleep(10 * time.Second)
			if e, _ := colabSessionExists(session); e {
				break
			}
		}
		if e, _ := colabSessionExists(session); !e {
			return fmt.Errorf("VM 创建超时(4min)")
		}
	}
	okf("计时", "①建VM: %s", time.Since(t0).Round(time.Second))

	tU := time.Now()
	startf("上传", "引导文件(bootstrap/h3api/workflows, config profile=none)…")
	if err := uploadBootstrap(session, projRoot(), "none"); err != nil {
		return err
	}
	okf("计时", "②上传: %s", time.Since(tU).Round(time.Second))

	tS := time.Now()
	startf("引导", "提交 setup(装环境+起服务, 不拉权重)…")
	if err := submitSetup(session); err != nil {
		return err
	}
	ok, failed, _ := pollReady(session, 20*time.Minute, func(min int, s string) {
		waitf("服务", "[%02d分] 轮询中… %s", min, summarizeStatus(s)) // 心跳先于 exec: exec 挂死时仍有输出
		lf := newVMLogFollower()
		lf.poll(session)
	})
	if failed || !ok {
		return fmt.Errorf("裸 VM 启动未就绪 — h3ctl logs %s --file setup 定位", session)
	}
	okf("计时", "③环境+服务: %s", time.Since(tS).Round(time.Second))

	tT := time.Now()
	t, err := startTunnel(session, port, comfyPort)
	if err != nil {
		return fmt.Errorf("隧道失败: %s", err)
	}
	tunnelUp := false
	for i := 0; i < 10; i++ {
		time.Sleep(2 * time.Second)
		st, _, err := httpReq("GET", "/health", port, nil, "", 5*time.Second)
		if err == nil && st < 500 {
			tunnelUp = true
			break
		}
	}
	if !tunnelUp {
		return fmt.Errorf("隧道探活失败(pid=%d) — h3ctl tunnel %s 手动排查", t.PID, session)
	}
	okf("计时", "④隧道: %s (pid=%d)", time.Since(tT).Round(time.Second), t.PID)
	okf("就绪", "裸 VM %s 可用(总 %s) → 下一步: h3ctl load <档案> %s",
		session, time.Since(t0).Round(time.Second), session)
	return nil
}

func cmdVm(args []string) error {
	fs := flag.NewFlagSet("vm", flag.ExitOnError)
	machine := fs.String("machine", "A100", "机型")
	highMem := fs.Bool("high-mem", false, "A100 High-RAM")
	dryRun := fs.Bool("dry-run", false, "只出计划")
	// 多会话并行(2026-11-22 L4+T4 双机实证): 第二台 VM 的本地隧道端口须错开
	port := fs.Int("port", 8188, "本地隧道端口(契约外壳)")
	comfyPort := fs.Int("comfy-port", 8189, "本地隧道端口(ComfyUI)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := guardPositional(fs, 1); err != nil {
		return err
	}
	session := "h3"
	if fs.NArg() > 0 {
		session = fs.Args()[0]
	}
	if err := validSession(session); err != nil {
		return err
	}
	return vmUp(session, *machine, *highMem, *dryRun, *port, *comfyPort)
}

// loadProfile: 层2 —— 按需挂载档案(增量)。
func loadProfile(session, profile string) error {
	st, ok := findStation(profile)
	if !ok {
		return fmt.Errorf("未知档案 %s (h3ctl stations 可查)", profile)
	}
	startf("加载", "会话=%s → 档案=%s(%s) [差异增量: 盘上已有秒过]", session, profile, st.Kind)
	t0 := time.Now()
	if err := uploadBootstrap(session, projRoot(), profile); err != nil {
		return err
	}
	// weights 阶段单独跑(legacy 阶段名): 增量下载 + symlink, 不碰 daemon
	startf("加载", "增量下载缺失文件(HF snapshot 只拉缺的)…")
	waitf("加载", "weights 阶段执行中(增量下载, 进度经 VM 日志回流)…")
	out, err := colabExecTimeout(session, "import runpy; _ = runpy.run_path('/content/h3/bootstrap_h3.py', run_name='weights')", 15*time.Minute)
	if err != nil {
		return fmt.Errorf("weights 阶段失败: %s | %s", err, lastLine(out))
	}
	for _, ln := range strings.Split(out, "\n") {
		if strings.Contains(ln, "[weights]") {
			infof("VM·weights", "%s", strings.TrimSpace(ln))
		}
	}
	if strings.Contains(out, "SETUP FAILED") || strings.Contains(out, "Traceback") {
		return fmt.Errorf("weights 阶段报错 — h3ctl logs %s --file setup 定位", session)
	}

	// ComfyUI 重扫(新 symlink) + 外壳换模板表(config 启动时一次性读取)
	startf("加载", "重启 ComfyUI(重扫模型) + 外壳(换档案模板表)…")
	// 坑(2026-11-22 L4 桥接不稳实测): 杀进程 exec 可能静默丢失(config 已换但旧外壳
	// 仍持旧模板表) — 必须重试; 就绪判据交给 pollReady(新 h3api.log 时间戳即重启成功)
	restartPy := `
import subprocess
for pat in ("main.py --listen 127.0.0.1", "python3 /content/h3/h3api.py"):
    r = subprocess.run("pgrep -f '%s'" % pat, shell=True, capture_output=True, text=True)
    for p in r.stdout.split():
        subprocess.run("kill %s" % p, shell=True)
print("comfy+shell killed → daemon 拉起")`
	var rerr error
	for attempt := 1; attempt <= 3; attempt++ {
		if _, rerr = colabExec(session, restartPy); rerr == nil {
			break
		}
		warnf("加载", "重启指令第 %d/3 次失败: %s", attempt, rerr)
		time.Sleep(10 * time.Second)
	}
	if rerr != nil {
		return fmt.Errorf("重启进程失败(3次): %s", rerr)
	}
	ok, failed, _ := pollReady(session, 15*time.Minute, func(min int, s string) {
		waitf("加载", "服务重启中… [%d]", min)
	})
	if failed || !ok {
		return fmt.Errorf("加载后服务未就绪 — h3ctl status %s 续看", session)
	}
	okf("加载", "完成(%s): 档案=%s 生效", time.Since(t0).Round(time.Second), profile)
	return nil
}

func cmdLoad(args []string) error {
	fs := flag.NewFlagSet("load", flag.ExitOnError)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := guardPositional(fs, 2); err != nil {
		return err
	}
	profile, err := firstArg(fs, "档案名")
	if err != nil {
		return err
	}
	session := "h3"
	if fs.NArg() > 1 {
		session = fs.Args()[1]
	}
	if err := validSession(session); err != nil {
		return err
	}
	return loadProfile(session, profile)
}

// cmdLoaded: 盘上方案清单(档案 marker + 文件占用)。
func cmdLoaded(args []string) error {
	fs := flag.NewFlagSet("loaded", flag.ExitOnError)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := guardPositional(fs, 1); err != nil {
		return err
	}
	session := "h3"
	if fs.NArg() > 0 {
		session = fs.Args()[0]
	}
	if err := validSession(session); err != nil {
		return err
	}
	out, err := colabExec(session, `
import os, json
root = "/content/models"
res = {"dirs": {}, "markers": []}
for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
    p = os.path.join(root, d)
    if not os.path.isdir(p) or d.startswith("."):
        continue
    n, gb = 0, 0.0
    for dp, _, fns in os.walk(p):
        for f in fns:
            if f.endswith(".safetensors"):
                n += 1
                try: gb += os.path.getsize(os.path.join(dp, f)) / 1e9
                except OSError: pass
    res["dirs"][d] = {"files": n, "gb": round(gb, 1)}
res["markers"] = [m for m in os.listdir(root+"/fasth3") if m.startswith(".done_")] if os.path.isdir(root+"/fasth3") else []
print("LOADED_JSON:" + json.dumps(res))`)
	if err != nil {
		return fmt.Errorf("探查失败: %s", err)
	}
	for _, ln := range strings.Split(out, "\n") {
		if strings.HasPrefix(ln, "LOADED_JSON:") {
			infof("清单", "%s", strings.TrimPrefix(ln, "LOADED_JSON:"))
		}
	}
	fmt.Println("↑ dirs=权重目录占用; markers=已就绪档案(.done_<档案>)")
	return nil
}
