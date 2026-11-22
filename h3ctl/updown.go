package main

// up / status / down —— 开站全流程(替代 scripts/h3_up.sh), 分步骤计时:
//   预检 → 建 VM → 传引导 → 提交 setup → 轮询就绪(VM 日志增量直出) → 自动开隧道
// 每阶段耗时实时打印, 收官打印「启动分解」小结(数据回填 docs/test-log.md)。

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func projRoot() string {
	ex, err := os.Executable()
	if err != nil {
		return "."
	}
	for d := filepath.Dir(ex); d != "/" && d != "."; d = filepath.Dir(d) {
		if _, err := os.Stat(filepath.Join(d, "colab", "bootstrap_h3.py")); err == nil {
			return d
		}
	}
	wd, _ := os.Getwd()
	for d := wd; d != "/" && d != "."; d = filepath.Dir(d) {
		if _, err := os.Stat(filepath.Join(d, "colab", "bootstrap_h3.py")); err == nil {
			return d
		}
	}
	return wd
}

func cmdUp(args []string) error {
	fs := flag.NewFlagSet("up", flag.ExitOnError)
	machine := fs.String("machine", "", "覆盖站点矩阵默认机型")
	highMem := fs.Bool("high-mem", false, "A100 High-RAM(8.88 CU/h, 费 39%)")
	force := fs.Bool("force", false, "放行本地机型错配(实验; VM gate 仍拦)")
	dryRun := fs.Bool("dry-run", false, "只做预检与计划, 不建 VM 不花钱")
	waitMode := fs.Bool("wait", false, "抢位模式: 分配 503 时持续重试(20 次×60s, 高峰常需)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := guardPositional(fs, 2); err != nil {
		return err
	}
	profile, err := firstArg(fs, "档案名 (h3ctl stations 可查)")
	if err != nil {
		return err
	}
	st, ok := findStation(profile)
	if !ok {
		return fmt.Errorf("未知档案 %s (h3ctl stations 可查)", profile)
	}
	session := "h3"
	if fs.NArg() > 1 {
		session = fs.Args()[1]
	}
	if err := validSession(session); err != nil {
		return err
	}
	m := st.Machine
	if *machine != "" {
		m = *machine
	}
	startf("预检", "站点=%s 档案=%s(%s) 机型=%s", session, profile, st.Kind, m)
	if err := preflightMachine(st, m, *force); err != nil {
		return err
	}
	if err := hasBinary("colab"); err != nil {
		return err
	}
	if err := hasBinary("ssh-keygen"); err != nil {
		return err
	}
	okf("预检", "依赖齐备(colab / ssh-keygen)")

	t0 := time.Now()
	stages := map[string]time.Duration{}
	mark := func(name string, from time.Time) {
		stages[name] = time.Since(from)
		okf("计时", "%s: %s", name, time.Since(from).Round(time.Second))
	}

	// ── 1/5 建 VM(幂等: 已存在直接复用) ──
	tVM := time.Now()
	exists, err := colabSessionExists(session)
	if err != nil {
		return err
	}
	if *dryRun {
		okf("预检", "dry-run 通过: 会话=%s 档案=%s 机型=%s 已存在=%v 高内存=%v — 未创建任何资源",
			session, profile, m, exists, *highMem)
		return nil
	}
	if exists {
		infof("VM", "会话已存在, 复用")
	} else {
		startf("VM", "创建 %s 中…(分配通常 1-2 分钟)", m)
		newArgs := []string{"new", "-s", session, "--gpu", m}
		if *highMem && m == "A100" {
			newArgs = append(newArgs, "--high-mem")
		}
		maxAttempts, gap := 3, 20*time.Second
		if *waitMode {
			maxAttempts, gap = 20, 60*time.Second
			infof("VM", "抢位模式: 最多 %d 次×60s, 分配成功即止", maxAttempts)
		}
		var out string
		var err error
		for attempt := 1; attempt <= maxAttempts; attempt++ { // 503/瞬断重试
			out, err = colabOutHB("VM", newArgs...)
			if err == nil {
				break
			}
			warnf("VM", "第 %d/%d 次创建失败: %s", attempt, maxAttempts, lastLine(strings.TrimSpace(out)))
			if attempt < maxAttempts {
				waitf("VM", "%s 后重试…", gap)
				time.Sleep(gap)
			}
		}
		if err != nil {
			return fmt.Errorf("建 VM 失败(重试 %d 次): %s | %s", maxAttempts, err, lastLine(strings.TrimSpace(out)))
		}
		waitf("VM", "等待 runtime 可用…")
		for i := 1; i <= 24; i++ { // 最多 4 分钟
			time.Sleep(10 * time.Second)
			e, err := colabSessionExists(session)
			if err == nil && e {
				break
			}
			debugf("VM", "第 %d 次探测…", i)
		}
		if e, _ := colabSessionExists(session); !e {
			return fmt.Errorf("VM 创建超时(4min)")
		}
	}
	mark("①建VM", tVM)
	gpuLine := ""
	if out, err := colabOut("status", "-s", session); err == nil {
		for _, ln := range strings.Split(out, "\n") {
			if strings.Contains(ln, "gpu-") || strings.Contains(ln, "Hardware") {
				gpuLine = strings.TrimSpace(ln)
				break
			}
		}
	}
	if gpuLine != "" {
		infof("VM", "%s", gpuLine)
	}

	// ── 2/5 传引导 ──
	tUp := time.Now()
	startf("上传", "引导文件 → /content/h3 …")
	if err := uploadBootstrap(session, projRoot(), profile); err != nil {
		return err
	}
	mark("②上传", tUp)

	// ── 3/5 提交 setup(fire-and-poll) ──
	tSetup := time.Now()
	startf("引导", "提交 setup 线程(装环境+拉权重+起服务, VM 后台)…")
	if err := submitSetup(session); err != nil {
		return err
	}
	mark("③提交", tSetup)

	// ── 4/5 轮询就绪(VM 日志增量直出) ──
	startf("服务", "轮询就绪(每 30s; 判据 READY-CHECK: PASS; --verbose 看全量)…")
	lf := newVMLogFollower()
	lastLine := ""
	stageSeen := map[string]time.Time{}
	ok, failed, _ := pollReady(session, 40*time.Minute, func(min int, out string) {
		lf.poll(session) // VM 侧 setup/err/daemon 日志增量
		waitf("服务", "第 %d 次轮询(距提交 %s)… 无增量行=VM 静默无事件", min,
			time.Since(tSetup).Round(time.Second))
		line := summarizeStatus(out)
		if line == "" || line == lastLine {
			return
		}
		lastLine = line
		infof("服务", "[%02d分] %s", min, line)
		for _, key := range []string{"[install] OK", "[weights] OK", "shell: phase=ready"} {
			if strings.Contains(out, key) && stageSeen[key].IsZero() {
				stageSeen[key] = time.Now()
				okf("服务", "里程碑: %s (距提交 %s)", key,
					time.Since(tSetup).Round(time.Second))
			}
		}
	})
	if failed {
		errf("服务", "setup 失败 — 详查: h3ctl logs %s --file setup", session)
		return fmt.Errorf("setup 失败")
	}
	if !ok {
		return fmt.Errorf("等待超时 — h3ctl status %s 继续观察", session)
	}

	// ── 5/5 自动开隧道 ──
	tTun := time.Now()
	comfyPort := 8189
	t, err := startTunnel(session, 8188, comfyPort)
	if err != nil {
		return fmt.Errorf("隧道启动失败(%s); 可手动: h3ctl tunnel %s", err, session)
	}
	waitf("隧道", "ssh 转发建立中(pid=%d)…", t.PID)
	tunnelUp := false
	for i := 0; i < 10; i++ { // 探活: 隧道端口可 HTTP
		time.Sleep(2 * time.Second)
		st, _, err := httpReq("GET", "/health", 8188, nil, "", 5*time.Second)
		if err == nil && st < 500 {
			tunnelUp = true
			break
		}
		debugf("隧道", "第 %d 次探活未通…", i+1)
	}
	if !tunnelUp {
		tail, _ := os.ReadFile(filepath.Join(h3ctlDir(), session+".tunnel.log"))
		return fmt.Errorf("隧道 20s 探活失败 — %s\n  手动排查: h3ctl tunnel %s", strings.TrimSpace(string(tail)), session)
	}
	mark("⑤隧道", tTun)
	mark("④服务就绪", tSetup) // 从提交到就绪(含权重下载)

	total := time.Since(t0)
	hr("启动分解(回填 docs/test-log.md)")
	for _, k := range []string{"①建VM", "②上传", "③提交", "④服务就绪", "⑤隧道"} {
		if d, ok := stages[k]; ok {
			fmt.Printf("  %-14s %s\n", k, d.Round(time.Second))
		}
	}
	fmt.Printf("  %-14s %s\n", "总启动", total.Round(time.Second))
	if gpuLine != "" {
		fmt.Printf("  %-14s %s\n", "机型", gpuLine)
	}
	okf("就绪", "站点 %s 可用 → http://127.0.0.1:8188 (总启动 %s)", session, total.Round(time.Second))
	fmt.Printf(`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 站点就绪 · 档案=%s 机型=%s
  生成:  h3ctl gen --mode ref2v --ref 图=image --prompt "…"
  日志:  h3ctl logs %s --follow        状态: h3ctl status %s
  收工:  h3ctl down %s
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`, profile, m, session, session, session)
	return nil
}

// lastLine: 长输出取最后非空行(报错时贴 traceback 太长)
func lastLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndex(s, "\n"); i >= 0 {
		s = s[i+1:]
	}
	if len(s) > 200 {
		s = s[:200] + "…"
	}
	return s
}

// summarizeStatus: 从 status 输出里抽一行最有信息量的进度
func summarizeStatus(out string) string {
	for _, key := range []string{"READY-CHECK", "SETUP FAILED", "shell: phase=", "weights]", "install]", "comfy:", "setup:"} {
		for _, ln := range strings.Split(out, "\n") {
			if strings.Contains(ln, key) {
				return strings.TrimSpace(ln)
			}
		}
	}
	return ""
}

func cmdStatus(args []string) error {
	fs := flag.NewFlagSet("status", flag.ExitOnError)
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
	out, err := colabExec(session, statusPy)
	if err != nil {
		return fmt.Errorf("exec 失败(会话在?): %s | %s", err, strings.TrimSpace(out))
	}
	fmt.Print(out)
	if t := readTunnel(session); t != nil {
		fmt.Printf("tunnel: UP pid=%d port=%d (since %s)\n", t.PID, t.Port, t.Started)
	} else {
		fmt.Println("tunnel: 无 (h3ctl tunnel " + session + " 开启)")
	}
	return nil
}

func cmdDown(args []string) error {
	fs := flag.NewFlagSet("down", flag.ExitOnError)
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
	if stopTunnel(session) {
		okf("隧道", "已停")
	}
	startf("VM", "释放 runtime %s …", session)
	out, err := colabOut("stop", "-s", session)
	if err != nil && !strings.Contains(strings.ToLower(out), "not found") {
		return fmt.Errorf("释放 runtime: %s | %s", err, strings.TrimSpace(out))
	}
	okf("VM", "已释放(VM 盘权重随会话销毁; 产出已即时下载到本地 outputs/)")
	return nil
}
