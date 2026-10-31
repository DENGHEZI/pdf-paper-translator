#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽取论文全文与块清单，供逐段翻译时对照，防止漏段。

产物（--out-dir 下）:
    fulltext.txt   按页分隔的全文
    blocks.json    每个文本块的 key/页码/bbox/字号/是否粗体/分类/文本
    blocks.txt     人读版块清单（翻译时直接看这个）

用法:
    python extract_text.py --pdf INPUT.pdf --out-dir work/
"""
import argparse
import json
import os
import re

try:
    import fitz
except ImportError:
    raise SystemExit("需要 PyMuPDF：pip install pymupdf")

CAP_RE = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*(\d+|[A-Z]\d+)\s*[:.]?", re.I)
TAB_RE = re.compile(r"^\s*(?:Table|表)\s*(\d+|[IVX]+|[A-Z]\d+)\s*[:.]?", re.I)
H1_RE = re.compile(r"^\s*(?:\d+\.|[IVX]+\.)\s+[A-Z]")
H2_RE = re.compile(r"^\s*(?:\d+\.\d+\.?|[A-Z]\.)\s+[A-Z]")          # 1.2 Xxx / A. Xxx
H3_RE = re.compile(r"^\s*(?:\d+\.\d+\.\d+\.?\s+[A-Z]|\(?\d+\)\s+[A-Z]|[a-z]\)\s)")


def norm(s):
    for a, b in (("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"), ("ﬃ", "ffi"), ("ﬄ", "ffl")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def classify(txt, size, bold, is_cap, is_tab, pno, last_page):
    # 参考文献标题：PDF 里常被拆成 “R EFERENCES”，要忽略字间空格
    if re.sub(r"\s+", "", txt).lower() in ("references", "bibliography", "参考文献"):
        return "refs_title"
    if is_cap:
        return "figcap"
    if is_tab:
        return "tablecap"
    if H3_RE.match(txt):
        return "h3"
    # 先判 h1（"1. Xxx" / "IV. Xxx"），否则 "I. INTRODUCTION" 会被 [A-Z]\. 抢成 h2
    if H1_RE.match(txt):
        return "h1"
    if H2_RE.match(txt):
        return "h2"
    if pno == last_page and re.match(r"^\s*(?:\[\d+\]|\d+\.)\s", txt):
        return "ref"
    return "body"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out-dir", default="work")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    doc = fitz.open(args.pdf)
    blocks, lines_out, full = [], [], []
    in_refs = False   # 遇到 References 标题后，其后所有块都视为参考文献

    for pno in range(doc.page_count):
        page = doc[pno]
        text = page.get_text("text")
        full.append(f"\n===== PAGE {pno + 1} ({len(text)}) =====\n{text}")

        for bi, b in enumerate(page.get_text("dict")["blocks"]):
            if b["type"] != 0:
                continue
            spans = [s for l in b["lines"] for s in l["spans"]]
            if not spans:
                continue
            raw = " ".join(s["text"] for s in spans)
            txt = norm(raw)
            if not txt:
                continue
            sizes = [round(s["size"], 1) for s in spans]
            size = max(set(sizes), key=sizes.count)
            bold = bool(re.search(r"Bold|Heavy|Medi", spans[0]["font"]))
            # 图注/表注：块内任意一行匹配（图注块首行可能是空行）
            block_lines = ["".join(s["text"] for s in l["spans"]).strip() for l in b["lines"]]
            bh = b["bbox"][3] - b["bbox"][1]
            is_cap = any(ln and CAP_RE.match(ln) for ln in block_lines) and bh < 45
            is_tab = any(ln and TAB_RE.match(ln) for ln in block_lines) and bh < 60
            key = f"p{pno + 1}_{bi}"
            cat = classify(txt, size, bold, is_cap, is_tab, pno + 1, doc.page_count)
            if cat == "refs_title":
                in_refs = True
            elif in_refs and cat in ("body", "h1", "h2", "h3"):
                cat = "ref"

            blocks.append({
                "key": key, "page": pno + 1,
                "bbox": [round(v, 1) for v in b["bbox"]],
                "size": size, "bold": bold, "cat": cat, "text": txt,
            })
            lines_out.append(f"=== {key}  p{pno + 1}  [{cat}]  {size}pt bold={int(bold)} ===\n{txt}\n")

    with open(os.path.join(args.out_dir, "fulltext.txt"), "w", encoding="utf-8") as f:
        f.write("".join(full))
    with open(os.path.join(args.out_dir, "blocks.json"), "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out_dir, "blocks.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))

    from collections import Counter
    c = Counter(b["cat"] for b in blocks)
    print(f"blocks: {len(blocks)}  ->  {args.out_dir}/blocks.json / blocks.txt / fulltext.txt")
    print("categories:", dict(c))
    print("提示：翻译时对 blocks.txt 逐条处理；body/h1/h2/h3 为需要翻译的内容，"
          "ref 保持原文，figcap/tablecap 需译说明文字。")


if __name__ == "__main__":
    main()
