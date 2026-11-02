# content.json 规范

`scripts/build_docx.py` 读取的内容描述文件。只有 `meta` 和 `body` 是必需的。

```jsonc
{
  "meta": {
    "title_zh": "中文标题",
    "title_en": "English Title",            // 可选，显示为灰色斜体副标题
    "authors": "A ᵃ,ᵇ，B ᶜ",                 // 可保留原文姓名，仅加中文单位角标
    "affiliations": [
      "ᵃ 单位一，城市，国家",
      "ᵇ 单位二"
    ],
    "correspondence": "通讯作者：xxx@example.com",   // 可选
    "note": "预印本，DOI: ...；许可：CC-BY-NC-ND 4.0",  // 可选，灰色小字
    "abstract": "中文摘要全文……",
    "keywords": "关键词一 · 关键词二",
    "nomenclature": {                        // 可选，渲染为两张两列表
      "symbols": [["a₀", "初始裂纹尺寸"], ["B", "试样厚度"]],
      "abbr":    [["HOM", "同质数据集"]]
    }
  },

  "figures": {                               // 图注中文说明，key 为图号字符串
    "1": "数据集组成示意",
    "2": "三个数据集的 SSIM 分析"
  },

  "tables": {                                // key 为表号字符串
    "1": {
      "caption": "表 1：数据集特征",
      "header": ["特征", "同质", "异质"],
      "rows": [["N", "200", "588"], ["N_test", "38", "24"]],
      "note": "可选，表下方小字"
    }
  },

  "body": [
    ["h1", "1. 引言"],
    ["h2", "2.1 数据"],
    ["h3", "2.1.1 数据集划分"],
    ["p", "正文段落……"],
    ["li", "• 列表项"],
    ["eq", "SSIM(x, y) = l^α · c^β · s^γ", "(1)"],
    ["fig", "1"],
    ["table", "1"],
    ["quote", "缩进引用段"],
    ["pagebreak"]
  ],

  "back": {
    "author_contributions": "L.X.——原始稿件撰写、可视化；X.Y.——审阅与编辑、监督",  // 可选
    "ack": "致谢正文……",
    "ack_list": ["合作单位 A", "合作单位 B"],
    "funding": "资助说明……",
    "declaration": "伦理审查声明：不适用。\n知情同意声明：不适用。\n数据可得性声明：……\n利益冲突：……",  // 支持 \n 分行
    "refs": {
      "items": ["1. ASTM E399-22 ...", "2. ..."]   // 英文原文，照抄
    }
  },

  "appendix": {                              // 可选：排在参考文献之后的附录图表
    "title": "附录图表",
    "figures": ["8", "9"],                   // 复用 figures 里的图注与 fig_<label>.png
    "tables": [["表 3", "训练超参数"], ["表 4", "微调超参数"]]
  },

  "style": {                                 // 可选，覆盖默认排版
    "body_size": 10.5,
    "line_spacing": 1.32,
    "figure_width_cm": 14.0,
    "figure_max_height_cm": 17.0
  }
}
```

## 约定

- **图号/表号一律用字符串**（`"1"`、`"A1"`），对应图片文件名 `fig_<label>.png`。
- `body` 是**有序**列表，顺序即文档顺序；`fig` / `table` 出现在哪里，图/表就插在哪里。
- 图片默认从 `content.json` 同级的 `figures/` 目录找，可用 `--fig-dir` 覆盖。
- 公式用 Unicode 近似写法（`10⁻³`、`a₀`、`Σ`、`√`），`Cambria Math` 斜体渲染，式号右对齐。
- 参考文献**不翻译**，直接放英文条目。
