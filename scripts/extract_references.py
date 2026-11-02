#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽取参考文献条目（保持英文原文）。

要点（都是真实论文里踩出来的）：
1. 编号顺序法：只按 1,2,3… 依次向后找，遇到断号即停。
   这比“凡 N. 大写就切”稳得多——参考文献里常有 `vol. 97. PMLR` 这类
   会被误认成第 97 条的内容。
2. 标题可能是 `R EFERENCES`（PDF 拆字），要去掉空白再比。
3. 第 1 条常以 `...PA` / `...doi:...` 结尾（无句点），
   所以不能用 `(?<=[.)])` 后视断言切分（会把 38 条切成 1 条）。

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

HEAD_WORDS = ("references", "bibliography", "参考文献")
HEADER_NOISE = [
    r"Preprint submitted to [^.]{0,140}",
    r"Published as a conference paper at ICLR \d{4}",
    r"Proceedings of the \w+ Conference on [^.]{0,80}",
    r"Available (?:under|at) https?://\S+",
    r"Preprints? \(www\.preprint\.org\)[^.]{0,80}",
]


def find_heading_page(doc, start_page):
    """返回 (页码索引, 标题行之后的行列表)。"""
    for pno in range(start_page - 1, doc.page_count):
        lines = doc[pno].get_text("text").splitlines()
        for i, ln in enumerate(lines):
            if re.sub(r"\s+", "", ln).lower() in HEAD_WORDS:
                return pno, lines[i + 1:]
    return None, None


def collect_text(doc, pno, tail_lines):
    parts = ["\n".join(tail_lines)]
    for j in range(pno + 1, doc.page_count):
        parts.append(doc[j].get_text("text"))
    raw = " ".join(parts)
    for pat in HEADER_NOISE:
        raw = re.sub(pat, " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = re.sub(r"\s*Appendix\s*$", "", raw, flags=re.I)
    # 参考文献区若后面还跟着附录，截断在 APPENDIX 处
    m_app = re.search(r"\bA\s*P\s*P\s*E\s*N\s*D\s*I\s*X\b", raw, re.I)
    if m_app and m_app.start() > 500:
        raw = raw[:m_app.start()]
    return raw


def pick_style(raw):
    if re.search(r"\[1\]", raw):
        return "bracket"
    if re.search(r"(?:^|[\s.])1\.\s+[A-Z]", raw):
        return "dot"
    return None


def extract_sequential(raw, style):
    """按编号 1,2,3… 顺序提取，断号即停。"""
    entries, pos, k = [], 0, 1
    while True:
        if style == "bracket":
            pat = re.compile(r"\[%d\]" % k)
        else:
            # “k. ”且前面是空白或句末，避免把 97. 里的 7. 当成第 7 条
            pat = re.compile(r"(?:^|(?<=[\s.]))%d\.\s+(?=[A-Z])" % k)
        m = pat.search(raw, pos)
        if not m:
            break
        start = m.start()
        if style == "bracket":
            nxt = re.compile(r"\[%d\]" % (k + 1)).search(raw, start + 1)
        else:
            nxt = re.compile(r"(?:^|(?<=[\s.]))%d\.\s+(?=[A-Z])" % (k + 1)).search(raw, start + 1)
        end = nxt.start() if nxt else len(raw)
        entries.append(raw[start:end].strip())
        pos = end
        k += 1
    return entries


def extract_unnumbered(doc, pno, tail_lines):
    """兜底：无编号（作者—年份）格式。

    以「X. Y. Surname,」开头的行视为新条目起点，其余为续行。
    """
    lines = list(tail_lines)
    for j in range(pno + 1, doc.page_count):
        lines += doc[j].get_text("text").splitlines()

    start_re = re.compile(r"^[A-Z]\.(?:\s?[A-Z]\.)*\s?[A-Z][a-zA-Z\-']+\s*,")
    entries = []
    for ln in lines:
        ln = ln.strip()
        if not ln or re.fullmatch(r"\d+", ln):
            continue
        if start_re.match(ln) or not entries:
            entries.append(ln)
        else:
            if entries[-1].endswith("-") and ln[:1].islower():
                entries[-1] = entries[-1][:-1] + ln        # 修复跨行连字符
            else:
                entries[-1] += " " + ln
    return [re.sub(r"\s+", " ", e).strip() for e in entries if len(e) > 20]


def extract_by_year(doc, pno, tail_lines):
    """兜底二：无编号且作者用全名（ICLR/NeurIPS 风格）。

    这类条目几乎都以年份结尾（“… In ACL, 2020.”），所以按“年份+句点”收尾来切。
    """
    lines = list(tail_lines)
    for j in range(pno + 1, doc.page_count):
        lines += doc[j].get_text("text").splitlines()

    entries, cur = [], ""
    for ln in lines:
        ln = ln.strip()
        if not ln or re.fullmatch(r"\d+", ln):
            continue
        if len(entries) >= 20 and re.search(r"\bA\s*P\s*P\s*E\s*N\s*D\s*I\s*X\b", ln, re.I):
            break                                # 进入附录，参考文献结束
        if re.match(r"^Published as a conference paper", ln, re.I) or \
           re.match(r"^Proceedings of the ", ln):
            continue                             # 页眉噪声
        cur = (cur + " " + ln).strip() if cur else ln
        if re.search(r"(?:19|20)\d{2}[a-z]?\.\s*$", cur) and len(cur) > 25:
            entries.append(cur)
            cur = ""
    # 末尾残留（附录表格等）里没有年份就不收
    if cur.strip() and re.search(r"(?:19|20)\d{2}", cur):
        entries.append(cur.strip())
    return [re.sub(r"\s+", " ", e).strip() for e in entries if len(e) > 20]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--start-page", type=int, default=1, help="从第几页开始找 References（1-based）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    pno, tail = find_heading_page(doc, args.start_page)
    if pno is None:
        sys.exit(f"在 {args.start_page} 页之后未找到 References 标题")

    raw = collect_text(doc, pno, tail)
    style = pick_style(raw[:3000])   # 只看开头，避免被文末附录的 “1. …” 误导

    if style is None:
        refs = extract_unnumbered(doc, pno, tail)
        kind = "无编号（作者—年份·姓名缩写）"
        if len(refs) < 5:                       # 全名作者 → 改用“年份收尾”切分
            alt = extract_by_year(doc, pno, tail)
            if len(alt) > len(refs):
                refs, kind = alt, "无编号（作者—年份·全名）"
    else:
        refs = extract_sequential(raw, style)
        refs = [r for r in refs if len(r) > 20]
        kind = "[N]" if style == "bracket" else "N."

    print(f"标题所在页: p{pno + 1} | 编号风格: {kind}")
    print(f"提取到 {len(refs)} 条参考文献")
    if style is not None and refs:
        print(f"编号范围 1..{len(refs)}（顺序提取，无断号）")
    for r in refs[:2]:
        print("  样例:", r[:100])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(refs, f, ensure_ascii=False, indent=1)
        print("已写入", args.json)


if __name__ == "__main__":
    main()
