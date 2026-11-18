#!/usr/bin/env python3
"""Render an annotated reference-request email to self-contained HTML and TXT."""

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


KINDS = {
    "applicant-guide": "申请者指南",
    "recommender-guide": "推荐信写作指南",
    "sample-letter": "推荐信样例",
}


def esc(value):
    return html.escape(str(value), quote=True)


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value must be an object")
    require_text(data.get("title"), "title")
    for optional in ("subtitle", "notice"):
        if optional in data and not isinstance(data[optional], str):
            raise ValueError(f"{optional} must be a string")
    email = data.get("email")
    if not isinstance(email, dict):
        raise ValueError("email must be an object")
    require_text(email.get("subject"), "email.subject")
    require_text(email.get("greeting"), "email.greeting")
    require_text(email.get("closing"), "email.closing")
    paragraphs = email.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs or any(
        not isinstance(p, str) or not p.strip() for p in paragraphs
    ):
        raise ValueError("email.paragraphs must be a non-empty array of non-empty strings")

    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("sources must be an array")
    source_map = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each source must be an object")
        sid = require_text(source.get("id"), "source.id")
        if sid in source_map:
            raise ValueError(f"duplicate source id: {sid}")
        require_text(source.get("title"), f"source {sid}.title")
        require_text(source.get("url"), f"source {sid}.url")
        parts = urlsplit(source["url"])
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"source {sid}.url must be an http(s) URL")
        if source.get("kind") not in KINDS:
            raise ValueError(f"source {sid}.kind must be one of: {', '.join(KINDS)}")
        if source.get("status") not in {"verified", "unverified"}:
            raise ValueError(f"source {sid}.status must be verified or unverified")
        if source["status"] == "verified":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(source.get("accessed_on", ""))):
                raise ValueError(f"verified source {sid} needs accessed_on in YYYY-MM-DD format")
            require_text(source.get("excerpt"), f"verified source {sid}.excerpt")
        source_map[sid] = source

    notes = data.get("notes", [])
    if not isinstance(notes, list):
        raise ValueError("notes must be an array")
    seen_ids = set()
    by_paragraph = {}
    for note in notes:
        if not isinstance(note, dict):
            raise ValueError("each note must be an object")
        number = note.get("id")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError("each note.id must be a positive integer")
        if number in seen_ids:
            raise ValueError(f"duplicate note id: {number}")
        seen_ids.add(number)
        paragraph = note.get("paragraph")
        if not isinstance(paragraph, int) or isinstance(paragraph, bool) or not 0 <= paragraph < len(paragraphs):
            raise ValueError(f"note {number}.paragraph is outside email.paragraphs")
        quote = require_text(note.get("quote"), f"note {number}.quote")
        if paragraphs[paragraph].count(quote) != 1:
            raise ValueError(f"note {number}.quote must occur exactly once in its paragraph")
        require_text(note.get("title"), f"note {number}.title")
        require_text(note.get("comment"), f"note {number}.comment")
        citations = note.get("citations", [])
        if not isinstance(citations, list):
            raise ValueError(f"note {number}.citations must be an array")
        for citation in citations:
            if not isinstance(citation, dict):
                raise ValueError(f"note {number} citations must be objects")
            sid = citation.get("source_id")
            if sid not in source_map:
                raise ValueError(f"note {number} cites unknown source {sid!r}")
            require_text(citation.get("support"), f"note {number} citation support")
        by_paragraph.setdefault(paragraph, []).append((quote, number))

    for paragraph, ranges in by_paragraph.items():
        text = paragraphs[paragraph]
        locations = sorted((text.index(quote), text.index(quote) + len(quote), number) for quote, number in ranges)
        for previous, current in zip(locations, locations[1:]):
            if current[0] < previous[1]:
                raise ValueError(f"notes {previous[2]} and {current[2]} overlap in paragraph {paragraph}")
    checks = data.get("checks", [])
    if not isinstance(checks, list) or any(not isinstance(x, str) for x in checks):
        raise ValueError("checks must be an array of strings")
    return email, paragraphs, notes, source_map, checks


def render_paragraph(text, paragraph_index, notes):
    matches = sorted(
        ((text.index(note["quote"]), text.index(note["quote"]) + len(note["quote"]), note["id"])
         for note in notes if note["paragraph"] == paragraph_index),
        key=lambda item: item[0],
    )
    out = []
    cursor = 0
    for start, end, number in matches:
        out.append(esc(text[cursor:start]))
        out.append(f'<mark id="mark-{number}">{esc(text[start:end])}</mark>')
        out.append(f'<sup class="marker"><a href="#note-{number}" aria-label="批注 {number}">{number}</a></sup>')
        cursor = end
    out.append(esc(text[cursor:]))
    return "".join(out)


def render_citations(note, sources):
    citations = note.get("citations", [])
    if not citations:
        return '<p class="editor-note">编辑建议</p>'
    rows = []
    for citation in citations:
        source = sources[citation["source_id"]]
        verified = source["status"] == "verified"
        state = "已核实" if verified else "来源待核实"
        row = (
            '<div class="citation">'
            f'<a href="{esc(source["url"])}" rel="noreferrer">{esc(source["title"])}</a>'
            f'<span class="source-kind">{esc(KINDS[source["kind"]])} · {state}</span>'
            f'<p>{esc(citation["support"])}</p>'
        )
        if verified:
            row += (
                '<details><summary>核实摘录 · ' + esc(source["accessed_on"]) + '</summary>'
                f'<blockquote>{esc(source["excerpt"])}</blockquote></details>'
            )
        rows.append(row + '</div>')
    return "".join(rows)


def build_html(data, email, paragraphs, notes, sources, checks):
    paragraphs_html = "\n".join(
        f'<p>{render_paragraph(text, index, notes)}</p>'
        for index, text in enumerate(paragraphs)
    )
    cards = []
    for note in notes:
        cards.append(
            f'<article class="note-card" id="note-{note["id"]}">'
            f'<div class="note-head"><span>{note["id"]}</span><h3>{esc(note["title"])}</h3></div>'
            f'<p>{esc(note["comment"])}</p>{render_citations(note, sources)}'
            f'<a class="back" href="#mark-{note["id"]}">回到正文 ↑</a></article>'
        )
    checks_html = ""
    if checks:
        checks_html = '<section class="checks"><h2>发送前核对</h2><ul>' + "".join(
            f"<li>{esc(item)}</li>" for item in checks
        ) + "</ul></section>"
    source_rows = []
    for source in sources.values():
        status = "已核实" if source["status"] == "verified" else "待核实"
        source_rows.append(
            f'<li><a href="{esc(source["url"])}" rel="noreferrer">{esc(source["title"])}</a>'
            f' — {esc(KINDS[source["kind"]])}，{status}</li>'
        )
    source_html = '<section class="source-list"><h2>来源</h2><ul>' + "".join(source_rows) + '</ul></section>' if source_rows else ""
    subtitle = f'<p class="subtitle">{esc(data["subtitle"])}</p>' if data.get("subtitle") else ""
    notice = f'<aside class="notice">{esc(data["notice"])}</aside>' if data.get("notice") else ""
    closing = esc(email["closing"]).replace("\n", "<br>")
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(data["title"])}</title>
<style>
:root{{--ink:#17243b;--muted:#61708a;--line:#dce4f0;--paper:#fff;--canvas:#edf2f8;--blue:#2d64e6;--blue-soft:#e9efff;--green:#287e69;--gold:#a8741d;--shadow:0 18px 55px #17243b18}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 4%,#2d64e610,transparent 28%),var(--canvas);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{width:min(1420px,calc(100% - 44px));margin:auto;padding:38px 0 54px}}header{{margin-bottom:22px}}.kicker{{color:var(--blue);font-size:12px;font-weight:700;letter-spacing:.08em}}h1{{font:700 clamp(30px,4vw,48px)/1.15 "Songti SC","Noto Serif CJK SC",serif;margin:7px 0}}.subtitle{{color:var(--muted);margin:0}}.notice{{margin-top:15px;padding:12px 15px;border-left:4px solid var(--gold);background:#fff6df;border-radius:8px}}
.grid{{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(330px,.8fr);gap:22px;align-items:start}}.paper,.side,.checks,.source-list{{background:var(--paper);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow)}}.paper{{padding:32px 38px}}.paper-head{{padding-bottom:18px;margin-bottom:20px;border-bottom:1px solid var(--line)}}.label{{color:var(--muted);font-size:11px;letter-spacing:.1em}}.subject{{font:700 21px/1.45 "Songti SC","Noto Serif CJK SC",serif;margin-top:5px}}.greeting,.body,.closing{{font:17px/1.95 "Songti SC","Noto Serif CJK SC",serif}}.greeting{{margin:0 0 18px}}.body p{{margin:0 0 18px}}.closing{{margin-top:26px}}mark{{background:var(--blue-soft);color:inherit;border-bottom:2px solid var(--blue);padding:0 2px}}.marker{{font:700 10px/1 sans-serif;vertical-align:super;margin-left:3px}}.marker a{{display:inline-block;color:white;background:var(--blue);text-decoration:none;border-radius:50%;width:19px;height:19px;text-align:center;line-height:19px}}
.side{{padding:23px;position:sticky;top:16px}}.side h2,.checks h2,.source-list h2{{font:700 22px/1.3 "Songti SC","Noto Serif CJK SC",serif;margin:0 0 14px}}.note-card{{padding:15px 16px;margin:12px 0;background:#f7f9fd;border-left:4px solid var(--blue);border-radius:12px;scroll-margin-top:18px}}.note-head{{display:flex;align-items:center;gap:9px}}.note-head span{{flex:none;background:var(--blue);color:white;border-radius:50%;width:23px;height:23px;text-align:center;font:700 12px/23px sans-serif}}.note-head h3{{font-size:15px;margin:0}}.note-card>p{{margin:8px 0;color:#37455c;font-size:13px}}.citation{{padding-top:8px;border-top:1px solid var(--line);font-size:12px;margin-top:8px}}.citation a,.source-list a{{color:#1d55c2;text-underline-offset:2px}}.source-kind{{display:block;color:var(--muted);font-size:10px}}.citation p{{margin:4px 0}}.citation details{{color:var(--muted)}}blockquote{{margin:6px 0;padding-left:10px;border-left:2px solid var(--line)}}.editor-note{{color:var(--green);font-size:11px!important}}.back{{font-size:11px;color:var(--blue)}}.checks,.source-list{{padding:20px 24px;margin-top:18px}}ul{{padding-left:20px}}.checks li,.source-list li{{margin:5px 0}}.source-list{{font-size:12px}}.source-list h2{{font-size:18px}}footer{{margin-top:18px;color:var(--muted);font-size:11px}}
@media(max-width:950px){{.grid{{grid-template-columns:1fr}}.side{{position:static}}}}@media(max-width:600px){{.wrap{{width:calc(100% - 20px);padding:22px 0}}.paper{{padding:23px 20px}}.side{{padding:18px}}.body p,.greeting,.closing{{font-size:16px}}}}
@media print{{body{{background:#fff}}.wrap{{width:auto;padding:0}}.paper,.side,.checks,.source-list{{box-shadow:none}}.grid{{grid-template-columns:minmax(0,1.4fr) minmax(290px,.8fr);gap:12px}}.paper{{padding:20px}}.side{{position:static;padding:16px}}.note-card{{break-inside:avoid}}}}
</style></head><body><main class="wrap"><header><div class="kicker">推荐信请求邮件 · 内容批注</div><h1>{esc(data["title"])}</h1>{subtitle}{notice}</header>
<div class="grid"><article class="paper"><div class="paper-head"><div class="label">EMAIL DRAFT</div><div class="subject">{esc(email["subject"])}</div></div><p class="greeting">{esc(email["greeting"])}</p><div class="body">{paragraphs_html}</div><div class="closing">{closing}</div></article>
<aside class="side"><h2>批注与依据</h2>{''.join(cards) if cards else '<p>当前邮件没有附加批注。</p>'}</aside></div>{checks_html}{source_html}<footer>来源标记说明：已核实表示引用内容已人工核对原文；待核实链接仅供后续查证。批注中的编辑建议不代表院校要求。</footer></main></body></html>'''


def build_text(data, email, paragraphs):
    notice = (data.get("notice", "") + "\n\n") if data.get("notice") else ""
    body = "\n\n".join([email["greeting"], *paragraphs, email["closing"]])
    return f"{notice}邮件标题\n{email['subject']}\n\n邮件正文\n{body}\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 annotation JSON")
    parser.add_argument("--output", required=True, type=Path, help="HTML output path; a sibling TXT is also written")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        email, paragraphs, notes, sources, checks = validate(data)
        html_content = build_html(data, email, paragraphs, notes, sources, checks)
        text_content = build_text(data, email, paragraphs)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html_content, encoding="utf-8")
        args.output.with_suffix(".txt").write_text(text_content, encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        parser.error(str(error))
    print(f"HTML: {args.output}")
    print(f"TXT:  {args.output.with_suffix('.txt')}")


if __name__ == "__main__":
    main()
