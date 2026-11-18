# 本地部署与验收

## 安装 Skill

把压缩包中的完整 `resume-fix/` 文件夹放进所用代理支持的 Skills 目录。例如 Codex 的个人目录是 `~/.codex/skills/`，Claude Code 常用 `~/.claude/skills/`；以安装版本的文档为准。不要覆盖同名现有技能，先备份或合并。也可以直接让代理读取解压目录的 SKILL.md。

本包 1.1.2 是标准指令 Skill＋本地渲染脚本，不是 Codex 模板图库包，也不是自动运行的后台服务。没有模型 API 时，仍可用合成样例验证渲染；实际对话追问与改写依赖宿主代理的模型。升级后让客户端重新加载本 Skill，避免旧会话继续使用已缓存的旧指令。

## Python 环境

Python 3.10 或更高；建议独立虚拟环境。进入解压后的 `resume-fix` 目录：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python scripts/make_example.py --out /absolute/path/example
./.venv/bin/python scripts/make_example.py --followup --out /absolute/path/followup-example
```

Windows 把 `./.venv/bin/python` 换成 `.venv\Scripts\python.exe`，路径换成本机实际绝对路径。离线环境可预先准备与 Python／操作系统匹配的 PyMuPDF wheel，用 `pip install --no-index --find-links /path/to/wheels -r requirements.txt` 安装。

依赖固定为 PyMuPDF 1.26.5，中文字体使用其内置 Droid Sans Fallback 并嵌入输出。没有 Mac 专属路径或字体要求。PyMuPDF 为 AGPL／商业双许可证；公司部署、分发及与其他代码集成前应按实际使用方式审核依赖许可证，本包不替代商业授权。

## 实际使用

向代理提供 PDF 与目标岗位，例如：

> 用 $resume-fix 修改这份简历，目标是 AI 产品经理（研发平台 / Agent 方向）。

默认行为：先简短诊断，每轮直接问 1–2 个关键问题并等待回答；清楚的回答用于改写，含糊／冲突的事实再澄清。信息足够后生成同款双段精批 PDF。用户随时可说「跳过」「记不清」或「先出初稿」。已答题在 PDF 中变成「已补充」记录，不再重复问。

不想问答时直接说「不追问，按现有信息先生成初稿」。仅运行下面的 Python 命令不会启动聊天，打开 PDF 也不会弹出 AI 问答；对话发生在加载 Skill 的宿主客户端里。

只在用户要求对外演示／脱敏时建立隐私配置并运行：

```bash
./.venv/bin/python scripts/resume_fix.py sanitize --pdf /absolute/path/source.pdf --privacy /absolute/path/privacy.json --out /absolute/path/safe.pdf
./.venv/bin/python scripts/resume_fix.py inspect --pdf /absolute/path/safe.pdf --out /absolute/path/work
./.venv/bin/python scripts/resume_fix.py validate --pdf /absolute/path/safe.pdf --review /absolute/path/review.json
./.venv/bin/python scripts/resume_fix.py render --pdf /absolute/path/safe.pdf --review /absolute/path/review.json --out /absolute/path/result/review.pdf
```

模型依据 `inspection.json` 与渲染页生成符合协议的 review.json，不需要渲染器联网。每页会输出 `review-1.png` 等及 `review.qa.json`。数字／单位、原句、PDF 哈希不一致时返回非零退出码；错误不能绕过，先核对原材料。

## 复现边界

- 固定模板使几何、字号、颜色、原 PDF 摘录方式接近确认稿；内容因模型、简历与用户回答而变化。
- 合成例子的原文来自仓库手造测试材料，示例改写／问题是 fixture，不是一次新的真实模型运行。不可用它宣传模型实测成绩。
- 修改内容规则在 references/content-rules.md，版式常量在 assets/layout.json，数据协议单独版本化。不要每次运行重新手写渲染代码。
- 原文／模型返回／review.json／qa.json 属于本地工作产物，默认不打包分享，不自动上传。
- conversation.json 同样只保存在本次工作目录，记录已问、已答、跳过和待核实状态。新一轮不能把旧用户回答套用到另一份简历。
- 自动测试覆盖回答状态与证据引用、已答显示及分页，不能证明每个宿主模型都正确逐轮提问。首次部署请按 references/conversation.md 的行为验收表，在目标客户端试一轮「回答→跳过→出初稿」。

## 与流水产品的关系

正式项目内维护路径为 `skills/resume-fix`。原网站的 `backend/resume_fix.py` 仍提供 diagnose → rewrite 的已有接口，本包不修改路由、计费或前端下载按钮。后端要生成同款 PDF 时，应将可信的诊断／改写结果适配为本包 review.json，再调用渲染器；不能把普通 Markdown 当作结构化证据，也不能声称网站已经自动接入该导出。

## 重新打包

```bash
./.venv/bin/python scripts/package_skill.py --out /absolute/path/resume-fix-local.zip
```

打包器只收固定白名单，排除环境、缓存、真实文档、运行目录和任意额外文件；输出包含文件校验清单。禁止把整个项目目录直接压缩当作 Skill。
