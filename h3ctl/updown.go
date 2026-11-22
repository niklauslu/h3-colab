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
	// 多会话并行: 第二台 VM 本地隧道端口错开(如 --port 8288 --comfy-port 8289)
	port := fs.Int("port", 8188, "本地隧道端口(契约外壳)")
	comfyPort := fs.Int("comfy-port", 8189, "本地隧道端口(ComfyUI)")
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
	startf("预检", "站点=%s 档案=%s(%s) 机型=%s [糖: vm+load]", session, profile, st.Kind, m)
	if err := preflightMachine(st, m, *force); err != nil {
		return err
	}
	if *dryRun {
		okf("预检", "dry-run 计划: ①vm(裸, %s) → ②load %s(增量) → 就绪 — 未创建资源", m, profile)
		return nil
	}
	// 层1: 裸 VM → 层2: 按需挂载(全部日志/计时由两层自打)
	if err := vmUp(session, m, *highMem, false, *port, *comfyPort); err != nil {
		return err
	}
	return loadProfile(session, profile)
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
