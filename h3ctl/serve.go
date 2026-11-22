package main

// serve — 本地网页控制台(薄视图: 用户定位"给人看的一键操作", agent 仍直连契约)。
// 后端经隧道反代 VM 外壳契约(/state /status /generate /upload /files /events),
// 面板零业务逻辑; --host 0.0.0.0 + --token 预留局域网/Tailscale 场景。

import (
	"embed"
	"flag"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

//go:embed console.html
var consoleFS embed.FS

func cmdServe(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:8090", "监听地址")
	session := fs.String("session", "h3", "站点会话名")
	host := fs.String("host", "", "覆盖 addr(--host 0.0.0.0 局域网开放)")
	token := fs.String("token", "", "访问令牌(设置后所有请求须带 ?token= 或 X-Token)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	listen := *addr
	if *host != "" {
		listen = *host + ":8090"
	}

	t := readTunnel(*session)
	vmPort := 8188
	if t != nil {
		vmPort = t.Port
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		b, _ := consoleFS.ReadFile("console.html")
		w.Write(b)
	})
	// /api/* → 隧道反代 VM 外壳(只放行契约端点, 不做通用代理)
	allow := map[string]string{
		"/api/state":    "/state",
		"/api/status":   "/status",
		"/api/result":   "/result",
		"/api/files":    "/files",
		"/api/generate": "/generate",
		"/api/cancel":   "/cancel",
		"/api/upload":   "/upload",
		"/api/events":   "/events",
	}
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		if t == nil {
			http.Error(w, `{"error":"隧道未连接 — 先 h3ctl up 或 h3ctl tunnel"}`, 502)
			return
		}
		target, ok := allow[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		backend := fmt.Sprintf("http://127.0.0.1:%d%s%s", vmPort, target, mustQuery(r))
		req, _ := http.NewRequestWithContext(r.Context(), r.Method, backend, r.Body)
		if r.Method == "POST" && strings.Contains(r.URL.Path, "upload") {
			req.Header.Set("Content-Type", "application/octet-stream")
		} else if r.Method == "POST" {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, 502)
			return
		}
		defer resp.Body.Close()
		for k, vv := range resp.Header {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		if r.URL.Path == "/api/events" {
			// SSE 透传: 边读边 flush
			fl, _ := w.(http.Flusher)
			buf := make([]byte, 4096)
			for {
				n, err := resp.Body.Read(buf)
				if n > 0 {
					w.Write(buf[:n])
					if fl != nil {
						fl.Flush()
					}
				}
				if err != nil {
					break
				}
			}
			return
		}
		io.Copy(w, resp.Body)
	})
	// 面板操作: down(收工) — 危险操作, 表单 POST + token
	mux.HandleFunc("/api/down", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "POST only", 405)
			return
		}
		stopTunnel(*session)
		out, err := colabOut("stop", "-s", *session)
		fmt.Fprintf(w, `{"ok":%v,"out":"%s"}`, err == nil, strings.ReplaceAll(out, `"`, `'`))
	})

	var handler http.Handler = mux
	if *token != "" {
		handler = withToken(mux, *token)
	}
	srv := &http.Server{Addr: listen, Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	fmt.Printf("▶ 面板 http://%s (会话=%s, VM 口=%d)\n", listen, *session, vmPort)
	return srv.ListenAndServe()
}

func withToken(next http.Handler, token string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("token") == token || r.Header.Get("X-Token") == token {
			next.ServeHTTP(w, r)
			return
		}
		http.Error(w, "token missing", 401)
	})
}

func mustQuery(r *http.Request) string {
	if r.URL.RawQuery == "" {
		return ""
	}
	return "?" + r.URL.RawQuery
}
