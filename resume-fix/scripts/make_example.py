"""Generate a synthetic acceptance example, never a claimed model run."""
import argparse
from pathlib import Path
import pymupdf as fitz
import resume_fix as rf


def make_example(out, render=True, followup=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fixture = (rf.ROOT / "tests/fixtures/resume-pm-recsys.txt").read_text(encoding="utf-8")
    # These contacts are intentionally fake fixture data; remove them in the
    # distributable example to make its demonstration privacy state explicit.
    fixture = fixture.replace("13800000000", "[已隐藏]").replace("liX@example.com", "[已隐藏]")
    doc, font = fitz.open(), rf.font_for()
    page = doc.new_page(width=595.32, height=841.92)
    rf.attach_font(page, font)
    page.insert_text((38, 39), "合成测试简历（非真实候选人）", fontname="ReviewCJK", fontsize=13)
    y, candidates = 72, []
    for text in fixture.splitlines():
        if not text:
            y += 13
            continue
        top = y - 11
        lines = rf.line_layout([(text, rf.INK)], font, 520, 10)
        for line in lines:
            page.insert_text((38, y), "".join(c for c, _ in line), fontname="ReviewCJK", fontsize=10)
            y += 17
        if "梳理 AB 实验" in text or "用 SQL 拉取" in text:
            candidates.append([36, top-1, 562, y-12])
    assert y < 800
    doc.subset_fonts()
    source = out / "synthetic-source.pdf"
    doc.save(source, garbage=4, deflate=True)
    with fitz.open(source) as src:
        originals = [src[0].get_text(clip=fitz.Rect(b)).strip() for b in candidates]
    rewrites = [
        "围绕首页猜你喜欢开展策略迭代，对接算法团队需求，梳理 12 组 AB 实验方案。",
        "使用 SQL 拉取留存数据，形成新用户 7 日留存归因分析。",
    ]
    titles = ["推荐策略：让实验体现产品判断", "留存分析：从数据动作到业务结论"]
    roles = ["某电商平台 · 推荐策略产品实习生", "某内容社区 · 用户增长产品实习生"]
    anchors = ["AB 实验", "留存数据"]
    questions = [
        [
            ("问题来源", "这次策略迭代针对什么用户问题，为什么优先处理它？"),
            ("方案取舍", "实验比较了哪些方案？你决定了哪个变量，为什么这样选择？"),
            ("结果验证", "用哪个指标判断实验？观察期与结论是什么，是否有反向指标？"),
            ("个人交付", "哪些实验设计由你完成，哪些由算法同事负责？能展示什么脱敏证据？"),
        ],
        [
            ("分析目标", "关注哪类新用户、什么流失问题？为什么选择这一观察口径？"),
            ("分析判断", "哪些数据支持归因结论？还考虑过什么解释，如何排除？"),
            ("业务应用", "结论用于哪个决策？如果尚未落地，就保留分析产出，不编业务提升。"),
            ("验证边界", "后续用什么办法核对结论？你个人负责数据提取、分析还是建议？"),
        ],
    ]
    review = {"version": 1, "pdf_sha256": rf.digest(source), "target_role": "推荐策略产品经理",
              "title": "合成验收样例：双段简历精批", "privacy_note": "合成测试材料；联系方式已隐藏，非真实候选人。",
              "provenance": {"kind": "fixture", "label": "手造验收示例，非模型实跑"},
              "answers": [], "forbidden_text": ["13800000000", "liX@example.com"], "focuses": []}
    for i in range(2):
        review["focuses"].append({"id": f"F{i+1}", "page": 1, "bbox": candidates[i],
            "title": titles[i], "role_label": roles[i], "source_text": originals[i], "rewrite": rewrites[i],
            "evidence": [{"claim": rewrites[i], "quote": originals[i], "answer_ids": []}],
            "strike_quotes": [], "break_after": [],
            "questions": [{"id": f"F{i+1}-Q{j}", "title": title, "ask": ask, "quote": anchors[i], "origin": "fixture"}
                          for j, (title, ask) in enumerate(questions[i], 1)]})
    if followup:
        # Simulated user turns for renderer acceptance only, not real answers.
        review["provenance"]["label"] = "手造问答验收，非模型实跑"
        review["answers"] = [
            {"id": "a1", "origin": "user", "status": "confirmed", "text": "本次迭代针对首页推荐内容重复的问题。"},
            {"id": "a2", "origin": "user", "status": "uncertain", "text": "记不清效果数字，目前没有可核对的数据。"},
        ]
        focus = review["focuses"][0]
        added = "迭代针对首页推荐内容重复的问题。"
        focus["rewrite"] += added
        focus["evidence"].append({"claim": added, "quote": "", "answer_ids": ["a1"]})
        focus["questions"][0].update(status="answered", answer_ids=["a1"], answer_excerpt=review["answers"][0]["text"])
        focus["questions"][1].update(status="skipped")
        focus["questions"][2].update(status="uncertain", answer_ids=["a2"])
    rf.save_json(out / "review.json", review)
    if render:
        rf.inspect_pdf(source, out / "inspection")
        rf.render_pdf(source, review, out / "example-review.pdf")
    return source, review


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--followup", action="store_true", help="Render synthetic answered / skipped / uncertain states")
    args = parser.parse_args()
    source, review = make_example(args.out, followup=args.followup)
    print(args.out / "example-review.pdf")
