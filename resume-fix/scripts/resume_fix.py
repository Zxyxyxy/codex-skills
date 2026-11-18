"""Portable local PDF review renderer. No model, network, env or backend access."""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = json.loads((ROOT / "assets/layout.json").read_text(encoding="utf-8"))
RED, INK, GRAY, RULE = (tuple(LAYOUT[k]) for k in ("red", "ink", "gray", "rule"))
NUM = re.compile(r"\d+(?:[.,]\d+)*\s*(?:(?:万|千|亿)\s*)?(?:人次|小时|分钟|毫秒|万元|%|％|人|张|组|次|个|篇|元|天|周|月|年|倍|ms|k|K)?")


class ReviewError(ValueError):
    pass


class LayoutError(ReviewError):
    pass


def require(condition, message):
    if not condition:
        raise ReviewError(message)


def norm(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def font_for(path=None):
    return fitz.Font(fontfile=str(path)) if path else fitz.Font("cjk")


def attach_font(page, font, name="ReviewCJK"):
    page.insert_font(fontname=name, fontbuffer=font.buffer)


def plain(value, field):
    require(isinstance(value, str) and bool(value.strip()), f"{field}: 必须是非空文本")
    require(not re.search(r"\*\*|```|(?m:^\s*#{1,6}\s)|<!--", value), f"{field}: 使用纯文本，不要 Markdown 标记")
    return value


def numeric_tokens(text):
    # Never join adjacent numeric tokens by removing whitespace first.
    text = unicodedata.normalize("NFKC", text)
    return {norm(m.group()).replace(",", "") for m in NUM.finditer(text)}


def document_ready(doc):
    require(not doc.is_encrypted, "PDF 已加密，请先提供解锁副本")
    require(0 < len(doc) <= 30, "支持 1–30 页 PDF")
    for page in doc:
        require(page.rotation == 0, "旋转页需先生成旋转归零的本地副本，再 inspect")


def checked_rect(raw, page):
    require(isinstance(raw, list) and len(raw) == 4 and
            all(type(n) in (int, float) and math.isfinite(n) for n in raw), "bbox 必须是4个有限数值")
    rect = fitz.Rect(raw)
    require(not rect.is_empty and page.rect.contains(rect), "bbox 超出原页或为空")
    return rect


def inspect_pdf(pdf, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    document_ready(doc)
    pages = []
    for i, page in enumerate(doc, 1):
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"])
                if text.strip():
                    lines.append({"id": f"p{i}-l{len(lines)+1}", "bbox": list(line["bbox"]), "text": text})
        pages.append({"page": i, "size": list(page.rect), "lines": lines,
                      "searchable": len(norm(page.get_text())) >= 20})
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(out / f"page-{i}.png")
    result = {"version": 1, "pdf_sha256": digest(pdf), "pages": pages}
    save_json(out / "inspection.json", result)
    return {"inspection": str(out / "inspection.json"), "page_count": len(pages),
            "ocr_required_pages": [p["page"] for p in pages if not p["searchable"]]}


def sanitize_pdf(pdf, config, out, font_path=None):
    require(Path(pdf).resolve() != Path(out).resolve(), "不能覆盖源 PDF；另存脱敏副本")
    require(isinstance(config, dict) and config.get("authorized") is True, "隐私替换需要 authorized=true，先取得用户授权")
    edits = config.get("redactions")
    require(isinstance(edits, list) and edits, "redactions 不能为空")
    doc, font = fitz.open(pdf), font_for(font_path)
    document_ready(doc)
    rectangles, replacements = [], []
    for index, edit in enumerate(edits):
        require(isinstance(edit, dict), "隐私条目必须是对象")
        number = edit.get("page")
        require(type(number) is int and 1 <= number <= len(doc), "隐私条目页码无效")
        page = doc[number - 1]
        if "quote" in edit:
            matches = page.search_for(plain(edit["quote"], "quote"))
            require(len(matches) == 1, f"隐私条目 {index+1} 未唯一匹配；改用 inspect 中核对过的 bbox")
            rect = matches[0]
        else:
            rect = checked_rect(edit.get("bbox"), page)
        require(not any(n == number and r.intersects(rect) for n, r in rectangles), "脱敏区域重叠，请合并条目")
        replacement = edit.get("replacement", "")
        require(isinstance(replacement, str), "replacement 必须为文本")
        size = edit.get("font_size", min(10, rect.height * .7))
        require(type(size) in (int, float) and math.isfinite(size) and size >= 7, "替换字号至少7pt")
        if replacement:
            require(font.text_length(replacement, fontsize=size) <= rect.width - 1 and size <= rect.height - 1,
                    f"隐私条目 {index+1} 的替换文本放不下；扩大经核对的空白区域或缩短标签")
        rectangles.append((number, rect))
        replacements.append((number, rect, replacement, size))
        page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
    replacement_fonts = {}
    for page in doc:
        # Remove only overlapping image pixels (not an entire scanned page).
        page.apply_redactions(images=2, graphics=0, text=0)
        for link in page.get_links():
            page.delete_link(link)
        for annotation in list(page.annots() or []):
            page.delete_annot(annotation)
        for widget in list(page.widgets() or []):
            page.delete_widget(widget)
        # A source PDF may already have a subset font under our usual name.
        # Reusing that resource silently drops replacement glyphs not in it.
        names = {entry[4] for entry in page.get_fonts(full=True)}
        serial = 1
        while f"PrivacyCJK{serial}" in names:
            serial += 1
        name = f"PrivacyCJK{serial}"
        attach_font(page, font, name)
        replacement_fonts[page.number + 1] = name
    for number, rect, replacement, size in replacements:
        if replacement:
            require(all(c.isspace() or font.has_glyph(ord(c)) for c in replacement),
                    "脱敏字体缺少字符；请用 --font 指定兼容字体")
            doc[number-1].insert_text((rect.x0 + .5, rect.y1 - 2), replacement,
                                     fontname=replacement_fonts[number], fontsize=size, color=(0, 0, 0))
    for name in doc.embfile_names():
        doc.embfile_del(name)
    doc.set_metadata({})
    doc.del_xml_metadata()
    doc.set_toc([])
    doc.subset_fonts()
    data = doc.tobytes(garbage=4, deflate=True)
    with fitz.open(stream=data, filetype="pdf") as check:
        text = "\n".join(p.get_text() for p in check)
        for number, rect, replacement, _ in replacements:
            if replacement:
                require(norm(replacement) in norm(check[number-1].get_text(clip=rect)),
                        "脱敏替换文字未完整写入；检查字体或替换区域")
        for token in config.get("forbidden_text", []):
            require(isinstance(token, str) and token, "forbidden_text 必须含非空字符串")
            require(norm(token).lower() not in norm(text).lower(), "脱敏扫描失败；仍有禁止字段，请核对遗漏位置")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(data)
    return {"pdf": str(out), "redactions": len(edits), "pdf_sha256": digest(out),
            "visual_privacy_review_required": True}


def validate_review(pdf, review):
    require(isinstance(review, dict) and review.get("version") == 1, "review.version 必须为1")
    require(review.get("pdf_sha256") == digest(pdf), "PDF 哈希不匹配；对最终脱敏 PDF 重新 inspect")
    plain(review.get("target_role"), "target_role")
    plain(review.get("privacy_note"), "privacy_note")
    provenance = review.get("provenance", {})
    require(isinstance(provenance, dict) and provenance.get("kind") in {"model", "editor", "fixture"}, "必须声明 provenance.kind")
    plain(provenance.get("label"), "provenance.label")
    answers = review.get("answers", [])
    require(isinstance(answers, list), "answers 必须为列表")
    answer_map, confirmed_answers = {}, set()
    for answer in answers:
        require(isinstance(answer, dict) and answer.get("origin") == "user", "回答只能是明确用户补充，不能是模型的问题")
        ident = plain(answer.get("id"), "answer.id")
        require(ident not in answer_map, "answer.id 重复")
        answer_map[ident] = plain(answer.get("text"), "answer.text")
        status = answer.get("status", "confirmed")  # Legacy v1 recorded confirmed answers only.
        require(status in {"confirmed", "uncertain", "superseded"}, "answer.status 无效")
        if status == "confirmed":
            confirmed_answers.add(ident)
    focuses = review.get("focuses")
    require(isinstance(focuses, list) and 1 <= len(focuses) <= 30, "focuses 需要1–30段")
    doc = fitz.open(pdf)
    document_ready(doc)
    ids, question_ids, clean = set(), set(), []
    for index, focus in enumerate(focuses):
        require(isinstance(focus, dict), "focus 必须为对象")
        ident = plain(focus.get("id"), "focus.id")
        require(ident not in ids, "focus.id 重复")
        ids.add(ident)
        number = focus.get("page")
        require(type(number) is int and 1 <= number <= len(doc), "focus.page 无效")
        page = doc[number - 1]
        rect = checked_rect(focus.get("bbox"), page)
        original = page.get_text(clip=rect).strip()
        require(len(norm(original)) >= 8, "摘录没有足够可搜索文字，不能猜原文；请提供可搜索 PDF 或先本地 OCR")
        require(norm(plain(focus.get("source_text"), "source_text")) == norm(original), f"焦点{index+1}原句与 bbox 不一致")
        rewrite = plain(focus.get("rewrite"), "rewrite")
        plain(focus.get("title"), "title")
        plain(focus.get("role_label"), "role_label")
        evidence = focus.get("evidence")
        require(isinstance(evidence, list) and evidence, "每段改写需要 evidence")
        covered = [False] * len(norm(rewrite))
        allowed_answers = set()
        for item in evidence:
            require(isinstance(item, dict), "evidence 条目应为对象")
            claim = norm(plain(item.get("claim"), "evidence.claim"))
            require(claim in norm(rewrite), "evidence.claim 不在改写中")
            quote = item.get("quote", "")
            refs = item.get("answer_ids", [])
            require(isinstance(quote, str) and isinstance(refs, list) and all(isinstance(r, str) and r in answer_map for r in refs), "evidence 回答引用无效")
            require(all(r in confirmed_answers for r in refs), "未确认或已更正的回答不能作为改写证据")
            require((norm(quote) and norm(quote) in norm(original)) or refs, "证据必须来自当前原句或明确用户回答")
            if quote:
                require(norm(quote) and norm(quote) in norm(original), "证据原句不在当前段落或为空白")
            allowed_answers.update(refs)
            start = norm(rewrite).index(claim)
            covered[start:start + len(claim)] = [True] * len(claim)
        require(all(flag or not char.isalnum() for flag, char in zip(covered, norm(rewrite))), "改写中的事实性文字尚未全部挂接 evidence")
        source = original + "\n" + "\n".join(answer_map[k] for k in sorted(allowed_answers))
        require(not numeric_tokens(rewrite) - numeric_tokens(source), f"焦点{index+1}含无来源数字或更改单位；不得自动填数")
        for word in ("独立", "主导", "负责人"):
            require(word not in rewrite or word in source, f"焦点{index+1}可能升级个人职责：{word}")
        if re.search(r"未上线|上线前|(?:预计|计划|准备)[^。；\n]{0,12}上线", source):
            require(not re.search(r"已上线|成功上线|推动上线|上线后", rewrite) or
                    bool(re.search(r"已上线|成功上线|推动上线|上线后", source)), "可能把上线前阶段改成已上线")
        questions = focus.get("questions")
        require(isinstance(questions, list) and len(questions) <= 4, "每段0–4条补充记录／问题，不为凑数制造问题")
        for question in questions:
            require(isinstance(question, dict), "question 必须为对象")
            plain(question.get("title"), "question.title")
            plain(question.get("ask"), "question.ask")
            quote = plain(question.get("quote"), "question.quote")
            require(norm(quote) in norm(original), "问题锚点必须来自该段原文")
            require(question.get("origin") in {"model", "editor", "fixture"}, "每条问题需声明 origin")
            if "id" in question:
                qid = plain(question["id"], "question.id")
                require(qid not in question_ids, "question.id 重复")
                question_ids.add(qid)
            status = question.get("status", "open")
            require(status in {"open", "asked", "answered", "skipped", "uncertain"}, "question.status 无效")
            refs = question.get("answer_ids", [])
            require(isinstance(refs, list) and all(isinstance(r, str) and r in answer_map for r in refs), "question 回答引用无效")
            if status == "answered":
                require(refs and all(r in confirmed_answers for r in refs), "已答题必须关联已确认的用户回答")
                excerpt = norm(plain(question.get("answer_excerpt"), "question.answer_excerpt"))
                require(any(excerpt in norm(answer_map[r]) for r in refs), "已补充摘录必须来自关联用户回答的连续原话")
            else:
                require(not question.get("answer_excerpt"), "未确认的问题不能显示已补充事实")
        for quote in focus.get("strike_quotes", []):
            require(isinstance(quote, str) and len(norm(quote)) >= 2 and norm(quote) in norm(original), "删除线原句无效")
        for boundary in focus.get("break_after", []):
            require(isinstance(boundary, str) and boundary and rewrite.count(boundary) == 1, "break_after 必须唯一出现在改写中")
        clean.append(dict(focus, source_text=original))
    return doc, clean


def question_display(question):
    """Render the current state, never present an answered question as pending."""
    status = question.get("status", "open")
    if status == "answered":
        return "【已补充：" + question["answer_excerpt"] + "】"
    ask = question["ask"]
    if ask.startswith("【") and ask.endswith("】"):
        ask = ask[1:-1]
    prefix = {"skipped": "暂跳过：", "uncertain": "待核实："}.get(status, "")
    return "【" + prefix + ask + "】"


def question_heading(questions):
    if not questions:
        return "本段暂无待补充问题"
    if all(q.get("status") == "answered" for q in questions):
        return "本轮已补充的信息"
    if any(q.get("status", "open") not in {"open", "asked"} for q in questions):
        return "补充记录与待确认问题"
    return "继续追问，补出更有说服力的细节"


def diff_parts(original, rewrite, break_after=()):
    old, new = norm(original), norm(rewrite)
    red = set()
    for tag, _, _, a, b in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            red.update(range(a, b))
    breaks = {rewrite.index(s) + len(s) for s in break_after}
    parts, pos, bracket = [], 0, False
    for index, char in enumerate(rewrite, 1):
        bracket = bracket or char == "【"
        normalized = norm(char)
        changed = any(p in red for p in range(pos, pos + len(normalized)))
        parts.append((char, RED if bracket or changed else INK))
        pos += len(normalized)
        if char == "】":
            bracket = False
        if index in breaks:
            parts.append(("\n", INK))
    return parts


def line_layout(parts, font, width, size):
    chars = [(c, color) for text, color in parts for c in text]
    tokens = []
    for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9/+%.]*|[^A-Za-z0-9]", "".join(c for c, _ in chars)):
        token = chars[match.start():match.end()]
        if token[0][0] in "，。；：！？、）》】％%”」" and tokens:
            tokens[-1].extend(token)
        else:
            tokens.append(token)
    lines, current, used = [], [], 0
    for token in tokens:
        text = "".join(c for c, _ in token)
        if text == "\n":
            lines.append(current)
            current, used = [], 0
            continue
        length = font.text_length(text, fontsize=size)
        if length > width:
            raise LayoutError("单个词过长，请检查 URL 或无空格长词")
        if used + length > width and current:
            lines.append(current)
            current, used = [], 0
        if text == " " and not current:
            continue
        current.extend(token)
        used += length
    if current:
        lines.append(current)
    return lines


def fitted(source, box, max_scale=None):
    scale = min(box.width / source.width, box.height / source.height)
    if max_scale is not None:
        scale = min(scale, max_scale)
    return fitz.Rect(box.x0, box.y0, box.x0 + source.width * scale, box.y0 + source.height * scale)


def mapped(rect, source, target):
    scale = target.width / source.width
    return fitz.Rect(target.x0 + (rect.x0-source.x0)*scale, target.y0 + (rect.y0-source.y0)*scale,
                     target.x0 + (rect.x1-source.x0)*scale, target.y0 + (rect.y1-source.y0)*scale)


def section_layout(focus, top, font, limit):
    x, width = LAYOUT["right_x"], LAYOUT["right_width"]
    if font.text_length(focus["title"], fontsize=20) > width-38:
        raise LayoutError("段落标题过长，请压缩展示标题而非缩字")
    if font.text_length(focus["role_label"], fontsize=12) > width-38:
        raise LayoutError("岗位标签过长")
    clip = fitz.Rect(focus["bbox"])
    excerpt = fitted(clip, fitz.Rect(x, top+78, x+width, top+134), max_scale=2)
    if excerpt.width / clip.width < .8:
        raise LayoutError("原文摘录过高；请选择一个代表段落，不要把整段实习全部塞进摘录")
    rewrite_label = excerpt.y1 + 27
    rewrite_y = rewrite_label + 27
    lines = line_layout(diff_parts(focus["source_text"], focus["rewrite"], focus.get("break_after", [])),
                        font, width, LAYOUT["rewrite_size"])
    end = rewrite_y + max(0, len(lines)-1)*LAYOUT["rewrite_leading"]
    heading_y = end + 35
    questions, cursor = [], heading_y + 28
    for question in focus["questions"]:
        if font.text_length(question["title"], fontsize=13) > 80:
            raise LayoutError("追问短标题过长")
        ask = question_display(question)
        qlines = line_layout([(ask, RED)], font, width-112, LAYOUT["question_size"])
        questions.append((cursor, qlines))
        cursor += max(0, len(qlines)-1)*LAYOUT["question_leading"] + 24
    if cursor > limit:
        raise LayoutError("内容超过版面预算；拆分焦点或压缩追问，不得裁字")
    return {"top": top, "excerpt": excerpt, "rewrite_label": rewrite_label, "rewrite_y": rewrite_y,
            "lines": lines, "heading_y": heading_y, "questions": questions, "bottom": cursor}


def render_pdf(pdf, review, out, font_path=None):
    out = Path(out)
    require(out.suffix.lower() == ".pdf", "输出扩展名必须为 .pdf")
    require(Path(pdf).resolve() != out.resolve(), "不能覆盖源 PDF")
    source_hash = digest(pdf)
    src, focuses = validate_review(pdf, review)
    font, result, plans = font_for(font_path), fitz.open(), []
    for page_index in range(len(src)):
        selected = [f for f in focuses if f["page"] == page_index+1]
        if not selected:
            plans.append((page_index, [], []))
        while selected:
            group = selected[:2]
            try:
                sections = [section_layout(f, LAYOUT["section_tops"][i], font, 535 if i == 0 else 1005)
                            for i, f in enumerate(group)]
            except LayoutError:
                group = selected[:1]
                sections = [section_layout(group[0], LAYOUT["section_tops"][0], font, 1005)]
            plans.append((page_index, group, sections))
            selected = selected[len(group):]
    manifest = []
    for source_index, group, sections in plans:
        if not group:
            result.insert_pdf(src, from_page=source_index, to_page=source_index)
            manifest.append({"source_page": source_index+1, "focus_ids": [], "source_only": True})
            continue
        page = result.new_page(width=LAYOUT["page"][0], height=LAYOUT["page"][1])
        attach_font(page, font)

        def label(text, x, y, size=14, color=INK, max_width=None):
            if max_width is not None and font.text_length(text, fontsize=size) > max_width:
                raise LayoutError("标题或页脚过长，请缩短说明")
            for char in text:
                require(char.isspace() or font.has_glyph(ord(char)), "字体缺少字符；请用 --font 指定兼容字体")
            page.insert_text((x, y), text, fontname="ReviewCJK", fontsize=size, color=color)

        def draw_lines(lines, x, y, size, leading):
            for line in lines:
                cursor = x
                for char, color in line:
                    label(char, cursor, y, size, color)
                    cursor += font.text_length(char, fontsize=size)
                y += leading

        label("流水  /  简历精批", 32, 38, 14, GRAY)
        label(review.get("title", "一份完整简历，两段经历深入修改"), 32, 75, 26, max_width=620)
        label("目标：" + review["target_role"], 700, 43, 13, GRAY, 868)
        legend = ("黑字保留，红字改动；【】内为补充记录或待确认的信息"
                  if any(q.get("status") == "answered" for f in group for q in f["questions"])
                  else "黑字保留，红字改动；【】内为待补充或待确认的信息")
        label(legend, 700, 72, 13, GRAY)
        page.draw_line((32, 93), (1568, 93), color=RULE, width=.7)
        label("完整原页" if len(src) > 1 else "完整原简历", 32, 117, 13)
        label(f"保留原 PDF 版式 · 第 {source_index+1}/{len(src)} 页", 128, 117, 12, GRAY)
        left = fitted(src[source_index].rect, fitz.Rect(LAYOUT["left"]))
        page.show_pdf_page(left, src, source_index)
        page.draw_line((678, 112), (678, 1011), color=RULE, width=.7)
        for focus, section in zip(group, sections):
            index = focuses.index(focus)+1
            x, top = LAYOUT["right_x"], section["top"]
            label(f"{index:02d}", x, top+17, 16, RED)
            label(focus["title"], x+38, top+17, 20)
            label(focus["role_label"], x+38, top+40, 12, GRAY)
            label("原文摘录", x, top+66, 11, GRAY)
            clip = fitz.Rect(focus["bbox"])
            page.show_pdf_page(section["excerpt"], src, source_index, clip=clip)
            focus_rect = mapped(clip, src[source_index].rect, left)
            page.draw_rect(focus_rect, color=RED, width=.9)
            cy = (focus_rect.y0+focus_rect.y1)/2
            page.draw_circle((17, cy), 10.5, color=RED, fill=(1,1,1), width=.7)
            label(f"{index:02d}", 10.6, cy+3.6, 10, RED)
            page.insert_link({"kind": fitz.LINK_GOTO, "from": focus_rect, "page": len(result)-1,
                              "to": fitz.Point(x, top), "zoom": 1})
            for quote in focus.get("strike_quotes", []):
                for rect in src[source_index].search_for(quote):
                    if clip.contains(rect):
                        annotation = page.add_strikeout_annot(mapped(rect, clip, section["excerpt"]))
                        annotation.set_colors(stroke=RED)
                        annotation.set_info(title="建议删改", content=focus.get("reason", "对应改写见下方"))
                        annotation.update()
            for question in focus["questions"]:
                matches = [r for r in src[source_index].search_for(question["quote"]) if clip.contains(r)]
                require(len(matches) == 1, "问题锚点未唯一定位，请用同一行的精确原句")
                annotation = page.add_underline_annot(mapped(matches[0], clip, section["excerpt"]))
                annotation.set_colors(stroke=RED)
                comment = question.get("full", question["ask"])
                if question.get("status", "open") not in {"open", "asked"}:
                    comment = question_display(question) + "\n原提问：" + comment
                annotation.set_info(title=f"{question['title']} ({question['origin']})", content=comment)
                annotation.update()
            label("建议改写", x, section["rewrite_label"], 12)
            draw_lines(section["lines"], x, section["rewrite_y"], LAYOUT["rewrite_size"], LAYOUT["rewrite_leading"])
            label(question_heading(focus["questions"]), x, section["heading_y"], 12, GRAY)
            for i, (question, (y, lines)) in enumerate(zip(focus["questions"], section["questions"]), 1):
                label(f"{i:02d}  {question['title']}", x, y, 13)
                draw_lines(lines, x+112, y, LAYOUT["question_size"], LAYOUT["question_leading"])
        if len(group) == 2:
            page.draw_line((700, 547), (1568, 547), color=RULE, width=.7)
        page.draw_line((32, 1021), (1568, 1021), color=RULE, width=.7)
        label(review["privacy_note"], 32, 1041, 11, GRAY, 620)
        origins = "/".join(sorted({q["origin"] for f in group for q in f["questions"]})) or "无"
        label(f"改写来源：{review['provenance']['label']}；追问来源：{origins}。", 700, 1041, 11, GRAY, 868)
        require(norm(page.get_text(clip=left)) == norm(src[source_index].get_text()), "完整原页文字有缺失")
        manifest.append({"source_page": source_index+1, "focus_ids": [f["id"] for f in group],
                         "source_only": False, "left_rect": list(left),
                         "sections": [{"id": f["id"], "top": s["top"], "bottom": s["bottom"]} for f, s in zip(group, sections)]})
    result.set_metadata({"title": "简历精批展示", "author": "resume-fix"})
    result.del_xml_metadata()
    result.subset_fonts()
    data = result.tobytes(garbage=4, deflate=True)
    with fitz.open(stream=data, filetype="pdf") as check:
        all_text = "\n".join(p.get_text() for p in check)
        annotation_text = "\n".join(str(a.info) for p in check for a in (p.annots() or []))
        for focus in focuses:
            rendered = "\n".join(p.get_text(clip=fitz.Rect(700, 0, 1600, 1020)) for p in check if p.rect.width == 1600)
            require(norm(focus["rewrite"]) in norm(rendered), "渲染改写与结构化内容不一致")
        for token in review.get("forbidden_text", []):
            require(isinstance(token, str) and token, "forbidden_text 必须是非空字符串")
            require(norm(token).lower() not in norm(all_text + annotation_text + str(check.metadata)).lower(), "输出仍含禁止字段")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        for i, page in enumerate(check, 1):
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(out.with_name(f"{out.stem}-{i}.png"))
    require(source_hash == digest(pdf), "源文件发生变化")
    qa = {"version": 1, "layout_version": LAYOUT["version"], "source_sha256": source_hash,
          "review_sha256": hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
          "font": font.name, "font_sha256": hashlib.sha256(font.buffer).hexdigest(),
          "pymupdf_version": fitz.VersionBind, "source_unchanged": True,
          "full_original_pages_preserved": True, "rewrite_text_preserved": True,
          "original_retypeset": False, "numbers_checked": True, "page_count": len(manifest),
          "human_semantic_review_required": True, "visual_review_required": True,
          "question_states": [{"focus_id": f["id"], "id": q.get("id"),
                               "status": q.get("status", "open"), "answer_ids": q.get("answer_ids", [])}
                              for f in focuses for q in f["questions"]],
          "provenance": review["provenance"], "pages": manifest}
    save_json(out.with_suffix(".qa.json"), qa)
    return {"pdf": str(out), "page_count": len(manifest), "qa": str(out.with_suffix(".qa.json"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "sanitize", "render", "validate"):
        p = sub.add_parser(command)
        p.add_argument("--pdf", required=True, type=Path)
        if command != "validate":
            p.add_argument("--out", required=True, type=Path)
        if command in ("render", "validate"):
            p.add_argument("--review", required=True, type=Path)
        if command == "sanitize":
            p.add_argument("--privacy", required=True, type=Path)
        if command in ("render", "sanitize"):
            p.add_argument("--font", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = inspect_pdf(args.pdf, args.out)
        elif args.command == "sanitize":
            result = sanitize_pdf(args.pdf, load(args.privacy), args.out, args.font)
        elif args.command == "render":
            result = render_pdf(args.pdf, load(args.review), args.out, args.font)
        else:
            doc, focus = validate_review(args.pdf, load(args.review))
            result = {"valid": True, "focus_count": len(focus), "human_semantic_review_required": True}
            doc.close()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ReviewError, ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        print(f"resume-fix: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
