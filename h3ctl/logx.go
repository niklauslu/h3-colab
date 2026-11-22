package main

// logx — 模块化分级日志（用户要求：分模块、等级、中文 + emoji，VM 日志可直出）
//
// 格式: HH:MM:SS [模块] <emoji> 消息
// 等级: 🔍DEBUG ℹ️INFO ✅OK ⚠️WARN ❌ERROR 🚀START ⏳WAIT
// DEBUG 仅 --verbose 或 H3CTL_VERBOSE=1 时输出；ERROR 同时落 stderr。

import (
	"fmt"
	"os"
	"strings"
	"time"
)

type Level int

const (
	LDebug Level = iota
	LInfo
	LOk
	LWarn
	LErr
	LStart
	LWait
)

func (l Level) emoji() string {
	switch l {
	case LDebug:
		return "🔍"
	case LInfo:
		return "ℹ️ "
	case LOk:
		return "✅"
	case LWarn:
		return "⚠️ "
	case LErr:
		return "❌"
	case LStart:
		return "🚀"
	case LWait:
		return "⏳"
	}
	return "·"
}

var verbose = false

func initVerbose() {
	for _, a := range os.Args {
		if a == "-v" || a == "--verbose" {
			verbose = true
		}
	}
	if os.Getenv("H3CTL_VERBOSE") == "1" {
		verbose = true
	}
	// 从 args 里剥掉, 免得污染各命令的 flag 解析
	out := os.Args[:1]
	for _, a := range os.Args[1:] {
		if a != "-v" && a != "--verbose" {
			out = append(out, a)
		}
	}
	os.Args = out
}

func logM(mod string, lv Level, format string, a ...any) {
	msg := fmt.Sprintf(format, a...)
	line := fmt.Sprintf("%s [%s] %s %s", time.Now().Format("15:04:05"), mod, lv.emoji(), msg)
	if lv == LDebug && !verbose {
		return
	}
	if lv == LErr {
		fmt.Fprintln(os.Stderr, line)
		return
	}
	fmt.Println(line)
}

func debugf(mod, format string, a ...any) { logM(mod, LDebug, format, a...) }
func infof(mod, format string, a ...any)  { logM(mod, LInfo, format, a...) }
func okf(mod, format string, a ...any)    { logM(mod, LOk, format, a...) }
func warnf(mod, format string, a ...any)  { logM(mod, LWarn, format, a...) }
func errf(mod, format string, a ...any)   { logM(mod, LErr, format, a...) }
func startf(mod, format string, a ...any) { logM(mod, LStart, format, a...) }
func waitf(mod, format string, a ...any)  { logM(mod, LWait, format, a...) }

// hr: 分隔线（阶段性小结用）
func hr(title string) {
	bar := strings.Repeat("─", 46)
	if title == "" {
		fmt.Println(bar)
		return
	}
	fmt.Println(bar + "\n  「" + title + "」\n" + bar)
}
