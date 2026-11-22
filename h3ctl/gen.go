package main

// gen — 生成客户端(替代 scripts/h3_generate.py 的本地侧; agent 仍可直连隧道+HTTP)。
// 契约(h3api 外壳): /health → /upload → POST /generate → GET /status 轮询 → /result → /files 下载。

import (
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type strSlice []string

func (s *strSlice) String() string { return strings.Join(*s, " ") }
func (s *strSlice) Set(v string) error {
	*s = append(*s, v)
	return nil
}

func httpReq(method, path string, port int, body []byte, ctype string, timeout time.Duration) (int, []byte, error) {
	u := fmt.Sprintf("http://127.0.0.1:%d%s", port, path)
	var rd io.Reader
	if body != nil {
		rd = strings.NewReader(string(body))
	}
	req, err := http.NewRequest(method, u, rd)
	if err != nil {
		return 0, nil, err
	}
	if ctype != "" {
		req.Header.Set("Content-Type", ctype)
	}
	cl := &http.Client{Timeout: timeout}
	resp, err := cl.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("连不上 %s (隧道开着? h3ctl tunnel): %s", u, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, data, nil
}

func uploadForGen(port int, local string) (string, error) {
	b, err := os.ReadFile(local)
	if err != nil {
		return "", fmt.Errorf("读文件失败 %s: %s", local, err)
	}
	name := url.QueryEscape(filepath.Base(local))
	st, data, err := httpReq("POST", "/upload?name="+name, port, b, "application/octet-stream", 5*time.Minute)
	if err != nil {
		return "", err
	}
	if st != 200 {
		return "", fmt.Errorf("上传失败(%d): %s", st, truncate(data))
	}
	// {"path": "/content/ComfyUI/input/..."}
	p := gjsonString(string(data), "path")
	fmt.Printf("  ↑ %s → %s (%dB)\n", filepath.Base(local), p, len(b))
	return p, nil
}

func cmdGen(args []string) error {
	fs := flag.NewFlagSet("gen", flag.ExitOnError)
	mode := fs.String("mode", "t2v", "t2v|fl2v|ref2v")
	prompt := fs.String("prompt", "", "提示词(必填)")
	seed := fs.Int64("seed", 42, "种子")
	seconds := fs.Float64("seconds", 5, "时长 4~15s")
	width := fs.Int("width", 0, "宽(默认模板值)")
	height := fs.Int("height", 0, "高")
	name := fs.String("name", "", "产出目录名")
	port := fs.Int("port", 8188, "本地隧道端口")
	pollSec := fs.Float64("poll", 5, "轮询间隔(s)")
	timeout := fs.Float64("timeout", 3600, "等待上限(s)")
	dumpJSON := fs.Bool("json", false, "结束时打印完整 job JSON")
	var keyframes, refs strSlice
	fs.Var(&keyframes, "keyframe", "fl2v: 路径=0|-1, 可重复")
	fs.Var(&refs, "ref", "ref2v: 路径=image|video|audio, 可重复")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := guardPositional(fs, 0); err != nil {
		return err
	}
	if *prompt == "" {
		return fmt.Errorf("--prompt 必填")
	}

	// 0) 就绪检查
	st, data, err := httpReq("GET", "/health", *port, nil, "", 15*time.Second)
	if err != nil {
		return err
	}
	if st != 200 || !strings.Contains(gjsonString(string(data), "phase"), "ready") {
		return fmt.Errorf("外壳未就绪: %s", truncate(data))
	}

	// 1) 素材上传 + spec 组装
	type kv struct {
		Path  string `json:"path"`
		Index int    `json:"index,omitempty"`
		Type  string `json:"type,omitempty"`
	}
	var kfs, rfs []kv
	for _, kf := range keyframes {
		lhs, val, ok := strings.Cut(kf, "=")
		if !ok {
			return fmt.Errorf("--keyframe 格式 路径=0|-1, 收到 %s", kf)
		}
		idx := 0
		if val == "-1" {
			idx = -1
		}
		p, err := uploadForGen(*port, lhs)
		if err != nil {
			return err
		}
		kfs = append(kfs, kv{Path: p, Index: idx})
	}
	for _, r := range refs {
		lhs, typ, ok := strings.Cut(r, "=")
		if !ok || (typ != "image" && typ != "video" && typ != "audio") {
			return fmt.Errorf("--ref 格式 路径=image|video|audio, 收到 %s", r)
		}
		p, err := uploadForGen(*port, lhs)
		if err != nil {
			return err
		}
		rfs = append(rfs, kv{Path: p, Type: typ})
	}
	if *mode == "fl2v" && len(kfs) == 0 {
		return fmt.Errorf("fl2v 模式至少一个 --keyframe")
	}
	if *mode == "ref2v" && len(rfs) == 0 {
		return fmt.Errorf("ref2v 模式至少一个 --ref")
	}
	spec := map[string]any{"mode": *mode, "prompt": *prompt, "seed": *seed,
		"seconds": *seconds, "name": *name}
	if *width > 0 {
		spec["width"] = *width
	}
	if *height > 0 {
		spec["height"] = *height
	}
	if len(kfs) > 0 {
		spec["keyframes"] = kfs
	}
	if len(rfs) > 0 {
		spec["references"] = rfs
	}

	// 2) 提交
	body := jsonMarshal(spec)
	st, data, err = httpReq("POST", "/generate", *port, body, "application/json", 60*time.Second)
	if err != nil {
		return err
	}
	if st != 202 {
		return fmt.Errorf("提交被拒(%d): %s", st, truncate(data))
	}
	jid := gjsonString(string(data), "job_id")
	fmt.Printf("▶ 已入队 %s (mode=%s seed=%d seconds=%g)\n", jid, *mode, *seed, *seconds)

	// 3) 轮询 /status(agent 主契约)
	t0 := time.Now()
	var last, lastBody string
	for {
		time.Sleep(time.Duration(*pollSec * float64(time.Second)))
		st, data, err = httpReq("GET", "/status?job_id="+url.QueryEscape(jid), *port, nil, "", 30*time.Second)
		if err != nil {
			return err
		}
		if st != 200 {
			return fmt.Errorf("轮询异常(%d): %s", st, truncate(data))
		}
		s := string(data)
		lastBody = s
		state := gjsonString(s, "state")
		pct := gjsonNumber(s, "progress.pct_est")
		line := fmt.Sprintf("  [%6.0fs] %s", time.Since(t0).Seconds(), state)
		if pct > 0 {
			line += fmt.Sprintf(" %.0f%%", pct)
		}
		if line != last {
			last = line
			fmt.Println(line)
		}
		if state == "done" || state == "failed" || state == "cancelled" {
			if state != "done" {
				return fmt.Errorf("job %s — err: %s", state, gjsonString(s, "error"))
			}
			break
		}
		if time.Since(t0).Seconds() > *timeout {
			return fmt.Errorf("超时(>%gs), 可续看: /status?job_id=%s", *timeout, jid)
		}
	}

	// 4) 下载成片
	st, data, err = httpReq("GET", "/result?job_id="+url.QueryEscape(jid), *port, nil, "", 60*time.Second)
	if err != nil || st != 200 {
		return fmt.Errorf("取 result 失败(%d): %v %s", st, err, truncate(data))
	}
	dest := filepath.Join("outputs", fmt.Sprintf("%s_%s", gjsonString(string(data), "name")+"_",
		time.Now().Format("20060102_150405")))
	os.MkdirAll(dest, 0o755)
	for _, f := range gjsonObjects(string(data), "files") {
		fn := f["name"].(string)
		qs := url.Values{}
		qs.Set("job_id", jid)
		qs.Set("name", fn)
		qs.Set("subfolder", fstr(f, "subfolder"))
		qs.Set("type", fstr(f, "type"))
		st, blob, err := httpReq("GET", "/files?"+qs.Encode(), *port, nil, "", 5*time.Minute)
		if err != nil || st != 200 || len(blob) < 1024 {
			return fmt.Errorf("下载失败(%d): %s → %s", st, fn, truncate(blob))
		}
		out := filepath.Join(dest, fn)
		os.WriteFile(out, blob, 0o644)
		fmt.Printf("  ↓ %s (%dB)\n", out, len(blob))
	}
	fmt.Printf("✅ 完成 (%s) → %s\n", time.Since(t0).Round(time.Second), dest)
	if *dumpJSON {
		fmt.Println(lastBody)
	}
	return nil
}

func truncate(b []byte) string {
	s := string(b)
	if len(s) > 200 {
		s = s[:200] + "…"
	}
	return s
}
