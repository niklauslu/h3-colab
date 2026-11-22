package main

// colab-cli 包装 —— 全部经 subprocess(计划文档定案: 本地侧胶水代码, 语言无关,
// 契约防漂移靠 API 而非同语言)。坑⑤: colab status 对不存在会话退出码为 0,
// 必须按输出 "not found" 判断。

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func colabOut(args ...string) (string, error) {
	cmd := exec.Command("colab", args...)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	err := cmd.Run()
	return buf.String(), err
}

// colabOutHB: 长时 colab 子命令(建 VM 等)带心跳 — colab-cli 内部重试可达数分钟,
// 无输出窗口像挂死(2026-11-22 用户反馈)。每 15s 打一行 ⏳。
func colabOutHB(mod string, args ...string) (string, error) {
	cmd := exec.Command("colab", args...)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	if err := cmd.Start(); err != nil {
		return "", err
	}
	done := make(chan error, 1)
	start := time.Now()
	go func() { done <- cmd.Wait() }()
	tick := time.NewTicker(15 * time.Second)
	defer tick.Stop()
	for {
		select {
		case err := <-done:
			return buf.String(), err
		case <-tick.C:
			waitf(mod, "colab 执行中… 已等待 %s(内部在自动重试)", time.Since(start).Round(time.Second))
		}
	}
}

// colabSessionExists: 坑⑤ —— 退出码不可信, 认输出
func colabSessionExists(session string) (bool, error) {
	out, err := colabOut("status", "-s", session)
	if err != nil {
		return false, fmt.Errorf("colab status: %s | %s", err, strings.TrimSpace(out))
	}
	return !strings.Contains(strings.ToLower(out), "not found"), nil
}

// colabExec: 往 VM 内核提交一段 Python(立即返回, 重活在 VM 线程跑 —— fire-and-poll)
// 默认 90s 超时 —— 无超时的 exec 曾挂死整个轮询循环(2026-11-22 h3tao 裸 VM 实测)
func colabExec(session, py string) (string, error) {
	return colabExecTimeout(session, py, 90*time.Second)
}

// colabExecTimeout: exec 传输层统一自动重试。
// 坑(2026-11-22 L4 桥接实测): 一次抖动(Connection was lost / 静默超时)就整条命令退出的
// 粒度太粗, 上层被迫人工重跑 —— 发往内核的 exec 全部幂等(status 查询/线程提交/marker
// 判断/杀进程), "重跑=恢复"直接内建: 3 连试, 10s 退避。
func colabExecTimeout(session, py string, timeout time.Duration) (string, error) {
	var out string
	var err error
	for attempt := 1; attempt <= 3; attempt++ {
		out, err = colabExecOnce(session, py, timeout)
		if err == nil {
			return out, nil
		}
		if attempt < 3 {
			time.Sleep(10 * time.Second)
		}
	}
	return out, err
}

func colabExecOnce(session, py string, timeout time.Duration) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	// CLI 自身默认 30s 静默即杀(坑①变体 2026-11-22 qwen21t load 实测: 20GB 文件
	// 下载启动窗口超 30s → "Use --timeout to allow longer silences") —
	// 本地 ctx 与 CLI --timeout 必须同步给足, 两者取一致的秒数
	args := []string{"exec", "-s", session,
		"--timeout", fmt.Sprintf("%.0f", timeout.Seconds())}
	cmd := exec.CommandContext(ctx, "colab", args...)
	cmd.Stdin = strings.NewReader(py)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return buf.String(), fmt.Errorf("exec 超时(%s) — 会话可能忙或桥接异常", timeout)
	}
	return buf.String(), err
}

func colabUpload(session, local, remote string) error {
	out, err := colabOut("upload", "-s", session, local, remote)
	if err != nil {
		return fmt.Errorf("upload %s → %s: %s | %s", local, remote, err, strings.TrimSpace(out))
	}
	return nil
}

// uploadBootstrap: 上传引导全家桶(对齐 scripts/h3_up.sh 第 2 步)
func uploadBootstrap(session, proj string, profile string) error {
	if _, err := colabExec(session, "import os; os.makedirs('/content/h3/workflows',exist_ok=True); print('ready')"); err != nil {
		return fmt.Errorf("建目录失败: %s", err)
	}
	files := [][2]string{
		{proj + "/colab/bootstrap_h3.py", "/content/h3/bootstrap_h3.py"},
		{proj + "/colab/server_h3.py", "/content/h3/server_h3.py"},
		{proj + "/colab/h3api.py", "/content/h3/h3api.py"},
	}
	for _, f := range files {
		if _, err := os.Stat(f[0]); err == nil {
			if err := colabUpload(session, f[0], f[1]); err != nil {
				return err
			}
		}
	}
	// 工作流模板(现有 t2v/fl2v/ref2v + PDD 变体; 缺失忽略)
	matches, _ := filepath.Glob(proj + "/workflows/*_api.json")
	for _, wf := range matches {
		remote := "/content/h3/workflows/" + filepath.Base(wf)
		if err := colabUpload(session, wf, remote); err != nil {
			return err
		}
	}
	// config.json(站点选择的落点)
	cfg := fmt.Sprintf(`{"expose": "ssh", "port": 8188, "weights_profile": %q}`, profile)
	tmp, _ := os.CreateTemp("", "h3cfg*.json")
	tmp.WriteString(cfg)
	tmp.Close()
	defer os.Remove(tmp.Name())
	if err := colabUpload(session, tmp.Name(), "/content/h3/config.json"); err != nil {
		return err
	}
	// HF token(可选)
	if _, err := os.Stat(proj + "/colab/hf_token.txt"); err == nil {
		if err := colabUpload(session, proj+"/colab/hf_token.txt", "/content/h3/hf_token.txt"); err != nil {
			return err
		}
	}
	return nil
}

const setupPy = "import runpy; _ = runpy.run_path('/content/h3/bootstrap_h3.py', run_name='setup')"
const statusPy = "import runpy; _ = runpy.run_path('/content/h3/bootstrap_h3.py', run_name='status')"

func submitSetup(session string) error {
	out, err := colabExec(session, setupPy)
	if err != nil {
		return fmt.Errorf("提交 setup 失败: %s | %s", err, strings.TrimSpace(out))
	}
	return nil
}

// pollReady: 轮询至就绪(对齐 h3_up.sh 判据: READY-CHECK: PASS / SETUP FAILED)
// 返回 (就绪?, 失败?, 最后一次输出)
func pollReady(session string, timeout time.Duration, onTick func(min int, out string)) (bool, bool, string) {
	deadline := time.Now().Add(timeout)
	for i := 1; time.Now().Before(deadline); i++ {
		time.Sleep(30 * time.Second)
		out, err := colabExec(session, statusPy)
		if err != nil {
			out = fmt.Sprintf("(exec 异常: %v)", err)
		}
		if onTick != nil {
			onTick(i/2, out)
		}
		if strings.Contains(out, "READY-CHECK: PASS") {
			return true, false, out
		}
		if strings.Contains(out, "SETUP FAILED") {
			return false, true, out
		}
	}
	return false, false, "(轮询超时)"
}
