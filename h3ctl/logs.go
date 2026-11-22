package main

// logs — VM 侧日志直出（用户要求"最好 VM 的日志都可以打印出来"）
//
// h3ctl logs [会话] -n 30 [--follow] [--file setup|comfy|api|err|daemon|all]

import (
	"flag"
	"fmt"
	"strings"
	"time"
)

var vmLogs = map[string]string{
	"setup":  "/content/h3/setup.log",   // bootstrap 引导全程(装环境/拉权重/起服务)
	"comfy":  "/content/h3/comfyui.log", // ComfyUI 内核
	"api":    "/content/h3/h3api.log",   // 契约外壳
	"err":    "/content/h3/h3api.err.log",
	"daemon": "/content/h3/daemon.log", // 守护循环
}

func vmTail(session, path string, n int) (string, error) {
	py := fmt.Sprintf(
		"import os;p=%q;print(''.join(open(p,errors='replace').readlines()[-%d:]) if os.path.exists(p) else '(不存在)')",
		path, n)
	out, err := colabExec(session, py)
	if err != nil {
		return "", err
	}
	return strings.TrimRight(out, "\n"), nil
}

func cmdLogs(args []string) error {
	fs := flag.NewFlagSet("logs", flag.ExitOnError)
	n := fs.Int("n", 30, "每个文件末尾行数")
	follow := fs.Bool("follow", false, "持续跟踪(每 5s 增量)")
	file := fs.String("file", "all", "setup|comfy|api|err|daemon|all")
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
	names := []string{*file}
	if *file == "all" {
		names = []string{"setup", "comfy", "api", "err", "daemon"}
	}
	for _, nm := range names {
		if _, ok := vmLogs[nm]; !ok {
			return fmt.Errorf("未知日志 %s (可选: setup|comfy|api|err|daemon|all)", nm)
		}
	}

	if !*follow {
		for _, nm := range names {
			out, err := vmTail(session, vmLogs[nm], *n)
			if err != nil {
				warnf("日志", "%s 拉取失败: %v", nm, err)
				continue
			}
			fmt.Printf("── %s · %s ──\n%s\n", nm, vmLogs[nm], out)
		}
		return nil
	}

	// follow: 按文件增量(记住上次内容长度, 只打新增段)
	tw := newTailWatch(names)
	infof("日志", "跟踪 %s 会话 VM 日志(Ctrl-C 断开): %v", session, names)
	for {
		for _, nm := range names {
			out, err := vmTail(session, vmLogs[nm], *n)
			if err != nil {
				continue
			}
			if delta := tw.delta(nm, out); strings.TrimSpace(delta) != "" {
				fmt.Printf("── %s ──\n%s\n", nm, delta)
			}
		}
		time.Sleep(5 * time.Second)
	}
}

// tailWatch: 单次 tail -n 的内容做增量(diff 上次快照)
type tailWatch struct {
	last map[string]string
}

func newTailWatch(names []string) *tailWatch {
	return &tailWatch{last: map[string]string{}}
}

func (t *tailWatch) delta(name, cur string) string {
	prev := t.last[name]
	t.last[name] = cur
	if prev == "" {
		return cur // 首次: 全量
	}
	if len(cur) < len(prev) {
		return cur // 日志轮转/截断: 全量
	}
	// 找 prev 尾部在 cur 中的位置(取最长对齐后缀, 简化: 从 cur 头部对齐 prev 尾部 200 字符)
	tail := prev
	if len(tail) > 200 {
		tail = tail[len(tail)-200:]
	}
	if idx := strings.Index(cur, tail); idx >= 0 {
		return cur[idx+len(tail):]
	}
	return cur
}

// vmLogDelta: up 轮询中增量打 VM 引导日志(setup.log 新行), 静默期不出声
type vmLogFollower struct {
	w     *tailWatch
	names []string
}

func newVMLogFollower() *vmLogFollower {
	return &vmLogFollower{w: newTailWatch(nil), names: []string{"setup", "err", "daemon"}}
}

func (f *vmLogFollower) poll(session string) {
	for _, nm := range f.names {
		out, err := vmTail(session, vmLogs[nm], 14)
		if err != nil {
			return
		}
		if delta := f.w.delta(nm, out); strings.TrimSpace(delta) != "" {
			for _, ln := range strings.Split(strings.TrimRight(delta, "\n"), "\n") {
				if strings.TrimSpace(ln) == "" {
					continue
				}
				infof("VM·"+nm, "%s", strings.TrimSpace(ln))
			}
		}
	}
}
