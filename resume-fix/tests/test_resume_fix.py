import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pymupdf as fitz
import resume_fix as rf
import package_skill
from make_example import make_example


class ResumeFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf, self.review = make_example(self.root, render=False)

    def tearDown(self):
        self.tmp.cleanup()

    def invalid(self, mutate):
        review = copy.deepcopy(self.review)
        mutate(review)
        with self.assertRaises(rf.ReviewError):
            rf.validate_review(self.pdf, review)

    def test_hash_binding(self):
        self.invalid(lambda r: r.update(pdf_sha256="wrong"))

    def test_bad_source_quote(self):
        self.invalid(lambda r: r["focuses"][0].update(source_text="不存在的经历"))

    def test_bbox_out_of_page(self):
        self.invalid(lambda r: r["focuses"][0].update(bbox=[0, 0, 900, 900]))

    def test_nonfinite_coordinates(self):
        self.invalid(lambda r: r["focuses"][0].update(bbox=[0, float("nan"), 100, 120]))

    def test_unrelated_question(self):
        self.invalid(lambda r: r["focuses"][0]["questions"][0].update(quote="不存在的用户增长经历"))

    def test_invented_number(self):
        def change(r):
            f = r["focuses"][0]
            f["rewrite"] = f["rewrite"].replace("12", "300")
            f["evidence"][0]["claim"] = f["rewrite"]
        self.invalid(change)

    def test_number_unit_boundary(self):
        self.assertNotEqual(rf.numeric_tokens("2000人"), rf.numeric_tokens("2000人次"))
        self.assertEqual(rf.numeric_tokens("2.4 万人"), rf.numeric_tokens("2.4万人"))

    def test_cet_score_whitespace_boundary(self):
        self.assertEqual(rf.numeric_tokens("CET-6 509"), {"6", "509"})
        self.assertNotIn("6509", rf.numeric_tokens("CET-6 509"))

    def test_missing_fact_evidence(self):
        self.invalid(lambda r: r["focuses"][0].update(evidence=[]))

    def test_whitespace_is_not_fact_evidence(self):
        self.invalid(lambda r: r["focuses"][0]["evidence"][0].update(quote=" \n "))

    def test_evidence_claim_not_in_rewrite(self):
        self.invalid(lambda r: r["focuses"][0]["evidence"][0].update(claim="成功实现跨区域部署"))

    def test_new_personal_ownership(self):
        def change(r):
            f = r["focuses"][0]
            f["rewrite"] = "独立" + f["rewrite"]
            f["evidence"][0]["claim"] = f["rewrite"]
        self.invalid(change)

    def test_prelaunch_cannot_be_claimed_as_launched(self):
        pending = self.root / "pending.pdf"
        with fitz.open() as doc:
            page = doc.new_page()
            rf.attach_font(page, rf.font_for())
            page.insert_text((40, 60), "完成上线前问题修复，预计月底上线。", fontname="ReviewCJK", fontsize=10)
            doc.save(pending)
        r = copy.deepcopy(self.review)
        r["pdf_sha256"] = rf.digest(pending)
        f = r["focuses"][0]
        r["focuses"] = [f]
        f.update(bbox=[38, 45, 400, 65], source_text="完成上线前问题修复，预计月底上线。",
                 rewrite="完成问题修复，推动上线。", questions=[
                     {"title": "上线边界", "quote": "预计月底上线", "ask": "目前进度是什么？", "origin": "fixture"}])
        f["evidence"] = [{"claim": f["rewrite"], "quote": f["source_text"], "answer_ids": []}]
        with self.assertRaisesRegex(rf.ReviewError, "上线前"):
            rf.validate_review(pending, r)
        r["answers"] = [{"id": "a1", "origin": "user", "text": "本人确认：后续已完成问题修复并推动上线。"}]
        f["evidence"][0]["answer_ids"] = ["a1"]
        doc, _ = rf.validate_review(pending, r)
        doc.close()

    def test_user_answers_are_explicit_and_scoped(self):
        r = copy.deepcopy(self.review)
        r["answers"] = [{"id": "a1", "origin": "user", "text": "本人确认：共梳理 15 组 AB 实验方案。"}]
        f = r["focuses"][0]
        f["rewrite"] = f["rewrite"].replace("12", "15")
        f["evidence"][0].update(claim=f["rewrite"], answer_ids=["a1"])
        doc, _ = rf.validate_review(self.pdf, r)
        doc.close()
        f["evidence"][0]["answer_ids"] = []
        with self.assertRaises(rf.ReviewError):
            rf.validate_review(self.pdf, r)

    def test_model_questions_not_accepted_as_answers(self):
        self.invalid(lambda r: r.update(answers=[{"id": "a1", "origin": "model", "text": "达到300了吗"}]))

    def test_unconfirmed_or_superseded_answers_cannot_support_rewrite(self):
        for state in ("uncertain", "superseded"):
            with self.subTest(state=state):
                r = copy.deepcopy(self.review)
                r["answers"] = [{"id": "a1", "origin": "user", "status": state, "text": "梳理了15组实验。"}]
                f = r["focuses"][0]
                f["rewrite"] = f["rewrite"].replace("12", "15")
                f["evidence"][0].update(claim=f["rewrite"], answer_ids=["a1"])
                with self.assertRaisesRegex(rf.ReviewError, "未确认或已更正"):
                    rf.validate_review(self.pdf, r)

    def test_answered_question_requires_confirmed_user_answer(self):
        self.invalid(lambda r: r["focuses"][0]["questions"][0].update(status="answered", answer_excerpt="随意补充"))
        source, r = make_example(self.root / "followup", render=False, followup=True)
        r["answers"][0]["status"] = "uncertain"
        r["focuses"][0]["evidence"][-1]["answer_ids"] = []
        r["focuses"][0]["rewrite"] = self.review["focuses"][0]["rewrite"]
        r["focuses"][0]["evidence"] = self.review["focuses"][0]["evidence"]
        with self.assertRaisesRegex(rf.ReviewError, "已答题"):
            rf.validate_review(source, r)

    def test_answer_excerpt_cannot_invent_or_use_another_answer(self):
        source, r = make_example(self.root / "followup", render=False, followup=True)
        for excerpt in ("提升了300%", r["answers"][1]["text"]):
            with self.subTest(excerpt=excerpt):
                r["focuses"][0]["questions"][0]["answer_excerpt"] = excerpt
                with self.assertRaisesRegex(rf.ReviewError, "连续原话"):
                    rf.validate_review(source, r)

    def test_invalid_conversation_states_and_duplicate_ids(self):
        self.invalid(lambda r: r["focuses"][0]["questions"][0].update(status="invented"))
        self.invalid(lambda r: r["focuses"][0]["questions"][0].update(answer_excerpt="未确认事实"))
        self.invalid(lambda r: r["focuses"][1]["questions"][0].update(id="F1-Q1"))

    def test_followup_renders_answer_instead_of_reasking(self):
        source, r = make_example(self.root / "followup", render=False, followup=True)
        out = self.root / "followup.pdf"
        rf.render_pdf(source, r, out)
        with fitz.open(out) as doc:
            text = rf.norm("".join(p.get_text() for p in doc))
            question = r["focuses"][0]["questions"][0]
            self.assertIn(rf.norm("已补充：" + question["answer_excerpt"]), text)
            self.assertNotIn(rf.norm(question["ask"]), text)
            self.assertIn(rf.norm("暂跳过："), text)
            self.assertIn(rf.norm("待核实："), text)
            self.assertIn("补充记录或待确认的信息", text)
            comments = "".join(str(a.info) for p in doc for a in (p.annots() or []))
            self.assertIn(question["ask"], comments)
        qa = json.loads(out.with_suffix(".qa.json").read_text())
        self.assertEqual(qa["question_states"][0]["status"], "answered")
        self.assertEqual(qa["question_states"][0]["answer_ids"], ["a1"])

    def test_all_answered_does_not_claim_more_questions_needed(self):
        source, r = make_example(self.root / "followup", render=False, followup=True)
        r["focuses"][0]["questions"] = r["focuses"][0]["questions"][:1]
        r["focuses"][1]["questions"] = []
        out = self.root / "done.pdf"
        rf.render_pdf(source, r, out)
        with fitz.open(out) as doc:
            text = rf.norm(doc[0].get_text())
            self.assertIn("本轮已补充的信息", text)
            self.assertIn("本段暂无待补充问题", text)
            self.assertNotIn("继续追问", text)

    def test_markdown_not_printed_literal(self):
        self.invalid(lambda r: r["focuses"][0].update(rewrite="## 工作经历\n**改写**"))

    def test_selective_red_and_bracket_red(self):
        parts = rf.diff_parts("负责用户分析", "开展用户分析【建议：补充依据】")
        self.assertIn(("用", rf.INK), parts)
        self.assertIn(("开", rf.RED), parts)
        start = next(i for i, (c, _) in enumerate(parts) if c == "【")
        self.assertTrue(all(color == rf.RED for _, color in parts[start:]))

    def test_semantic_breaks_preserve_characters(self):
        text = self.review["focuses"][0]["rewrite"]
        parts = rf.diff_parts("", text, ["迭代，"])
        self.assertEqual(rf.norm("".join(c for c, _ in parts)), rf.norm(text))
        self.assertIn(("\n", rf.INK), parts)

    def test_source_unchanged_and_full_page_preserved(self):
        before = rf.digest(self.pdf)
        out = self.root / "result.pdf"
        rf.render_pdf(self.pdf, self.review, out)
        self.assertEqual(before, rf.digest(self.pdf))
        qa = json.loads(out.with_suffix(".qa.json").read_text())
        self.assertTrue(qa["full_original_pages_preserved"])
        self.assertTrue(qa["human_semantic_review_required"])
        self.assertEqual(qa["page_count"], 1)
        with fitz.open(out) as doc:
            self.assertEqual(len(doc[0].get_links()), 2)
            self.assertEqual(len(list(doc[0].annots())), 8)
            self.assertFalse(doc[0].get_image_info())
        self.assertTrue(out.with_name("result-1.png").is_file())

    def test_no_source_overwrite(self):
        with self.assertRaises(rf.ReviewError):
            rf.render_pdf(self.pdf, self.review, self.pdf)

    def test_long_questions_paginate_without_truncation(self):
        r = copy.deepcopy(self.review)
        for f in r["focuses"]:
            for q in f["questions"]:
                q["ask"] *= 5
        out = self.root / "long.pdf"
        rf.render_pdf(self.pdf, r, out)
        qa = json.loads(out.with_suffix(".qa.json").read_text())
        self.assertEqual(qa["page_count"], 2)
        self.assertEqual([len(p["focus_ids"]) for p in qa["pages"]], [1, 1])

    def test_impossible_content_fails_before_output(self):
        r = copy.deepcopy(self.review)
        r["focuses"][0]["questions"][0]["ask"] *= 500
        out = self.root / "overflow.pdf"
        with self.assertRaises(rf.LayoutError):
            rf.render_pdf(self.pdf, r, out)
        self.assertFalse(out.exists())

    def test_extra_source_page_not_dropped(self):
        multi = self.root / "multi.pdf"
        with fitz.open(self.pdf) as doc:
            doc.new_page()
            doc[-1].insert_text((40, 60), "Appendix - synthetic page")
            doc.save(multi)
        r = copy.deepcopy(self.review)
        r["pdf_sha256"] = rf.digest(multi)
        out = self.root / "multi-review.pdf"
        rf.render_pdf(multi, r, out)
        with fitz.open(out) as doc:
            self.assertEqual(len(doc), 2)
            self.assertIn("Appendix", doc[1].get_text())

    def test_sanitize_removes_text_and_links(self):
        privacy = {"authorized": True, "redactions": [{"page": 1, "quote": "李某", "replacement": "匿名", "font_size": 8}],
                   "forbidden_text": ["李某"]}
        safe = self.root / "safe.pdf"
        rf.sanitize_pdf(self.pdf, privacy, safe)
        with fitz.open(safe) as doc:
            self.assertNotIn("李某", doc[0].get_text())
            self.assertIn("匿名", doc[0].get_text())
            self.assertFalse(doc[0].get_links())
        with fitz.open(self.pdf) as doc:
            self.assertIn("李某", doc[0].get_text())

    def test_sanitize_requires_authorization(self):
        with self.assertRaises(rf.ReviewError):
            rf.sanitize_pdf(self.pdf, {"redactions": []}, self.root / "safe.pdf")

    def test_sanitize_existing_font_name_and_hidden_metadata(self):
        source = self.root / "private.pdf"
        with fitz.open(self.pdf) as doc:
            rf.attach_font(doc[0], rf.font_for(), "PrivacyCJK1")
            doc[0].insert_text((40, 740), "字形子集", fontname="PrivacyCJK1", fontsize=10)
            doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(40, 50, 100, 70),
                                "uri": "https://example.com/private"})
            doc[0].add_text_annot((40, 760), "EXAMPLE_PRIVATE_TOKEN")
            doc.embfile_add("private.txt", b"EXAMPLE_PRIVATE_TOKEN")
            doc.set_metadata({"author": "EXAMPLE_PRIVATE_TOKEN"})
            doc.set_xml_metadata("<private>EXAMPLE_PRIVATE_TOKEN</private>")
            doc.set_toc([[1, "EXAMPLE_PRIVATE_TOKEN", 1]])
            doc.subset_fonts()
            doc.save(source)
        safe = self.root / "clean.pdf"
        rf.sanitize_pdf(source, {"authorized": True, "redactions": [
            {"page": 1, "quote": "李某", "replacement": "匿名", "font_size": 8}],
            "forbidden_text": ["李某", "EXAMPLE_PRIVATE_TOKEN"]}, safe)
        with fitz.open(safe) as doc:
            self.assertIn("匿名", doc[0].get_text())
            self.assertFalse(doc[0].get_links())
            self.assertFalse(list(doc[0].annots() or []))
            self.assertFalse(doc.embfile_names())
            self.assertFalse(doc.get_toc())
            self.assertFalse(doc.get_xml_metadata())
            self.assertNotIn("EXAMPLE_PRIVATE_TOKEN", str(doc.metadata))

    def test_forbidden_text_in_final_comments_is_rejected(self):
        r = copy.deepcopy(self.review)
        r["forbidden_text"] = ["EXAMPLE_PRIVATE_TOKEN"]
        r["focuses"][0]["questions"][0]["full"] = "EXAMPLE_PRIVATE_TOKEN"
        out = self.root / "bad.pdf"
        with self.assertRaises(rf.ReviewError):
            rf.render_pdf(self.pdf, r, out)
        self.assertFalse(out.exists())

    def test_package_is_allowlisted_and_hashes_verify(self):
        isolated = self.root / "package-input"
        shutil.copytree(rf.ROOT, isolated)
        (isolated / ".env").write_text("EXAMPLE_PRIVATE_TOKEN", encoding="utf-8")
        (isolated / "candidate.pdf").write_bytes(b"private fixture, not a PDF")
        out = self.root / "skill.zip"
        with patch.object(package_skill, "ROOT", isolated):
            package_skill.package(out)
        with zipfile.ZipFile(out) as archive:
            manifest = json.loads(archive.read("resume-fix/PACKAGE-MANIFEST.json"))
            self.assertNotIn("resume-fix/.env", archive.namelist())
            self.assertNotIn("resume-fix/candidate.pdf", archive.namelist())
            self.assertFalse(any("__pycache__" in name for name in archive.namelist()))
            self.assertEqual(len(archive.namelist()), len(manifest["sha256"]) + 1)
            for name, expected in manifest["sha256"].items():
                self.assertEqual(hashlib.sha256(archive.read("resume-fix/" + name)).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
