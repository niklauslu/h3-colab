# qwen21t 测试样例（按场景分目录）

模型：Qwen-Image-2.1-Turbo INT8 · 生产机型 **L4** · 全部可复现（工作流 + 注入参数齐全）
完整测试分析见 `docs/test-report-qwen21t.md`。

## t2i/ — 文生图

| 文件 | 复现 |
|---|---|
| `t4_smoke.png` | `MACHINE=T4 WEIGHTS=qwen21t` 拉起；首张 91s（对照机型） |
| `l4_smoke.png` | L4 同题同 seed；首张 32s / 续出 16s |
| `l4_text_poster.png` | 中文海报渲染；16s |
| `l4_2k.png` | 原生 2048²；42s |

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_t2i_api.json \
  --set "4.prompt=<提示词>" --set "6.seed=42" \
  --set "5.width=1024" --set "5.height=1536"     # 2K 用 2048/2048
```
提示词（smoke）：`一只橘猫戴着宇航员头盔漂浮在太空中，远处是蓝色地球，电影感光线，细节丰富`
提示词（poster）：`电影海报设计，深蓝色星空背景，金色大字标题「海螺影像」，下方一行小字「MINIMAX H3 · COLAB PIPELINE」，构图居中，简洁大气，金属质感字体`（seed 7）

## edit/ — 图生图①：指令编辑（CFG 梯度对照）

原图：`source.png`（t2i 的 l4_smoke 同图）· 工作流 `workflows/qwen21t_edit_api.json`

| 文件 | 配置 | 结果 |
|---|---|---|
| `edit_cfg1_cap_on_helmet.png` | CFG1 + "把头盔换成红帽" | 半服从：帽子叠在头盔上 |
| `edit_cfg8_degraded.png` | CFG8（官方模板值，缺专用 sigmas） | 过曝崩坏 |
| `edit_cfg3_replace.png` | **CFG3 + 明确措辞** ✅ | 猫脸露出、帽子戴正 |

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_edit_api.json \
  --set "10.image=edit_source.png" \
  --set "4.prompt=移除头盔和面罩，给猫戴上一顶红色棒球帽，露出橘猫的脸，白色宇航服和地球背景保持不变" \
  --set "6.cfg=3.0" --set "6.seed=42"   # 26s
```

## mask_edit/ — 图生图②：指哪改哪（颜色标记）

原图：`source_masked.png`（source.png + PIL 红色椭圆标记头盔区）· 同 edit 工作流

| 文件 | 配置 | 结果 |
|---|---|---|
| `maskedit_v1_fail.png` | CFG3 + 简短指代 | ❌ 整图漂移（红标被当内容） |
| `maskedit_v2_ok.png` | **CFG1 + 结构化英文指代** ✅ | 标记区精确替换，身份保持 |

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_edit_api.json \
  --set "10.image=masked_source.png" \
  --set "4.prompt=The area painted in solid red on this image is a selection mask covering the helmet. Replace the red-painted helmet area with a red baseball cap on the cat's head. Keep the orange cat, white spacesuit and Earth background completely unchanged." \
  --set "6.cfg=1.0" --set "6.seed=7"   # 16s；背景可能轻微漂移（8步全图再生成）
```

## compose/ — 图生图③：参考图合成（人物+场景）

参考图：`ref_char.jpg`（人物）+ `ref_scene.jpg`（雨夜小巷）· 工作流 `workflows/qwen21t_compose_api.json`
产出：`compose_result.png` —— **16s，电影级融合**（`<image1>/<image2>` 标签引用）

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_compose_api.json \
  --set "4.prompt=把 <image1> 中的人物放进 <image2> 的雨夜东京小巷场景中：人物撑着透明雨伞走在巷子中央，霓虹灯牌的光洒在湿漉漉的地面上，电影感构图，人物保持 <image1> 的服装与发型" \
  --set "6.cfg=3.0" --set "6.seed=42"
```

## rgba/ — 透明图（VAE 原生四通道，非后期抠图）

| 文件 | 说明 |
|---|---|
| `rgba_native.png` | T2I 直出 RGBA：提示词要求透明背景，16s，透明占比 73.6% |
| `rgba_checker.png` | 上图合成到棋盘格的验证图 |
| `bgremove_source.jpg` | 抠图原图（= ref_char.jpg） |
| `bgremove_result.png` | 改图模式去背景：11s，透明占比 69.6% |

```bash
# 生成透明图: 标准 t2i 工作流 + 透明提示词
python3 scripts/h3_generate.py --workflow workflows/qwen21t_t2i_api.json \
  --set "4.prompt=A cute orange tabby cat astronaut figurine ..., isolated on a fully transparent background, the entire backdrop rendered as pure alpha transparency" \
  --set "6.seed=42"
# 抠图: edit 工作流 + 去背景指令
python3 scripts/h3_generate.py --workflow workflows/qwen21t_edit_api.json \
  --set "10.image=bgremove_source.jpg" \
  --set "4.prompt=Remove the background completely and output the subject as a transparent PNG image, keep the subject unchanged"
```
下游注意：解码输出为 4 通道，三通道节点前接 `SplitImageWithAlpha`（`JoinImageWithAlpha` 掩码 1=透明）。

## 权重与许可

- Turbo INT8 DiT 7.26GB + qwen3vl_8b INT8 编码器 9.35GB + VAE 0.68GB（Comfy-Org 重打包；abenzerps 同款备用）
- **Qwen Research License：仅限非商用**（商用需向 Qwen 授权；产出物自用时注意）
