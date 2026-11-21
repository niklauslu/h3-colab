# qwen21t 测试样例（按场景分目录）

模型：Qwen-Image-2.1-Turbo INT8 · 生产机型 **L4**（本文部分样片为 T4 复测，速度 ×4）· 全部可复现
完整测试分析见 `docs/test-report-qwen21t.md`。

**⚠️ img2img 三大铁律（坑⑳/㉓）**：
1. 参考图必须用**点号键**传：`"images.image_1": ["10", 0]`——列表/嵌套字典被静默丢弃（生成变纯文生图）
2. 采样必须用**官方配方**（`ManualSigmas` 专用曲线 + `CFGGuider` CFG8 + `SamplerCustomAdvanced`）——
   普通 KSampler + simple 调度器对 Turbo 蒸馏模型会糊/漂移；速度慢是 T4×CFG8 叠加（L4 预期 ~46s）
3. 提示词必须用 `<image1>` 标签显式引用参考图——只说 "the subject/person" 会自由发挥

## t2i/ — 文生图（无参考，不涉上述铁律）

| 文件 | 复现 |
|---|---|
| `t4_smoke.png` | T4 首测（对照机型），首张 91s |
| `l4_smoke.png` | L4 同题同 seed；续出 16s |
| `l4_text_poster.png` | 中文海报渲染；16s |
| `l4_2k.png` | 原生 2048²；42s |

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_t2i_api.json \
  --set "4.prompt=<提示词>" --set "6.seed=42" \
  --set "5.width=1024" --set "5.height=1536"     # 2K 用 2048/2048
```
smoke：`一只橘猫戴着宇航员头盔漂浮在太空中，远处是蓝色地球，电影感光线，细节丰富`
poster（seed 7）：`电影海报设计，深蓝色星空背景，金色大字标题「海螺影像」，下方一行小字「MINIMAX H3 · COLAB PIPELINE」，构图居中，简洁大气，金属质感字体`

## edit/ — 图生图①：指令编辑（官方配方）

原图：`source.png` · 工作流 `workflows/qwen21t_edit_official_api.json`
产出：`edit_official_cfg8.png` —— 头盔真被替换为红帽（T4 183s；L4 预期 ~46s）

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_edit_official_api.json \
  --set "10.image=edit_source.png" \
  --set "4.prompt=把宇航员的头盔换成红色棒球帽，白色宇航服和地球背景保持不变" \
  --set "14.noise_seed=42"
```

## mask_edit/ — 图生图②：指哪改哪（颜色标记，官方配方）

原图：`source_masked.png`（source.png + PIL 红椭圆标记头盔区）· 同官方工作流
产出：`maskedit_official.png` —— 红帽精准落在标记位置（T4 176s）

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_edit_official_api.json \
  --set "10.image=masked_source.png" \
  --set "4.prompt=The area painted in solid red on this image is a selection mask covering the helmet. Replace the red-painted helmet area with a red baseball cap on the cat's head. Keep the orange cat, white spacesuit and Earth background completely unchanged." \
  --set "14.noise_seed=7"
```

## compose/ — 图生图③：参考图合成（人物+场景，官方配方）

参考图：`ref_char.png` + `ref_scene.png`（**本管线 T2I 自产，无水印**；再生命令 seed 100/101，见 git 历史）
工作流 `workflows/qwen21t_compose_api.json`
产出：`compose_result.png` —— 人物穿参考图同款芥末黄雨衣+紫发，场景高保真（T4 7s 缓存命中；L4 预期 ~50s）

```bash
python3 scripts/h3_generate.py --workflow workflows/qwen21t_compose_api.json \
  --set "10.image=compose_char.png" --set "11.image=compose_scene.png" \
  --set "4.prompt=把 <image1> 中的人物放进 <image2> 的雨夜东京小巷场景中：人物撑着透明雨伞走在巷子中央，霓虹灯牌的光洒在湿漉漉的地面上，电影感构图，人物保持 <image1> 的服装与发型" \
  --set "14.noise_seed=42"
```

## rgba/ — 透明图（VAE 原生四通道）

| 文件 | 说明 |
|---|---|
| `rgba_native.png` | T2I 直出 RGBA：提示词要求透明背景，16s，透明占比 73.6% ✅ |
| `rgba_checker.png` | 上图棋盘格验证 |
| `bgremove_source.png` | 抠图原图（= ref_char.png） |
| `bgremove_result.png` | **改图模式抠图 ✅（官方配方）**：T4 168s，76.8% 透明，人物完整保持 |
| `bgremove_checker.png` | 抠图棋盘格验证 |

```bash
# 生成透明图: 标准 t2i 工作流 + 透明提示词 ✅
python3 scripts/h3_generate.py --workflow workflows/qwen21t_t2i_api.json \
  --set "4.prompt=A cute orange tabby cat astronaut figurine ..., isolated on a fully transparent background, the entire backdrop rendered as pure alpha transparency" \
  --set "6.seed=42"
# 抠图: 官方 edit 工作流 + <image1> 标签（缺标签=漂移成无关主体）
python3 scripts/h3_generate.py --workflow workflows/qwen21t_edit_official_api.json \
  --set "10.image=bgremove_source.png" \
  --set "4.prompt=Remove the background of <image1> completely and output the person from <image1> as a transparent PNG image, keep the person exactly unchanged" \
  --set "5.width=1024" --set "5.height=1536" --set "14.noise_seed=42"
```
**方法论教训**：alpha 统计只证格式不证内容——RGBA 输出必须目检主体保持（曾抓出"人变皮包"假阳性）。

## 权重与许可

- Turbo INT8 DiT 7.26GB + qwen3vl_8b INT8 编码器 9.35GB + VAE 0.68GB（Comfy-Org 重打包；abenzerps 同款备用）
- **Qwen Research License：仅限非商用**
