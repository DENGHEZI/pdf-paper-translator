# 原位中文 PDF：实现要点

目标：在原 PDF 上把英文**擦掉**、在**同一坐标**重绘中文，图片 / 矢量图 / 表格边框完全不动。

## 1. 核心 15 行

```python
import fitz

page = doc[pno]
for rect in rects_to_replace:
    page.add_redact_annot(fitz.Rect(rect.x0 - 0.7, rect.y0 - 0.9,
                                    rect.x1 + 0.7, rect.y1 + 0.9), fill=False)
page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                      graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                      text=fitz.PDF_REDACT_TEXT_REMOVE)
# 再逐块绘制中文
```

**三个参数都不能省：**
- `fill=False`：不刷白底。深色卡片上的白字替换后，背景不会被白色方块盖住。
- `PDF_REDACT_IMAGE_NONE`：不删图片。
- `PDF_REDACT_LINE_ART_NONE`：不删矢量线（表格边框、图表坐标轴）。

## 2. 中文断行（不要用 insert_htmlbox）

MuPDF 的 HTML 排版引擎对纯 CJK 连续串断行不可靠，长句会溢出栏宽、压到表格上。
自己写：CJK 单字成 token、连续 ASCII 词成 token、标点禁则。

```python
NO_HEAD = "，。、；：？！）】》”’…‰％"   # 不能出现在行首
NO_TAIL = "（【《“‘"                      # 不能出现在行尾

def wrap(text, width, size, font):
    toks = tokenize(text)          # CJK 单字 / ASCII 词 / 空格
    lines, cur, curw = [], "", 0.0
    for t in toks:
        tw = font.text_length(t, fontsize=size)
        if cur and curw + tw > width:
            if t[0] in NO_HEAD:    # 标点挂行末
                cur += t; lines.append(cur); cur, curw = "", 0.0; continue
            if cur[-1] in NO_TAIL: # 行尾不留开括号
                lines.append(cur[:-1]); cur = cur[-1] + t
                curw = font.text_length(cur, fontsize=size); continue
            lines.append(cur.rstrip())
            cur, curw = ("", 0.0) if t == " " else (t, tw)
        else:
            cur += t; curw += tw
    if cur.strip():
        lines.append(cur.rstrip())
    return lines
```

`layout()` 从原字号开始逐次减 0.2pt，直到 `行数 × 字号 × 行距 ≤ 块高`（下限 0.62 倍，
再小就说明译文比原文长太多，需要拆段或调整原文把术语挪到下一段）。

## 3. 绘制

```python
tw = fitz.TextWriter(page.rect, color=rgb)
y = rect.y0 + size * 0.92
for ln in lines:
    x = rect.x0 if align == "left" else rect.x0 + (width - font.text_length(ln, fontsize=size)) / 2
    tw.append((x, y), ln, font=font, fontsize=size)
    y += size * line_height
tw.write_text(page)
```

## 4. 防重叠

- `next_top(obstacles, bbox)`：向下扩展时，不越过下方最近障碍物的上沿。
- `right_limit(obstacles, bbox, y_end)`：右侧有宽 >30pt、高 >10pt 的元素时收缩右边界。
- 双栏论文里，右栏块 x0 ≈ 栏宽 + 间隔，`right_limit` 会自然把左栏文字挡在栏内。

## 5. 白名单式处理（推荐）

不要“猜哪些块要译”，而是**只处理映射表里明确列出的块**，其余一律不 redact、不重绘：

```python
if key in body_map:      # 普通段落：整块 redact + 重绘
elif key in raw_map:     # 精确 box：[box, 文本, 对齐, 字号, 粗体]
elif key in table_blocks:# 单元格式：逐 span 替换，保留列对齐
else:
    continue             # 图内标签 / 参考文献 / 公式 / 数据 —— 原样保留
```

好处：漏掉的块保持英文（可发现），不会出现空白页或误伤图表。

## 6. 三类特殊块

| 类型 | 处理 |
|---|---|
| 标题 / 作者 / 单位 | `raw` 模式，给精确 box + 居中；原两行标题可合并为一行居中 |
| 章节标题 | 粗体重绘；原块常很窄，需把绘制宽度扩到整栏（`x1 = min(栏右, x0 + 245)`） |
| 真表格（混淆矩阵等） | `cells` 模式：逐 span 按文本映射替换并居中；数字保留原文，只译表头/行列标签 |

## 7. 字体与体积

```python
F_SONG = fitz.Font(fontfile=r"C:/Windows/Fonts/simsun.ttc")   # 宋体
F_HEI  = fitz.Font(fontfile=r"C:/Windows/Fonts/simhei.ttf")   # 黑体（标题/加粗）
```

- SimSun 缺 `ŷ ϕ ∆ ⊆ − ⊂ ∂ ∇`，替换为 `y^ φ Δ ∈ -` 等（`φ Δ ∈ ρ ∑ ≈ ≤ ≥ ± ° µ ³ ² × — …` 都有）。
- TextWriter 每次写会重复嵌入字体，文件可涨到 15MB：
  `doc.subset_fonts(); doc.save(out, garbage=4, clean=True)` 可显著缩小。

## 8. 验收（模型看不了图时）

```python
# 越界：左栏文字 x1 不应超过栏分隔线；右栏文字 x0 不应小于栏起点
# 重叠：中文 span 与保留的英文 span（表格/图内标签）面积交叠 > 6 且 > 25% 视为碰撞
# 串行：两个中文 span 纵向重叠（|y0 差| > 2）视为段落溢出
# 缩放：layout 返回的 scale < 0.8 的块要人工复核
```

模板见本仓库 `scripts/` 之外的实际项目用法；核心是**用坐标断言代替肉眼**。
