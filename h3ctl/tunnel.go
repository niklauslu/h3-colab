package main

// SSH 隧道 —— 坑⑨: 认证靠 CLI 桥接时经 X-Colab-Ssh-Pubkey header 注册公钥,
// ssh 侧必须用配套私钥 -i + root 用户; 坑㉖: 每 runtime 仅 1 条 SSH 会话(第二条 429),
// 多端口并进一条连接的多 -L。

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

func h3ctlDir() string {
	home, _ := os.UserHomeDir()
	d := filepath.Join(home, ".h3ctl")
	os.MkdirAll(d, 0o755)
	return d
}

func ensureSSHKey() (string, string, error) {
	home, _ := os.UserHomeDir()
	priv := filepath.Join(home, ".ssh", "id_ed25519")
	pub := priv + ".pub"
	if _, err := os.Stat(priv); os.IsNotExist(err) {
		if err := exec.Command("ssh-keygen", "-t", "ed25519", "-N", "", "-f", priv,
			"-C", "colab-ssh", "-q").Run(); err != nil {
			return "", "", fmt.Errorf("生成密钥失败: %s", err)
		}
	}
	return priv, pub, nil
}

type TunnelInfo struct {
	Session string `json:"session"`
	Port    int    `json:"port"`
	PID     int    `json:"pid"`
	Started string `json:"started_at"`
}

func tunnelPidPath(session string) string { return filepath.Join(h3ctlDir(), session+".tunnel.json") }

func readTunnel(session string) *TunnelInfo {
	b, err := os.ReadFile(tunnelPidPath(session))
	if err != nil {
		return nil
	}
	var t TunnelInfo
	if json.Unmarshal(b, &t) != nil || t.PID == 0 {
		return nil
	}
	// 进程活着才算有隧道(signal 0 探活)
	if err := syscall.Kill(t.PID, 0); err != nil {
		os.Remove(tunnelPidPath(session))
		return nil
	}
	return &t
}

// startTunnel: 后台隧道(进程挂到本 CLI 脱钩? 不脱钩 —— pidfile 管理, down 时统一杀)
func startTunnel(session string, port, comfyPort int) (*TunnelInfo, error) {
	if t := readTunnel(session); t != nil {
		return t, nil // 幂等: 已有活隧道直接复用
	}
	priv, pub, err := ensureSSHKey()
	if err != nil {
		return nil, err
	}
	proxy := fmt.Sprintf("colab ssh --proxy-mode -s %s -i %s", session, pub)
	lArgs := fmt.Sprintf("-L 127.0.0.1:%d:127.0.0.1:%d", port, port)
	if comfyPort > 0 {
		lArgs += fmt.Sprintf(" -L 127.0.0.1:%d:127.0.0.1:%d", comfyPort, comfyPort)
	}
	logFile, err := os.Create(filepath.Join(h3ctlDir(), session+".tunnel.log"))
	if err != nil {
		return nil, err
	}
	cmd := exec.Command("ssh",
		"-o", "ProxyCommand="+proxy,
		"-i", priv,
		"-o", "StrictHostKeyChecking=no",
		"-o", "UserKnownHostsFile=/dev/null",
		"-o", "ServerAliveInterval=30",
		"-o", "ServerAliveCountMax=4",
		"-N", lArgs, fmt.Sprintf("root@%s", session))
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("ssh 启动失败: %s", err)
	}
	go cmd.Wait() // 防僵尸; down 时用 kill 组处理
	t := &TunnelInfo{Session: session, Port: port, PID: cmd.Process.Pid,
		Started: time.Now().Format(time.RFC3339)}
	b, _ := json.Marshal(t)
	os.WriteFile(tunnelPidPath(session), b, 0o644)
	return t, nil
}

func stopTunnel(session string) bool {
	t := readTunnel(session)
	if t == nil {
		return false
	}
	syscall.Kill(t.PID, syscall.SIGTERM)
	os.Remove(tunnelPidPath(session))
	return true
}

// cmdTunnel: 前台隧道(Ctrl-C 断开), 对齐 scripts/h3_tunnel.sh 行为
func cmdTunnel(args []string) error {
	session := "h3"
	for _, a := range args {
		if !strings.HasPrefix(a, "-") {
			session = a
		}
	}
	priv, pub, err := ensureSSHKey()
	if err != nil {
		return err
	}
	proxy := fmt.Sprintf("colab ssh --proxy-mode -s %s -i %s", session, pub)
	cmd := exec.Command("ssh",
		"-o", "ProxyCommand="+proxy,
		"-i", priv,
		"-o", "StrictHostKeyChecking=no",
		"-o", "UserKnownHostsFile=/dev/null",
		"-o", "ServerAliveInterval=30",
		"-o", "ServerAliveCountMax=4",
		"-N", "-L", "127.0.0.1:8188:127.0.0.1:8188",
		"-L", "127.0.0.1:8189:127.0.0.1:8189",
		fmt.Sprintf("root@%s", session))
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	fmt.Printf("▶ 前台隧道 → http://127.0.0.1:8188 (Ctrl-C 断开)\n")
	return cmd.Run()
}
