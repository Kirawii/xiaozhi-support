#!/usr/bin/env python3
"""星川商城知识库问答基准：校验、生成模板、评分、大模型裁判。

用法（在仓库根目录运行）：
    python scripts/bench.py validate
    python scripts/bench.py template --out predictions.jsonl
    python scripts/bench.py export-reference --out reference.jsonl
    python scripts/bench.py score --pred predictions.jsonl --report report.md
    python scripts/bench.py judge --pred predictions.jsonl --out judge.jsonl
    python scripts/bench.py score --pred predictions.jsonl --judge judge.jsonl --report report.md

预测文件为 JSONL，每行一个对象：
    {"id": "Q001", "answer": "系统的回答", "retrieved": ["P01-return-7day", "F01-faq"]}
retrieved 可省略；可以是文档 ID，也可以是“文档ID#分块ID”，评分时取 # 之前的部分。

依赖：PyYAML。judge 命令额外需要 openai SDK。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 PyYAML，请先运行：pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KB = ROOT / "knowledge_base"
DEFAULT_QUESTIONS = ROOT / "benchmark" / "questions.yaml"

VALID_BEHAVIORS = {"answer", "admit_unknown", "clarify", "correct_premise", "decline_promise"}
VALID_DIFFICULTY = {"easy", "medium", "hard"}

# 否定词：关键词前 3 个字符内出现这些词时，视为该处是被否定的说法。
# “无”单独出现时不算否定（如“无门槛包邮”），只认“无法、无需”等组合。
NEGATION_CHARS = set("不没未非别勿")
NEGATION_WORDS = ("无法", "无需", "无须", "并无")
NEGATION_WINDOW = 3


# ---------------------------------------------------------------- 文本处理

def normalize(text: str) -> str:
    """统一全角半角、大小写、破折号和空白，便于做关键词匹配。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[–—―−－~〜～]", "-", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 2,000 → 2000
    return text


def _needs_negation_guard(pattern: str) -> bool:
    """数字开头的事实类关键词、纯标点关键词、本身就以否定词开头的关键词，不做否定检查。"""
    if not pattern:
        return False
    if pattern[0].isdigit() or pattern[0] in NEGATION_CHARS or pattern.startswith(NEGATION_WORDS):
        return False
    return any(ch.isalnum() for ch in pattern)


def _is_negated(window: str) -> bool:
    return any(ch in NEGATION_CHARS for ch in window) or any(w in window for w in NEGATION_WORDS)


def occurs(text_norm: str, pattern: str) -> bool:
    """pattern 在文本中至少出现一次“未被否定”的匹配。text_norm 必须已经 normalize。"""
    pat = normalize(pattern)
    if not pat:
        return False
    guard = _needs_negation_guard(pat)
    start = 0
    while True:
        i = text_norm.find(pat, start)
        if i == -1:
            return False
        if not guard:
            return True
        window = text_norm[max(0, i - NEGATION_WINDOW):i]
        if not _is_negated(window):
            return True
        start = i + 1


# ---------------------------------------------------------------- 加载数据

def parse_front_matter(raw: str) -> tuple[dict, str]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            return (yaml.safe_load(parts[1]) or {}), parts[2]
    return {}, raw


def load_kb(kb_dir: Path) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for path in sorted(kb_dir.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(raw)
        doc_id = meta.get("doc_id")
        if not doc_id:
            raise ValueError(f"{path} 缺少 doc_id")
        if doc_id in docs:
            raise ValueError(f"doc_id 重复：{doc_id}")
        docs[doc_id] = {"path": path, "meta": meta, "body": body, "raw": raw}
    return docs


def load_questions(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["questions"]


def select_split(questions: list[dict], split: str | None) -> list[dict]:
    """dev = 奇数题号，test = 偶数题号，all = 全部。调参只用 dev，test 只在最终报告时跑一次。"""
    if not split or split == "all":
        return questions
    want = 1 if split == "dev" else 0
    return [q for q in questions if int(str(q["id"]).lstrip("Qq")) % 2 == want]


def load_jsonl(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path} 第 {n} 行不是合法 JSON：{e}") from e
            rows[str(obj["id"])] = obj
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 规则评分

def score_answer(q: dict, answer: str) -> dict:
    text = normalize(answer)
    groups = q.get("must_include") or []
    missing = [g for g in groups if not any(occurs(text, alt) for alt in g)]
    violations = [p for p in (q.get("must_not_include") or []) if occurs(text, p)]
    hit = len(groups) - len(missing)
    return {
        "facts_hit": hit,
        "facts_total": len(groups),
        "missing": missing,
        "violations": violations,
        "passed": bool(answer.strip()) and not missing and not violations,
    }


def retrieval_scores(gold: list[str], retrieved: list[str], ks: list[int]) -> dict:
    seen: list[str] = []
    for r in retrieved:
        doc = str(r).split("#", 1)[0]
        if doc not in seen:
            seen.append(doc)
    out: dict = {}
    gold_set = set(gold)
    for k in ks:
        top = set(seen[:k])
        out[f"hit@{k}"] = 1.0 if gold_set & top else 0.0
        out[f"recall@{k}"] = len(gold_set & top) / len(gold_set)
    rr = 0.0
    for rank, doc in enumerate(seen, 1):
        if doc in gold_set:
            rr = 1.0 / rank
            break
    out["mrr"] = rr
    return out


# ---------------------------------------------------------------- 命令：validate

def cmd_validate(args) -> int:
    docs = load_kb(Path(args.kb))
    questions = load_questions(Path(args.questions))
    errors: list[str] = []
    ids = Counter(q["id"] for q in questions)
    for qid, n in ids.items():
        if n > 1:
            errors.append(f"{qid}: 题号重复 {n} 次")

    required = ["id", "category", "difficulty", "behavior", "question", "reference_answer",
                "must_include", "gold_docs"]
    for q in questions:
        qid = q.get("id", "?")
        for key in required:
            if key not in q:
                errors.append(f"{qid}: 缺少字段 {key}")
        if q.get("behavior") not in VALID_BEHAVIORS:
            errors.append(f"{qid}: behavior 不合法：{q.get('behavior')}")
        if q.get("difficulty") not in VALID_DIFFICULTY:
            errors.append(f"{qid}: difficulty 不合法：{q.get('difficulty')}")
        for d in q.get("gold_docs") or []:
            if d not in docs:
                errors.append(f"{qid}: gold_docs 中的文档不存在：{d}")
        for ev in q.get("evidence") or []:
            doc = docs.get(ev["doc"])
            if doc is None:
                errors.append(f"{qid}: evidence 引用的文档不存在：{ev['doc']}")
            elif ev["quote"] not in doc["raw"]:
                errors.append(f"{qid}: evidence 原文在 {ev['doc']} 中找不到：{ev['quote']}")
            elif ev["doc"] not in (q.get("gold_docs") or []):
                errors.append(f"{qid}: evidence 文档 {ev['doc']} 不在 gold_docs 中")
        if q.get("behavior") == "answer" and not q.get("evidence"):
            errors.append(f"{qid}: 正常作答题缺少 evidence")
        res = score_answer(q, q.get("reference_answer", ""))
        if not res["passed"]:
            errors.append(f"{qid}: 参考答案未通过自身评分规则：缺少 {res['missing']}，违规 {res['violations']}")

    print(f"知识库文档：{len(docs)} 篇")
    for d in sorted(docs):
        status = docs[d]["meta"].get("status", "")
        print(f"  {d:40s} {docs[d]['meta'].get('title', '')}（{status}）")
    print(f"\n题目：{len(questions)} 道")
    for label, key in (("题型", "category"), ("期望行为", "behavior"), ("难度", "difficulty")):
        counts = Counter(q.get(key) for q in questions)
        print(f"  {label}：" + "，".join(f"{k} {v}" for k, v in counts.items()))
    used = Counter(d for q in questions for d in q.get("gold_docs") or [])
    unused = [d for d in docs if d not in used]
    if unused:
        print(f"\n注意：以下文档没有任何题目引用：{unused}")

    if errors:
        print(f"\n发现 {len(errors)} 个问题：")
        for e in errors:
            print("  - " + e)
        return 1
    print("\n校验通过：所有 evidence 都能在文档中找到，所有参考答案都通过自身评分规则。")
    return 0


# ---------------------------------------------------------------- 命令：template / export-reference

def cmd_template(args) -> int:
    questions = select_split(load_questions(Path(args.questions)), args.split)
    rows = [{"id": q["id"], "question": q["question"], "answer": "", "retrieved": []} for q in questions]
    write_jsonl(Path(args.out), rows)
    print(f"已写入 {len(rows)} 行模板：{args.out}")
    return 0


def cmd_export_reference(args) -> int:
    questions = load_questions(Path(args.questions))
    rows = [{"id": q["id"], "answer": q["reference_answer"], "retrieved": list(q.get("gold_docs") or [])}
            for q in questions]
    write_jsonl(Path(args.out), rows)
    print(f"已写入参考答案 {len(rows)} 行：{args.out}")
    return 0


# ---------------------------------------------------------------- 命令：score

def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def cmd_score(args) -> int:
    questions = select_split(load_questions(Path(args.questions)), args.split)
    preds = load_jsonl(Path(args.pred))
    judge = load_jsonl(Path(args.judge)) if args.judge else {}
    ks = sorted({int(k) for k in args.k.split(",")})

    results = []
    for q in questions:
        p = preds.get(q["id"], {})
        answer = p.get("answer") or ""
        res = score_answer(q, answer)
        res.update(id=q["id"], category=q["category"], difficulty=q["difficulty"],
                   behavior=q["behavior"], question=q["question"], answer=answer,
                   missing_prediction=q["id"] not in preds)
        gold = q.get("gold_docs") or []
        if gold and "retrieved" in p and p["retrieved"] is not None:
            res["retrieval"] = retrieval_scores(gold, p["retrieved"], ks)
        j = judge.get(q["id"])
        if j:
            res["judge_verdict"] = j.get("verdict")
            res["judge_score"] = float(j.get("score", 0))
            res["judge_reason"] = j.get("reason", "")
        results.append(res)

    def summarize(rows):
        n = len(rows)
        s = {
            "n": n,
            "pass": sum(r["passed"] for r in rows) / n if n else 0.0,
            "fact_recall": (sum(r["facts_hit"] for r in rows) / max(1, sum(r["facts_total"] for r in rows))),
            "violations": sum(bool(r["violations"]) for r in rows),
        }
        judged = [r for r in rows if "judge_score" in r]
        if judged:
            s["judge"] = sum(r["judge_score"] for r in judged) / len(judged)
            s["judged_n"] = len(judged)
        return s

    overall = summarize(results)
    by_cat = OrderedDict()
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    by_diff = OrderedDict((d, [r for r in results if r["difficulty"] == d]) for d in ("easy", "medium", "hard"))

    retr_rows = [r["retrieval"] for r in results if "retrieval" in r]
    retr = {}
    if retr_rows:
        for key in retr_rows[0]:
            retr[key] = sum(x[key] for x in retr_rows) / len(retr_rows)

    lines: list[str] = []
    lines.append("# 星川知识库问答基准：评分报告\n")
    lines.append(f"预测文件：`{args.pred}`" + (f"；裁判结果：`{args.judge}`" if args.judge else "") + "\n")
    missing_preds = sum(r["missing_prediction"] for r in results)
    if missing_preds:
        lines.append(f"注意：有 {missing_preds} 道题没有预测结果，按不通过计。\n")

    lines.append("## 总体\n")
    head = "| 指标 | 数值 |\n| --- | --- |"
    body = [
        f"| 题目数 | {overall['n']} |",
        f"| 规则通过率（关键事实全部命中且无错误说法） | {_pct(overall['pass'])} |",
        f"| 关键事实召回率 | {_pct(overall['fact_recall'])} |",
        f"| 出现错误说法的题数 | {overall['violations']} |",
    ]
    if "judge" in overall:
        body.append(f"| 裁判平均分（正确 1 / 部分 0.5 / 错误 0，共 {overall['judged_n']} 题） | {_pct(overall['judge'])} |")
    lines.append(head + "\n" + "\n".join(body) + "\n")

    def table(title, groups):
        has_judge = any("judge" in summarize(rows) for rows in groups.values())
        cols = "| 分组 | 题数 | 规则通过率 | 事实召回率 | 错误说法 |" + (" 裁判分 |" if has_judge else "")
        sep = "| --- | --- | --- | --- | --- |" + (" --- |" if has_judge else "")
        out = [f"## {title}\n", cols, sep]
        for name, rows in groups.items():
            if not rows:
                continue
            s = summarize(rows)
            row = f"| {name} | {s['n']} | {_pct(s['pass'])} | {_pct(s['fact_recall'])} | {s['violations']} |"
            if has_judge:
                row += f" {_pct(s['judge']) if 'judge' in s else '-'} |"
            out.append(row)
        return "\n".join(out) + "\n"

    lines.append(table("按题型", by_cat))
    lines.append(table("按难度", by_diff))

    if retr:
        lines.append(f"## 检索指标（{len(retr_rows)} 道有标准文档且提供了 retrieved 的题）\n")
        lines.append("| 指标 | 数值 |\n| --- | --- |")
        for k in ks:
            lines.append(f"| hit@{k}（前 {k} 个结果中至少命中 1 篇标准文档） | {_pct(retr[f'hit@{k}'])} |")
        for k in ks:
            lines.append(f"| recall@{k}（标准文档被召回的比例） | {_pct(retr[f'recall@{k}'])} |")
        lines.append(f"| MRR | {retr['mrr']:.3f} |\n")

    failed = [r for r in results if not r["passed"]]
    lines.append(f"## 未通过的题目（{len(failed)} 道）\n")
    for r in failed:
        lines.append(f"### {r['id']}（{r['category']}，{r['difficulty']}）\n")
        lines.append(f"- 问题：{r['question']}")
        lines.append(f"- 回答：{r['answer'] or '（无）'}")
        if r["missing"]:
            lines.append("- 缺少的关键事实：" + "；".join(" / ".join(g) for g in r["missing"]))
        if r["violations"]:
            lines.append("- 出现的错误说法：" + "、".join(r["violations"]))
        if "judge_verdict" in r:
            lines.append(f"- 裁判：{r['judge_verdict']}，{r['judge_reason']}")
        lines.append("")

    report = "\n".join(lines)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"报告已写入：{args.report}")
    if args.json:
        Path(args.json).write_text(json.dumps({"overall": overall, "retrieval": retr, "results": results},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已写入：{args.json}")

    print(f"题目 {overall['n']} | 规则通过率 {_pct(overall['pass'])} | "
          f"事实召回率 {_pct(overall['fact_recall'])} | 错误说法 {overall['violations']} 题"
          + (f" | 裁判分 {_pct(overall['judge'])}" if "judge" in overall else ""))
    if retr:
        print("检索：" + "，".join(f"{k} {_pct(v) if k != 'mrr' else f'{v:.3f}'}" for k, v in retr.items()))
    return 0


# ---------------------------------------------------------------- 命令：judge

BEHAVIOR_RUBRIC = {
    "answer": "候选回答需要给出与参考答案一致的结论和关键事实（数字、时限、条件），不得包含与知识库矛盾的信息。",
    "admit_unknown": "知识库没有覆盖这个问题。候选回答应说明无法确认或没有相关信息，并给出合理的引导（如联系人工客服、查看详情页），不得编造具体信息。",
    "clarify": "问题缺少关键信息。候选回答应主动追问参考答案中提到的关键信息；可以附带通用规则，但不能在缺少信息的情况下给出确定的个案结论。",
    "correct_premise": "用户的问题包含错误前提。候选回答必须明确指出前提错误，并给出正确的规则。",
    "decline_promise": "用户要求超出规则的处理，或冒充身份要求绕过流程。候选回答必须礼貌拒绝、说明规则，不得承诺或执行规则外的操作，可以提供转人工等合规选项。",
}

JUDGE_PROMPT = """你是电商客服问答系统的评测员。请根据参考答案和评分要求，判断候选回答是否正确。

【用户问题】
{question}

【参考答案】
{reference}

【必须包含的关键信息】（每行是一组同义写法，满足其一即可）
{facts}

【本题的评分要求】
{rubric}

【候选回答】
{answer}

评判规则：
1. correct：结论正确，关键信息齐全，没有与参考答案矛盾的内容。表述方式不同不扣分。
2. partial：结论正确，但遗漏了部分关键信息，或附带了不影响结论的小错误。
3. incorrect：结论错误、关键数字错误、编造了参考答案中没有的规则，或违反了本题的评分要求。

只输出一个 JSON 对象，不要输出其他内容：
{{"verdict": "correct 或 partial 或 incorrect", "reason": "一句话说明理由"}}"""

VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def build_judge_prompt(q: dict, answer: str) -> str:
    facts = "\n".join("- " + " / ".join(g) for g in (q.get("must_include") or [])) or "（无）"
    return JUDGE_PROMPT.format(question=q["question"], reference=q["reference_answer"], facts=facts,
                               rubric=BEHAVIOR_RUBRIC[q["behavior"]], answer=answer or "（空）")


def parse_verdict(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"verdict": "incorrect", "reason": f"裁判输出无法解析：{text[:100]}"}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"verdict": "incorrect", "reason": f"裁判输出无法解析：{text[:100]}"}
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in VERDICT_SCORE:
        verdict = "incorrect"
    return {"verdict": verdict, "reason": obj.get("reason", "")}


def cmd_judge(args) -> int:
    questions = select_split(load_questions(Path(args.questions)), args.split)
    preds = load_jsonl(Path(args.pred))
    if args.limit:
        questions = questions[: args.limit]

    if args.dry_run:
        for q in questions[:3]:
            print("=" * 60)
            print(build_judge_prompt(q, preds.get(q["id"], {}).get("answer", "")))
        print(f"\n（dry-run：仅展示前 3 道题的裁判提示词，共 {len(questions)} 道题）")
        return 0

    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("judge 命令需要 openai SDK，请先运行：pip install openai")
    model = args.model or os.environ.get("JUDGE_MODEL")
    if not model:
        sys.exit("请通过 --model 或环境变量 JUDGE_MODEL 指定裁判模型")
    client = OpenAI()  # 读取 OPENAI_API_KEY 和 OPENAI_BASE_URL，可接任意 OpenAI 兼容接口

    done = load_jsonl(Path(args.out)) if Path(args.out).exists() and not args.overwrite else {}
    rows = list(done.values())
    for i, q in enumerate(questions, 1):
        if q["id"] in done:
            continue
        answer = preds.get(q["id"], {}).get("answer", "")
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "user", "content": build_judge_prompt(q, answer)}],
        )
        result = parse_verdict(resp.choices[0].message.content or "")
        row = {"id": q["id"], "verdict": result["verdict"], "score": VERDICT_SCORE[result["verdict"]],
               "reason": result["reason"], "model": model}
        rows.append(row)
        write_jsonl(Path(args.out), rows)  # 每题写一次，中断后可续跑
        print(f"[{i}/{len(questions)}] {q['id']} {row['verdict']}")
    print(f"裁判结果已写入：{args.out}")
    return 0


def cmd_passk(args) -> int:
    """同一套题跑 k 次（k 个预测文件），统计 pass^1（平均通过率）、pass^k（k 次全部通过）和时对时错的题。"""
    questions = select_split(load_questions(Path(args.questions)), args.split)
    runs = [load_jsonl(Path(p)) for p in args.pred]
    k = len(runs)
    per_q = {}
    for q in questions:
        per_q[q["id"]] = [score_answer(q, (r.get(q["id"], {}).get("answer") or ""))["passed"] for r in runs]
    n = len(per_q) or 1
    pass1 = sum(sum(v) for v in per_q.values()) / (n * k)
    passk = sum(all(v) for v in per_q.values()) / n
    flaky = [qid for qid, v in per_q.items() if any(v) and not all(v)]
    never = [qid for qid, v in per_q.items() if not any(v)]
    print(f"题目数：{len(per_q)}，运行次数 k = {k}（规则评分）")
    print(f"pass^1 = {pass1:.1%}    pass^{k} = {passk:.1%}")
    print(f"时对时错（{len(flaky)} 题）：{', '.join(flaky) or '无'}")
    print(f"全部答错（{len(never)} 题）：{', '.join(never) or '无'}")
    return 0


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="星川商城知识库问答基准")
    ap.add_argument("--kb", default=str(DEFAULT_KB), help="知识库目录")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="题目文件")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="校验题目与知识库的一致性")

    p = sub.add_parser("template", help="生成预测文件模板")
    p.add_argument("--out", required=True)
    p.add_argument("--split", choices=["dev", "test", "all"], default="all", help="dev=奇数题号，test=偶数题号")

    p = sub.add_parser("export-reference", help="把参考答案导出为预测文件（用于自测评分脚本）")
    p.add_argument("--out", required=True)

    p = sub.add_parser("score", help="评分并生成报告")
    p.add_argument("--pred", required=True)
    p.add_argument("--judge", help="judge 命令生成的裁判结果文件")
    p.add_argument("--k", default="1,3,5", help="检索指标的 k 值，逗号分隔")
    p.add_argument("--report", help="Markdown 报告输出路径")
    p.add_argument("--json", help="逐题明细 JSON 输出路径")
    p.add_argument("--split", choices=["dev", "test", "all"], default="all", help="dev=奇数题号，test=偶数题号")

    p = sub.add_parser("judge", help="用大模型裁判逐题打分")
    p.add_argument("--pred", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", help="裁判模型名，也可用环境变量 JUDGE_MODEL")
    p.add_argument("--limit", type=int, help="只评前 N 道题")
    p.add_argument("--overwrite", action="store_true", help="忽略已有结果，从头开始")
    p.add_argument("--dry-run", action="store_true", help="只打印裁判提示词，不调用模型")
    p.add_argument("--split", choices=["dev", "test", "all"], default="all", help="dev=奇数题号，test=偶数题号")

    p = sub.add_parser("passk", help="稳定性：同一套题的 k 次运行结果，输出 pass^k 和时对时错的题")
    p.add_argument("--pred", nargs="+", required=True, help="k 个预测文件")
    p.add_argument("--split", choices=["dev", "test", "all"], default="all", help="dev=奇数题号，test=偶数题号")

    args = ap.parse_args(argv)
    handlers = {"validate": cmd_validate, "template": cmd_template, "export-reference": cmd_export_reference,
                "score": cmd_score, "judge": cmd_judge, "passk": cmd_passk}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
