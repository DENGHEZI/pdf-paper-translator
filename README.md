# pdf-paper-translator

> 把英文学术论文 PDF 翻译成中文，并输出**能直接交付的成品**——不是一段纯文本，而是带图片、表格、公式、分栏版式的 PDF 或 Word。

做科研的人大概都遇到过：一篇好论文，想给同组的人看，但全英文；机翻出来是一坨没有图、没有表、公式全乱的纯文本。

这个工具解决的就是最后一公里——**译文里的图还在原来的位置，表格还是表格，公式还有式号**。

## 两种产物

| 模式 | 说明 | 适用 |
|---|---|---|
| **原位中文 PDF** | 在原 PDF 上擦除英文、按原坐标重绘中文；图片、矢量图、表格边框、公式位置全部不动 | 想保持原版式、直接发给别人看 |
| **中文 Word 译版** | 抽取全文 → 翻译 → python-docx 排版；图片按原文位置嵌入，表格转为真 Word 表 | 想编辑、加批注、二次加工 |

## 快速开始

```bash
pip install pymupdf python-docx

git clone https://github.com/DENGHEZI/pdf-paper-translator.git
cd pdf-paper-translator

# 1) 先看这篇论文长什么样
python scripts/probe_pdf.py --pdf paper.pdf

# 2) 抽全文 + 块清单（翻译时照着 blocks.txt 逐条译，防漏段）
python scripts/extract_text.py --pdf paper.pdf --out-dir work/

# 3) 抽图（自动处理跨页图、窄图注、正文干扰）
python scripts/extract_figures.py --pdf paper.pdf --out-dir work/figures --dpi 250

# 4) 抽参考文献（保留英文）
python scripts/extract_references.py --pdf paper.pdf --start-page 23 --json work/refs.json

# 5) 写 work/content.json（格式见 references/content-schema.md），然后生成 Word
python scripts/build_docx.py --content work/content.json --out paper_中文译版.docx
```

生成后会自检并打印：

```
saved: paper_中文译版.docx
paragraphs=163 tables=8 media=12 figures_in_body=12 refs=38
```

`media` 与 `figures_in_body` 必须相等——这是“图片到底进没进文档”的硬指标。

## 它解决了哪些真问题

这些都不是设想，是做完 8 篇论文（双栏 IEEE、单栏 MDPI、arXiv 预印本、Elsevier 期刊）之后总结的：

- **图注是居中窄块**（宽 170–390pt），宽度阈值一大就整张图漏抽
- **图注块首行是空行**，只匹配 `lines[0]` 会全部漏检（21 张图只出 13 张，且没有报错）
- **长图注 3–5 行（50–80pt）**，用「块高 < 45pt」一刀切会把 13 张图漏成 1 张
- **正文里有 “Figure 1 shows…” / “Figure 4(A) is increased…”**，会被误判成图注并产生重复图号
- **双栏论文按整页宽裁图**，会把邻栏正文裁进来 → 按栏裁切
- **页边竖排水印**（arXiv）把内容区左界拉到 x=11
- **图跨页**：图在上页底部、图注在下页顶部
- **图是上百个位图碎片**拼成的，只能整区域渲染
- **参考文献标题被拆成 `R EFERENCES`**，直接匹配 → 提取到 0 条
- **参考文献里 `vol. 97. PMLR`** 被当成第 97 条 → 改按编号递增顺序提取
- **无编号（作者—年份）参考文献**，没有 `1.` 可选 → 按 `X. Surname,` 行首归并
- **双栏首页的“标题+摘要”合并成跨栏大块**，直接替换会串字
- **粗体章节标题原块只有 40pt 宽**，中文被缩到 0.66 倍

完整清单与处理办法见 [`references/pitfalls.md`](references/pitfalls.md)（29 条）。

## 验证过的论文

| 论文 | 版式 | 页数 | 图 | 参考文献 |
|---|---|---|---|---|
| IEEE 双栏（Letter） | 双栏 | 10 | 7/7 | 43（`[N]`） |
| Elsevier 预印本 | 单栏 | 44 | 20/20（13 + A1–A7） | 43（无编号） |
| 数据论文（A4） | 单栏 | 16 | 21/21 | 9 |
| 视觉检测（A4） | 单栏 | 13 | 8/8 | 25 |
| 断口分割（A4） | 单栏 | 25 | 12/12 | 38 |

端到端产物自检：`media=12 == figures_in_body=12`、`refs=38`。

## 翻译取舍（学术惯例）

- **译**：标题、单位、通讯作者、摘要、关键词、正文、章节标题、图注、表注、表头、术语表、致谢、资助
- **不译**：参考文献条目、作者姓名、机构与地名（加中文对照）、软件/数据集/标准代号（YOLOv7、DeepLabV3+、ASTM E399、RDD2022）、公式本体
- **公式**：用可读行间公式重排（Unicode 上下标 + `/` 表示分数），保留式号

## 仓库结构

```
.
├── SKILL.md                     # WorkBuddy / Claude 技能入口（含工作流与自检清单）
├── scripts/
│   ├── probe_pdf.py             # 结构探查
│   ├── extract_text.py          # 全文 + 块清单
│   ├── extract_figures.py       # 图注锚定抽图（核心）
│   ├── extract_references.py    # 参考文献切分
│   └── build_docx.py            # content.json → docx
├── references/
│   ├── pitfalls.md              # 19 个真实踩坑与解法
│   ├── content-schema.md        # content.json 规范
│   └── inplace-mode.md          # 原位中文 PDF 的实现要点
└── examples/
    └── content.example.json
```

## 作为技能使用

`SKILL.md` 带 YAML frontmatter，可直接放进 WorkBuddy / Claude 的 skills 目录，
或用 `@pdf-paper-translator` 触发。它规定了「先选模式 → 四步流程 → 三件必查」的执行顺序。

## 限制

- **翻译本身由模型完成**，脚本负责解析版式、抽图、排版与自检；不做机器翻译。
- 扫描版 PDF（纯图片、无文本层）不支持，需先 OCR。
- 公式从 PDF 文本层抽取时常已断裂（上下标丢失、符号错位），脚本只能给出可读近似，**不保证数学等价**。
- 原位模式对双栏 + 跨栏合并块的论文需要手工调 `raw` 区域的坐标（本仓库的自动化只覆盖 Word 模式的抽图/排版）。

## License

MIT © 2026 DENGHEZI
