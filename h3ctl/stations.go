package main

// 站点矩阵 —— 单一事实源(与 VM 端 bootstrap MACHINE_RULES 互为校验)。
// 机型默认值即矩阵判定: up 不需要用户传机型, 传了则校验一致(防呆)。

import (
	"flag"
	"fmt"
	"os/exec"
	"strings"
)

// Machine CU 费率(实测/订阅口径): A100 5.4 / L4 1.67 / T4 1.15 CU/h
type Station struct {
	Profile    string  // 对应 bootstrap MODEL_PROFILES / config.json weights_profile
	Machine    string  // 默认机型
	MinVRAM    int     // VM gate 硬校验显存下限(GB)
	Kind       string  // video | image
	Use        string  // 用途一句话
	Increment  string  // 冷启动增量下载(共享 TE/VAE 只拉一次)
	CostNote   string  // 单条成本参考
}

var Stations = []Station{
	{"h3video", "A100", 34, "video", "视频·FastH3 v2 8步基线", "~55GB(含共享TE/VAE)", "~0.26 CU/条"},
	{"h3pdd", "A100", 34, "video", "视频·t2v快产 PDD4步(t2v 47s⭐)", "DiT+LoRA ~25GB", "~0.11 CU/条"},
	{"h3omni", "A100", 34, "video", "视频·全能单DiT(t2v+ref2v)", "Omni DiT 22.2GB", "~0.26 CU/条"},
	{"h3pika", "A100", 34, "video", "视频·ref2v人物参考 Pika6步(112s⭐)", "DiT 22GB+LoRA ~2GB", "~0.2 CU/条(估)"},
	{"h3tao", "A100", 34, "video", "fl2v提速实验·TaoMate 3步LoRA(A/B: 强度0基线/1实验)", "DiT+LoRA ~24GB", "待测"},
	{"zimage", "T4", 14, "image", "生图·Z-Image Turbo(续出~61s/张)", "~19GB", "~0.025 CU/张"},
	{"qwen21t", "T4", 14, "image", "生图编辑·Qwen Turbo INT8", "~17.3GB", "~0.03 CU/张"},
}

func findStation(profile string) (Station, bool) {
	for _, s := range Stations {
		if s.Profile == profile {
			return s, true
		}
	}
	return Station{}, false
}

func cmdStations(_ []string) error {
	fmt.Printf("%-9s %-6s %-5s %-38s %-22s %s\n", "档案", "机型", "显存", "用途", "冷启动增量", "单条成本")
	for _, s := range Stations {
		fmt.Printf("%-9s %-6s %-5s %-38s %-22s %s\n",
			s.Profile, s.Machine, fmt.Sprintf("≥%dG", s.MinVRAM), s.Use, s.Increment, s.CostNote)
	}
	fmt.Println("\n启动: h3ctl up <档案> [会话名]   收工: h3ctl down [会话名]")
	return nil
}

// preflightMachine: 本地预检(VM 端 bootstrap stage_gate 仍会拦第二道)。
// force=true 只放行显存不足类错配(实验用, 如 L4 硬跑视频), 仍打印代价提示。
func preflightMachine(s Station, machine string, force bool) error {
	if machine == s.Machine {
		return nil
	}
	switch {
	case s.Kind == "video" && (machine == "L4" || machine == "T4"):
		if !force {
			return fmt.Errorf("%s 是视频档案, %s(24G/15G)装不下 55GB 集合 → 权重流送(同 T4 712s/张死法)。\n"+
				"  确要实验(代价未知)请加 --force; 生产请用 %s", s.Profile, machine, s.Machine)
		}
		fmt.Printf("⚠ --force 放行: %s × %s 实验跑, 代价未知, VM gate 仍以显存为准\n", s.Profile, machine)
	case s.Kind == "image" && machine == "A100":
		fmt.Printf("⚠ 生图档案被指定到 %s — CU ~3× 浪费(T4 1.15 / L4 1.67 / A100 5.4 CU/h)\n", machine)
	default:
		return fmt.Errorf("档案 %s 默认机型 %s, 指定了 %s; 用 --force 可放行", s.Profile, s.Machine, machine)
	}
	return nil
}

// hasBinary: 依赖存在性检查(colab-cli / ssh-keygen)
func hasBinary(name string) error {
	if _, err := exec.LookPath(name); err != nil {
		switch name {
		case "colab":
			return fmt.Errorf("未安装 google-colab-cli: uv tool install google-colab-cli")
		default:
			return fmt.Errorf("缺少 %s", name)
		}
	}
	return nil
}

// firstFlag: 从 flag.Args 里取第一个位置参数
func firstArg(fs *flag.FlagSet, what string) (string, error) {
	rest := fs.Args()
	if len(rest) == 0 {
		return "", fmt.Errorf("缺少 %s", what)
	}
	return strings.TrimSpace(rest[0]), nil
}

// guardPositional: flag 包在首个位置参数后停止解析 —— flag 误放位置参数之后
// 会被当位置参数吃掉, 绕过预检(2026-11-22 真实事故: "--machine L4" 被吃成会话名,
// 意外建起一个 A100)。所有命令 Parse 后必须过这道闸。
func guardPositional(fs *flag.FlagSet, maxPos int) error {
	for _, a := range fs.Args() {
		if strings.HasPrefix(a, "-") {
			return fmt.Errorf("flag %q 放在了位置参数之后(flag 包停止解析) — "+
				"所有 flag 必须在最前: 例 h3ctl up --machine L4 <档案>", a)
		}
	}
	if len(fs.Args()) > maxPos {
		return fmt.Errorf("多余的位置参数: %v", fs.Args()[maxPos:])
	}
	return nil
}

// validSession: 会话名 sanity(防把 flag/路径当会话名传给 colab)
func validSession(s string) error {
	if s == "" {
		return fmt.Errorf("会话名为空")
	}
	if strings.HasPrefix(s, "-") {
		return fmt.Errorf("非法会话名 %q (以 - 开头)", s)
	}
	for _, r := range s {
		ok := r == '-' || r == '.' || r == '_' ||
			(r >= '0' && r <= '9') || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z')
		if !ok {
			return fmt.Errorf("非法会话名 %q (含字符 %q)", s, r)
		}
	}
	return nil
}
