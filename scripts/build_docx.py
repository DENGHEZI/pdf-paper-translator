#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 content.json 渲染成中文论文 Word（.docx）。

content.json 结构见 references/content-schema.md，最少可只给 meta + body。

用法:
    python build_docx.py --content work/content.json --out 论文_中文译版.docx
    # 图片目录默认取 content.json 所在目录下的 figures/
"""
import argparse
import json
import os
import re
import sys

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

try:
    import fitz  # 仅用于读取 PNG 尺寸，做限高
except ImportError:
    fitz = None

ALIGN = {"just": WD_ALIGN_PARAGRAPH.JUSTIFY,
         "center": WD_ALIGN_PARAGRAPH.CENTER,
         "left": WD_ALIGN_PARAGRAPH.LEFT}


def cn_font(run, name="宋体", size=None, bold=None, italic=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color


class Builder:
    def __init__(self, cfg, fig_dir):
        self.cfg = cfg
        self.fig_dir = fig_dir
        self.doc = Document()
        st = cfg.get("style", {})
        self.base_size = st.get("body_size", 10.5)
        self.line_spacing = st.get("line_spacing", 1.32)
        self.fig_width = st.get("figure_width_cm", 14.0)
        self.fig_max_h = st.get("figure_max_height_cm", 17.0)
        self._setup()

    def _setup(self):
        sec = self.doc.sections[0]
        sec.top_margin = Cm(2.0)
        sec.bottom_margin = Cm(2.0)
        sec.left_margin = Cm(2.2)
        sec.right_margin = Cm(2.2)
        normal = self.doc.styles["Normal"]
        normal.font.name = "Times New Roman"
        normal.font.size = Pt(self.base_size)
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        normal.paragraph_format.line_spacing = self.line_spacing
        normal.paragraph_format.space_after = Pt(4)

    def para(self, text, size=None, bold=False, italic=False, align="just",
             color=None, indent=True, before=0, after=4, name="宋体"):
        size = size or self.base_size
        p = self.doc.add_paragraph()
        p.alignment = ALIGN[align]
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        if indent and align == "just":
            p.paragraph_format.first_line_indent = Pt(size * 2)
        run = p.add_run(text)
        cn_font(run, name, size, bold, italic, color)
        return p

    def bullet(self, text, size=None):
        size = size or self.base_size
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.left_indent = Pt(21)
        p.paragraph_format.first_line_indent = Pt(-21)
        p.paragraph_format.space_after = Pt(2)
        cn_font(p.add_run(text), "宋体", size)

    def equation(self, text, number=None):
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        cn_font(p.add_run(text), "Cambria Math", self.base_size, italic=True)
        if number:
            cn_font(p.add_run("　　" + number), "Times New Roman", self.base_size)

    def figure(self, label):
        cap = self.cfg.get("figures", {}).get(str(label), "")
        path = os.path.join(self.fig_dir, f"fig_{label}.png")
        if os.path.exists(path):
            width = self.fig_width
            if fitz is not None:
                pm = fitz.Pixmap(path)
                aspect = pm.height / max(1, pm.width)
                if width * aspect > self.fig_max_h:
                    width = self.fig_max_h / aspect
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(2)
            p.add_run().add_picture(path, width=Cm(width))
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(8)
        cn_font(p.add_run(f"图 {label}　{cap}"), "宋体", 9)

    def table(self, label):
        spec = self.cfg.get("tables", {}).get(str(label))
        if not spec:
            print(f"  ! 表 {label} 在 content.json 的 tables 中不存在")
            return
        cap, header, rows = spec.get("caption", ""), spec.get("header", []), spec.get("rows", [])
        note = spec.get("note", "")
        if cap:
            self.para(cap, size=9, bold=True, align="left", indent=False,
                      before=6, after=2, name="黑体")
        ncol = len(header) or (len(rows[0]) if rows else 0)
        if not ncol:
            return
        t = self.doc.add_table(rows=1 + len(rows), cols=ncol)
        t.style = "Table Grid"
        fs = 7.0 if ncol >= 10 else (8.0 if ncol >= 6 else 8.5)
        for j, h in enumerate(header):
            c = t.cell(0, j)
            c.text = ""
            pp = c.paragraphs[0]
            pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pp.paragraph_format.space_after = Pt(0)
            cn_font(pp.add_run(str(h)), "黑体", fs, True)
        for i, row in enumerate(rows, 1):
            for j in range(ncol):
                v = row[j] if j < len(row) else ""
                c = t.cell(i, j)
                c.text = ""
                pp = c.paragraphs[0]
                pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pp.paragraph_format.space_after = Pt(0)
                cn_font(pp.add_run(str(v)), "宋体", fs)
        if note:
            self.para(note, size=7.5, align="left", indent=False, before=2, after=6)
        else:
            self.para("", size=4, indent=False, after=6)

    # ---------- 组装 ----------
    def header_block(self):
        m = self.cfg["meta"]
        self.para(m.get("title_zh", ""), size=17, bold=True, align="center",
                  indent=False, after=4, name="黑体")
        if m.get("title_en"):
            self.para(m["title_en"], size=9, italic=True, align="center", indent=False,
                      after=8, color=RGBColor(0x55, 0x55, 0x55))
        if m.get("authors"):
            self.para(m["authors"], size=11, align="center", indent=False, after=3)
        for a in m.get("affiliations", []):
            self.para(a, size=8.5, align="center", indent=False, after=1,
                      color=RGBColor(0x44, 0x44, 0x44))
        if m.get("correspondence"):
            self.para(m["correspondence"], size=8.5, align="center", indent=False,
                      after=2, color=RGBColor(0x44, 0x44, 0x44))
        if m.get("note"):
            self.para(m["note"], size=7.5, align="center", indent=False, after=10,
                      color=RGBColor(0x88, 0x88, 0x88))

    def abstract_block(self):
        m = self.cfg["meta"]
        if not m.get("abstract"):
            return
        self.para("摘要", size=12, bold=True, align="left", indent=False,
                  before=2, after=3, name="黑体")
        self.para(m["abstract"], size=10, indent=True, after=5)
        if m.get("keywords"):
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.space_after = Pt(8)
            cn_font(p.add_run("关键词："), "黑体", 10, True)
            cn_font(p.add_run(m["keywords"]), "宋体", 10)

    def nomenclature(self):
        nom = self.cfg["meta"].get("nomenclature")
        if not nom:
            return
        self.para("术语与符号", size=12, bold=True, align="left", indent=False,
                  before=6, after=3, name="黑体")
        for title, key in (("符号", "symbols"), ("缩写", "abbr")):
            items = nom.get(key) or []
            if not items:
                continue
            t = self.doc.add_table(rows=1 + len(items), cols=2)
            t.style = "Table Grid"
            for j, h in enumerate((title, "含义")):
                c = t.cell(0, j)
                c.text = ""
                cn_font(c.paragraphs[0].add_run(h), "黑体", 8.5, True)
            for i, (k, v) in enumerate(items, 1):
                for j, val in enumerate((k, v)):
                    c = t.cell(i, j)
                    c.text = ""
                    cn_font(c.paragraphs[0].add_run(str(val)), "宋体", 8.5)
            self.para("", size=4, indent=False, after=4)

    def body_blocks(self):
        for item in self.cfg.get("body", []):
            kind = item[0]
            if kind == "h1":
                self.para(item[1], size=14, bold=True, align="left", indent=False,
                          before=12, after=6, name="黑体")
            elif kind == "h2":
                self.para(item[1], size=12, bold=True, align="left", indent=False,
                          before=9, after=4, name="黑体")
            elif kind == "h3":
                self.para(item[1], size=self.base_size, bold=True, align="left",
                          indent=False, before=7, after=3, name="黑体")
            elif kind == "p":
                self.para(item[1], after=5)
            elif kind == "li":
                self.bullet(item[1])
            elif kind == "eq":
                self.equation(item[1], item[2] if len(item) > 2 else None)
            elif kind == "fig":
                self.figure(item[1])
            elif kind == "table":
                self.table(item[1])
            elif kind == "pagebreak":
                self.doc.add_page_break()
            elif kind == "quote":
                p = self.para(item[1], size=self.base_size - 0.5, indent=False, after=5)
                p.paragraph_format.left_indent = Pt(21)
            else:
                print(f"  ! 未知块类型: {kind}")

    def back_matter(self):
        back = self.cfg.get("back", {})

        def section(title, text):
            if not text:
                return
            self.para(title, size=13, bold=True, align="left", indent=False,
                      before=10, after=3, name="黑体")
            for ln in str(text).split("\n"):
                ln = ln.strip()
                if ln:
                    self.para(ln, size=self.base_size - 0.5, indent=True, after=3)

        section("作者贡献", back.get("author_contributions"))

        section("致谢", back.get("ack"))
        for item in back.get("ack_list", []):
            self.bullet("• " + item, size=self.base_size - 0.5)
        section("资助", back.get("funding"))
        section("声明", back.get("declaration"))

        refs = back.get("refs") or {}
        items = refs.get("items") or []
        if items:
            self.para("参考文献", size=14, bold=True, align="left", indent=False,
                      before=10, after=4, name="黑体")
            self.para("（以下保留英文原文，按原文顺序）", size=8.5, align="left",
                      indent=False, after=4, color=RGBColor(0x66, 0x66, 0x66))
            for r in items:
                p = self.doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.paragraph_format.left_indent = Pt(17)
                p.paragraph_format.first_line_indent = Pt(-17)
                p.paragraph_format.space_after = Pt(2)
                cn_font(p.add_run(r), "Times New Roman", 8.5)
            self._ref_count = len(items)
        else:
            self._ref_count = 0

    def save(self, out):
        self.doc.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fig-dir", default=None)
    args = ap.parse_args()

    with open(args.content, encoding="utf-8") as f:
        cfg = json.load(f)
    fig_dir = args.fig_dir or os.path.join(os.path.dirname(os.path.abspath(args.content)), "figures")

    b = Builder(cfg, fig_dir)
    b.header_block()
    b.abstract_block()
    b.nomenclature()
    b.body_blocks()
    b.back_matter()
    b.save(args.out)

    # 自检：内嵌图片数 vs 文中 fig 标记数
    import zipfile
    media = [n for n in zipfile.ZipFile(args.out).namelist() if n.startswith("word/media/")]
    fig_marks = sum(1 for it in cfg.get("body", []) if it[0] == "fig")
    print(f"saved: {args.out}")
    print(f"paragraphs={len(b.doc.paragraphs)} tables={len(b.doc.tables)} "
          f"media={len(media)} figures_in_body={fig_marks} refs={getattr(b, '_ref_count', 0)}")
    if fig_marks and len(media) != fig_marks:
        print(f"!! 图片数不匹配：body 中 fig 标记 {fig_marks} 个，实际内嵌 {len(media)} 张"
              f"（检查 {fig_dir}/fig_<label>.png 是否齐全）")


if __name__ == "__main__":
    main()
