# 批注数据与渲染

使用 UTF-8 JSON，完整示例见 `../assets/example.json`。字符串均为纯文本，不传 HTML。渲染器会转义正文、批注与链接标签，输出同名 `.html` 和 `.txt`。需要 HTML 单独交付时仍可保留 TXT 方便复制正文。

## 字段

- `title`：页面标题；`subtitle`：可选说明，不放未经核实的学校数量。
- `notice`：页面可见的材料说明。有虚构演示、待核实事实时说明范围。
- `email.subject`、`email.greeting`：邮件主题与称呼。
- `email.paragraphs`：自然段字符串数组；以原始文字为准。
- `email.closing`：结语与签名，换行用 `\n`。
- `notes`：批注数组，每项包含 `id`（正整数）、`paragraph`（从 0 计）、`quote`（该段原文精确片段）、`title`、`comment`、`citations`。
- `citations`：数组，每项 `{ "source_id": "columbia", "support": "该来源具体支持的建议" }`。纯编辑建议用空数组。
- `sources`：数组，包含 `id`、`title`、`url`、`kind`、`status`。`kind` 为 `applicant-guide`（申请者指南）、`recommender-guide`（推荐信写作指南）或 `sample-letter`（推荐信样例）；`status` 为 `verified` 或 `unverified`。
- 已核实来源还需 `accessed_on`（实际访问日，YYYY-MM-DD）和 `excerpt`（与批注有关的原文短摘录）。未核实不填写虚假日期／摘录。
- `checks`：可选字符串数组，显示发送前真正需要补充／核对的项目。

示例中的 `notice` 明确全部身份和经历为合成，不可直接发送；它不是新用户任务的默认提示语。普通真实邮件可写“草稿；待补充截止时间与提交方式”，不机械添加“虚构”。

`quote` 在指定自然段中必须恰好出现一次。同一段多个批注不得重叠；片段重复时扩展成唯一片段。标号无需固定为六条。渲染失败应修正数据，不能删正文让错误消失。

## 示例运行

从技能目录执行，输出到技能目录以外：

```bash
python3 scripts/render_annotations.py --input assets/example.json --output /absolute/path/output/example.html
```

生成后打开 HTML 看宽屏两栏、手机单栏、标号跳转与长链接换行。TXT 应只有主题与邮件原文，不含批注或参考链接；虚构声明以独立说明保留在 TXT 顶部，与可复制邮件明确分隔。

渲染器不自动写邮件、不调用模型、不核实网页、不发送邮件。内容判断由当前代理完成。若用户只要文本邮件，不需要 JSON 或渲染步骤。
