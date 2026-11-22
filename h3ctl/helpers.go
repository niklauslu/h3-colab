package main

// 轻量 JSON 取值辅助 —— stdlib encoding/json 足够, 不引第三方。

import (
	"bytes"
	"encoding/json"
)

func jsonMarshal(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

// gjsonString: 取顶层(或 "a.b" 两级)字符串字段, 缺失返回 ""
func gjsonString(body, path string) string {
	v := gjsonPath(body, path)
	s, _ := v.(string)
	return s
}

func gjsonNumber(body, path string) float64 {
	v := gjsonPath(body, path)
	n, _ := v.(float64)
	return n
}

func gjsonPath(body, path string) any {
	var m map[string]any
	if json.Unmarshal([]byte(body), &m) != nil {
		return nil
	}
	cur := any(m)
	for _, part := range bytes.Split([]byte(path), []byte(".")) {
		mm, ok := cur.(map[string]any)
		if !ok {
			return nil
		}
		cur, ok = mm[string(part)]
		if !ok {
			return nil
		}
	}
	return cur
}

// gjsonObjects: 取某字段下的对象数组
func gjsonObjects(body, field string) []map[string]any {
	var m map[string]any
	if json.Unmarshal([]byte(body), &m) != nil {
		return nil
	}
	arr, ok := m[field].([]any)
	if !ok {
		return nil
	}
	out := make([]map[string]any, 0, len(arr))
	for _, it := range arr {
		if om, ok := it.(map[string]any); ok {
			out = append(out, om)
		}
	}
	return out
}

func fstr(m map[string]any, k string) string {
	s, _ := m[k].(string)
	return s
}
