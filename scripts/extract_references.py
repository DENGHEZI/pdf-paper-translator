#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽取参考文献条目（保持英文原文）。

支持两种编号风格：`1. Author...` 与 `[1] Author...`。
切分策略用**纯前瞻**（`\\s(?=N. 大写)`），不用后视断言——
真实论文里第 1 条常以 "...PA"、"...doi:..." 之类无句点内容结尾，
用 `(?<=[.)])` 后视会导致整段切不开（只得 1 条）。

用法:
    python extract_references.py --pdf INPUT.pdf --start-page 23
    python extract_references.py --pdf INPUT.pdf --start-page 23 --json refs.json
"""
import argparse
import json
import re
import sys

try:
    import fitz
except ImportError:
    sys.exit("需要 PyMuPDF：pip install pymupdf")

NUM_RE = re.compile(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})\.)")
HEADER_NOISE = [
    r"Preprint submitted to [^.]{0,120}",
    r"Available (?:under|at) https?://\S+",
    r"Preprints? \(www\.preprint\.org\)[^.]*",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--start-page", type=int, default=1, help="从第几页开始找 References（1-based）")
    ap.add_argument("--json", default=None, help="把结果写到 JSON")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    chunks, started = [], False
    for pno in range(args.start_page - 1, doc.page_count):
        t = doc[pno].get_text("text")
        if not started:
            m = re.search(r"\bReferences\b", t)
            if not m:
                continue
            started = True
            t = t[m.end():]
        chunks.append(t)

    raw = " ".join(chunks)
    for pat in HEADER_NOISE:
        raw = re.sub(pat, " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = re.sub(r"\s*Appendix\s*$", "", raw, flags=re.I)

    # 纯前瞻切分
    parts = re.split(r"\s(?=(?:\[\d{1,2}\]|\d{1,2}\.\s+[A-Z]))", raw)
    refs = [p.strip() for p in parts if NUM_RE.match(p.strip()) and len(p.strip()) > 25]

    nums = []
    for r in refs:
        m = re.match(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})\.)", r)
        if m:
            nums.append(int(m.group(1) or m.group(2)))

    print(f"提取到 {len(refs)} 条参考文献")
    if nums:
        print(f"编号范围 {min(nums)}..{max(nums)}")
        missing = [i for i in range(min(nums), max(nums) + 1) if i not in nums]
        if missing:
            print(f"!! 缺失编号: {missing}  —— 说明切分正则仍需调整")
        else:
            print("编号连续，无缺失")
    for r in refs[:2]:
        print("  样例:", r[:100])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(refs, f, ensure_ascii=False, indent=1)
        print("已写入", args.json)


if __name__ == "__main__":
    main()
