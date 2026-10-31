# 踩坑清单（真实论文上验证过）

这些都是实际做 E1 / E5 / E7 / E9 / P01 / P02 / P03 / P05 八篇论文时踩出来的。
每条都给出**现象 → 原因 → 处理**。

## 一、图片抽取

### 1. 图注是窄块，被宽度阈值滤掉
- 现象：21 张图只抽出 16 张，缺的几张图注都是居中短句（`Figure 12: Sample Annotation...`，块宽仅 212pt）。
- 原因：图注宽度过滤设成了 `w > 250`。
- 处理：**图注宽度阈值降到 120**。图注普遍居中，比正文窄得多。

### 2. 正文段落被误判成图注
- 现象：`Figure 4 shows an exemplary visualization...`、`Figure 9 shows boxplots...` 这种**正文句子**被当成图注，导致区域算错。
- 原因：这些段落以 `Figure N` 开头，正则匹配成功。
- 处理：加**块高 < 45pt** 限制。图注一般 1–2 行（10–30pt），正文段落高 100–300pt。
  ```python
  is_caption = CAP_RE.match(first_line) and (bbox_h < 45) and (bbox_w > 120)
  ```
- 备用判据：图注块宽度通常 < 原文内容宽度的 85%，且**下一块是正文段落**。

### 3. 图内文字与正文同字号，无法靠字号区分
- 现象：图表里的坐标轴标签、图例、子图标注都是 11pt，与正文一致。
- 处理：正文块判定用 **宽度 > 内容宽 × 0.70 且字符数 ≥ 80**；图内标签块要么窄、要么短。

### 4. 图跨页（图在上页底部，图注在下页顶部）
- 现象：`Figure 11` 的图注在 p20 顶部（y=74），同页上方没有图 → 区域高 < 40pt，被判“太矮”跳过。
- 处理：区域高度 < 40 时**回退到上一页**：
  ```
  上界 = 上一页“最后一张图 y0 之上的正文块底边”
  下界 = 上一页高度 - 20
  ```

### 5. 用「图形簇上溯」会吞掉正文
- 现象：`get_drawings()` 的矩形跨越正文区，向上聚类一直升到页顶，把正文段落裁进图里（验证方法：裁切区内最长文本行 > 110 字符）。
- 处理：**不要**只用 drawings 聚类。用「正文块底边 + 上一图注底边」作上界，drawings 只作辅助。

### 6. 图由上百个位图碎片拼成
- 现象：`len(page.get_images())` = 120，每片 43×77 到 1123×898，无法重组。
- 处理：放弃按碎片还原，改**按区域整页渲染** `page.get_pixmap(clip=rect, dpi=250)`。

### 7. 单页多图
- 处理：图注按 y 排序，`prev = 上一图注底边`，逐张推出上界。

### 8. 校验方法（必做）
```python
inner = page.get_text("text", clip=rect)
longest = max(len(x) for x in inner.splitlines())
# longest > 110 → 大概率混入正文
```
再统计非白像素占比（`< 3%` 可能是空白裁切）。

### 8b. 图注块的**首行是空行**
- 现象：P02 的 21 张图只抽出 13 张，缺 1–6、11、14；且日志里**没有任何警告**（说明图注压根没被识别）。
- 原因：排版把编号前的空行也算进了同一个 text block，`lines[0]` 是 `""`，图注文字在 `lines[1]`。
  ```python
  m = CAP_RE.match(lines[0])      # ✗ 全部落空
  ```
- 处理：**扫描块内任意一行**——
  ```python
  label = next((CAP_RE.match(ln).group(1) for ln in lines
                if ln and CAP_RE.match(ln)), None)
  ```
- 教训：`probe_pdf.py` / `extract_text.py` / `extract_figures.py` 三处都要用同一套判据，
  否则「探查说有 21 张图」但「抽图只出 13 张」，很难查。

### 8c. 跨页图的下界会裁进页脚页码
- 现象：跨页图 `y 472–822`，把页脚 “19” 也框进去了。
- 处理：底部先取 `page.height - 20`，再扫一遍页面底部 12% 区域里高度 <40pt 的文本块（页码/版权行），取 `min(bottom, 该块 y0 - 2)`。

### 8d. 栏数判断不要用「x0 是否有左右两簇」
- 现象：P02 明明是单栏，却被判成双栏——因为图表里的标签块落在右侧。
- 处理：看**最宽的那批文本块占页宽的比例**：单栏正文 ~0.75–0.85W，双栏单栏正文仅 ~0.40–0.48W。
  ```python
  median_wide < 0.55 * page_width   # → 双栏
  ```

## 二、参考文献

### 9. `(?<=[.)])` 后视断言导致整段切不开
- 现象：38 条参考文献只切出 1 条。
- 原因：第 1 条以 `... West Conshohocken, PA` 结尾（**无句点**），后面直接跟 `2. ASTM...`，后视断言不成立。
- 处理：改**纯前瞻** `\s(?=\d{1,2}\.\s+[A-Z])`，再过滤以 `^\d{1,2}\.` 开头的片段。
- 另一种坑：要求“首词后跟逗号”的正则（`[A-Z][a-z]+,`）会把 `7. Maintenance Guidebook...` 并进上一条。

### 10. 编号连续性自检
```python
nums = [int(re.match(r"^(\d{1,2})\.", r).group(1)) for r in refs]
assert nums == list(range(1, len(nums)+1))
```
不连续 = 切分或漏页。

## 三、双栏论文（原位模式）

### 11. 首页“标题+摘要”被合并成跨栏大块
- 现象：一个 block 的 bbox 横跨左右两栏（x 56–545），直接按块替换会串字。
- 处理：该块改用 `raw` 模式，按 span 级坐标逐段精确回填；或按 x 是否 < 栏分隔线拆成两块。

### 12. 粗体章节标题原块很窄
- 现象：`B. CNNs` 的 bbox 只有 40pt 宽，中文标题被缩到 0.66 倍。
- 处理：**粗体标题块扩展绘制宽度到整栏**（`x1 = min(栏右边界, x0 + 245)`）。

### 13. 混淆矩阵/结果表是真表格（带矢量边框）
- 现象：数字是文本 span，表头需译，但整块重绘会丢列对齐。
- 处理：`cells` 模式——按 span 文本精确映射替换并居中重绘，数字保留原文；或整表保留英文、只译表注。

## 四、原位 PDF 的技术要点

### 14. `fill=False` 是关键
```python
page.add_redact_annot(rect, fill=False)
page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                      graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                      text=fitz.PDF_REDACT_TEXT_REMOVE)
```
不刷白底 → 深色卡片上的白字替换后背景不受损；`IMAGE_NONE` / `LINE_ART_NONE` 保证图片与矢量图不被删。

### 15. 不要用 `insert_htmlbox` 排中文长句
MuPDF 的 HTML 引擎对纯 CJK 连续串断行不可靠，会溢出栏宽压到表格。
自己写 `wrap()`：CJK 单字成 token、连续 ASCII 词成 token、标点禁则（`，。、；：？！）】》` 不出现在行首），再用 `fitz.TextWriter` 逐行绘制。

### 16. 字体
- 正文 `simsun.ttc`（宋体），标题/加粗 `simhei.ttf`（黑体）。
- SimSun 缺 `ŷ ϕ ∆ ⊆ − ⊂ ∂ ∇` → 替换成 `y^ φ Δ ∈ -` 等。
- 体积会膨胀（TextWriter 重复嵌入字体）：`doc.subset_fonts()` + `save(garbage=4, clean=True)`。

## 五、Word 模式

### 17. 用户说“图片没保存”
- 现象：文字译完了，用户看不到图。
- 原因：PDF 里的图是位图碎片 / 矢量图，没内嵌进 docx。
- 处理：抽图 → `add_picture()` → **自检 `word/media/` 文件数 == 原文图数**，并把 PNG 另存一份到 `figures/` 供用户直接查看。

### 18. 宽表在 A4 竖排会挤
- 处理：列数 ≥ 10 时字号降到 7pt；必要时该节改横向（`section.orientation`）。

### 19. 预览器不渲染内嵌图
- 现象：docx 里明明有 12 张图，网页预览一片空白。
- 处理：告知用户用 Word/WPS 打开；或额外导出 PDF 供预览。
