package main

// switch — 同 VM 热切换权重档案（D1' 验证过的模式, 免重建 VM 免重拉共享 TE/VAE）:
//   改 config.json(weights_profile) → 重传引导 → 重跑 setup(gate+增量下载) → 等就绪 → 外壳自动换模板表
// 注意: 外壳进程读取 config 是启动时一次性, 守护循环检测到 shell 挂掉重启后即生效。

import (
	"flag"
	"fmt"
	"time"
)

func cmdSwitch(args []string) error {
	fs := flag.NewFlagSet("switch", flag.ExitOnError)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := guardPositional(fs, 2); err != nil {
		return err
	}
	profile, err := firstArg(fs, "目标档案名 (h3ctl stations 可查)")
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
	if err := preflightMachine(st, st.Machine, false); err != nil {
		return err
	}
	if err := hasBinary("colab"); err != nil {
		return err
	}
	startf("切换", "会话=%s → 档案=%s(%s) 机型=%s [共享 TE/VAE 不重拉]", session, profile, st.Kind, st.Machine)
	t0 := time.Now()

	// 1) 重传引导(含新 config.json 的 weights_profile)
	if err := uploadBootstrap(session, projRoot(), profile); err != nil {
		return err
	}
	okf("切换", "引导已更新(weights_profile=%s)", profile)

	// 2) 重跑 setup(增量: 已有档案秒过, 新档案补拉)
	startf("切换", "重跑 setup(增量下载新档案权重)…")
	if err := submitSetup(session); err != nil {
		return err
	}
	lf := newVMLogFollower()
	lastLine := ""
	ok, failed, _ := pollReady(session, 40*time.Minute, func(min int, out string) {
		lf.poll(session)
		waitf("切换", "第 %d 次轮询(距提交 %s)…", min, time.Since(t0).Round(time.Second))
		line := summarizeStatus(out)
		if line != "" && line != lastLine {
			lastLine = line
			infof("切换", "[%02d分] %s", min, line)
		}
	})
	if failed {
		return fmt.Errorf("切换失败 — h3ctl logs %s --file setup 定位", session)
	}
	if !ok {
		return fmt.Errorf("切换等待超时 — h3ctl status %s 续看", session)
	}
	okf("切换", "完成(%s): 档案=%s 生效, 外壳模板表已随重启切换",
		time.Since(t0).Round(time.Second), profile)
	return nil
}
