package main

// up / status / down —— 开站全流程(替代 scripts/h3_up.sh):
//   预检 → 建 VM(colab new) → 传引导 → 提交 setup(fire-and-poll) → 轮询就绪 → 自动开隧道
// 启动耗时逐段打印(四条硬性要求之一: 启动作为一个操作+记录启动时间)。

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const projHint = "项目根目录(H3)由 h3ctl 二进制位置推断: 上级目录"

func projRoot() string {
	ex, err := os.Executable()
	if err != nil {
		return "."
	}
	// 二进制可在 h3ctl/ 内也可在仓库根; 向上找含 colab/bootstrap_h3.py 的目录
	for d := filepath.Dir(ex); d != "/" && d != "."; d = filepath.Dir(d) {
		if _, err := os.Stat(filepath.Join(d, "colab", "bootstrap_h3.py")); err == nil {
			return d
		}
	}
	// 开发态: go run 从源码目录起, 用工作目录向上找
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
	if err := preflightMachine(st, m, *force); err != nil {
		return err
	}
	if err := hasBinary("colab"); err != nil {
		return err
	}
	if err := hasBinary("ssh-keygen"); err != nil {
		return err
	}
	t0 := time.Now()
	fmt.Printf("▶ 站点 %s · 档案 %s · 机型 %s (%s)\n", session, profile, m, st.Use)

	// 1/5 建 VM(幂等: 已存在直接复用)
	exists, err := colabSessionExists(session)
	if err != nil {
		return err
	}
	if *dryRun {
		fmt.Printf("✅ dry-run 通过: 会话=%s 档案=%s 机型=%s 已存在=%v 高内存=%v — 未创建任何资源\n",
			session, profile, m, exists, *highMem)
		return nil
	}
	if exists {
		fmt.Println("▶ 1/5 VM 已存在, 复用")
	} else {
		fmt.Printf("▶ 1/5 建 VM(%s)…\n", m)
		newArgs := []string{"new", "-s", session, "--gpu", m}
		if *highMem && m == "A100" {
			newArgs = append(newArgs, "--high-mem")
		}
		if out, err := colabOut(newArgs...); err != nil {
			return fmt.Errorf("建 VM 失败: %s | %s", err, strings.TrimSpace(out))
		}
	}
	fmt.Printf("   +%s\n", time.Since(t0).Round(time.Second))

	// 2/5 传引导
	fmt.Println("▶ 2/5 上传引导文件…")
	if err := uploadBootstrap(session, projRoot(), profile); err != nil {
		return err
	}
	fmt.Printf("   +%s\n", time.Since(t0).Round(time.Second))

	// 3/5 提交 setup(fire-and-poll: VM 后台线程干活, 重活绝不占 exec)
	fmt.Println("▶ 3/5 提交 setup(装环境+拉权重+起服务, VM 后台)…")
	if err := submitSetup(session); err != nil {
		return err
	}

	// 4/5 轮询就绪(上限 40min; 冷启动 HF 直下 ~6-10min)
	fmt.Println("▶ 4/5 轮询就绪(每 30s; 判据 READY-CHECK: PASS)…")
	lastLine := ""
	ok, failed, _ := pollReady(session, 40*time.Minute, func(min int, out string) {
		line := summarizeStatus(out)
		if line != "" && line != lastLine {
			lastLine = line
			fmt.Printf("   [%02d] %s\n", min, line)
		}
	})
	if failed {
		return fmt.Errorf("setup 失败 — colab exec -s %s 跑 status 看 setup.log 定位", session)
	}
	if !ok {
		return fmt.Errorf("等待超时 — colab exec -s %s 跑 status 继续观察", session)
	}
	fmt.Printf("   就绪! 总耗时 %s\n", time.Since(t0).Round(time.Second))

	// 5/5 自动开隧道(后台, pidfile 管理)
	comfyPort := 8189
	t, err := startTunnel(session, 8188, comfyPort)
	if err != nil {
		return fmt.Errorf("隧道启动失败(%s); 可手动: h3ctl tunnel %s", err, session)
	}
	fmt.Printf("▶ 5/5 隧道 UP pid=%d → http://127.0.0.1:%d\n", t.PID, t.Port)

	fmt.Printf(`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 站点就绪 · 档案=%s 机型=%s · 冷启动 %s
  生成:  h3ctl gen --mode t2v --prompt "提示词" --seed 42 --seconds 5
  状态:  h3ctl status %s
  面板:  h3ctl serve   → http://127.0.0.1:8090
  收工:  h3ctl down %s
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`, profile, m, time.Since(t0).Round(time.Second), session, session)
	return nil
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
		fmt.Println("▶ 隧道已停")
	}
	out, err := colabOut("stop", "-s", session)
	if err != nil && !strings.Contains(strings.ToLower(out), "not found") {
		return fmt.Errorf("释放 runtime: %s | %s", err, strings.TrimSpace(out))
	}
	fmt.Printf("✅ 已释放 %s(VM 盘权重随会话销毁; 产出已即时下载到本地 outputs/)\n", session)
	return nil
}
