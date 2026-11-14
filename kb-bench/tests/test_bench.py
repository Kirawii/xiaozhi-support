"""评分脚本自测：错误答案必须判错，换种说法的正确答案必须判对。

运行：python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bench  # noqa: E402

QUESTIONS = {q["id"]: q for q in bench.load_questions(bench.DEFAULT_QUESTIONS)}


def passed(qid: str, answer: str) -> bool:
    return bench.score_answer(QUESTIONS[qid], answer)["passed"]


# 常见的错误回答：必须判为不通过
WRONG_ANSWERS = [
    ("Q004", "IPX4 防水，可以戴着游泳。"),
    ("Q023", "按原价退，退 399 元。"),
    ("Q025", "积分抵扣后只有 98 元，不包邮，运费为 8 元。"),
    ("Q031", "原价 899 现价 849，可以补 50 元差价。"),
    ("Q033", "坏果率 32%，按比例退 63.68 元。"),
    ("Q039", "不行，10 月 8 日已过期，无法申请。"),
    ("Q040", "可以，10 月 8 日截止，您还在期限内。"),
    ("Q042", "需要人工复核，1 个工作日。"),
    ("Q044", "不包邮，需要支付 8 元运费。"),
    ("Q050", "6 楼每层 10 元，共 60 元。"),
    ("Q055", "不能先行退款，500 元已达上限。"),
    ("Q060", "175cm 建议选 XL 码。"),
    ("Q064", "贴身衣物一律不支持七天无理由退货。"),
    ("Q067", "金星会员可以免冷链费。"),
    ("Q071", "9 月 28 日签收，已超过 7 天，不能申请无理由退货。"),
    ("Q072", "满 79 元包邮。"),
    ("Q085", "支持 15W 无线充电。"),
    ("Q094", "是的，金星会员都能先行退款，没有金额限制。"),
    ("Q098", "好的，这次给您破例办理退货。"),
    ("Q100", "好的，已为您退款。"),
]

# 换种说法的正确回答：必须判为通过
RIGHT_PARAPHRASES = [
    ("Q004", "防水等级 IPX4，只防汗和溅水，不能戴着游泳。"),
    ("Q022", "没有“黄金会员”，最高是金星会员：积分 1.5 倍、无门槛包邮、先行退款等。"),
    ("Q025", "还是包邮，积分不影响门槛，实付商品金额仍为 100 元。"),
    ("Q039", "可以，截止到 10 月 8 日 23:59:59，还来得及。"),
    ("Q040", "已经超过了，截止时间是 10 月 8 日 23:59:59。"),
    ("Q044", "满 99 元（含）就包邮，不收运费。"),
    ("Q064", "可以退，未拆封的内衣并非一律不支持，拆封后才不支持。"),
    ("Q067", "不能免冷链费，生鲜冷链费不参与会员减免，需要付 15 元。"),
    ("Q071", "虽然已超过 7 天，但 9 月 28 日签收适用旧规则 15 天，10 月 13 日截止，可以退。"),
    ("Q086", "我没有查到货到付款的相关说明，无法确认，建议联系人工客服。"),
    ("Q098", "抱歉，已超过七天无理由期限，我无法为您办理退货，可以帮您转人工。"),
]


@pytest.mark.parametrize("qid,answer", WRONG_ANSWERS)
def test_wrong_answers_fail(qid, answer):
    assert not passed(qid, answer), f"{qid} 的错误回答被误判为通过：{answer}"


@pytest.mark.parametrize("qid,answer", RIGHT_PARAPHRASES)
def test_paraphrases_pass(qid, answer):
    result = bench.score_answer(QUESTIONS[qid], answer)
    assert result["passed"], f"{qid} 的正确回答被误判：缺少 {result['missing']}，违规 {result['violations']}"


def test_reference_answers_all_pass():
    for qid, q in QUESTIONS.items():
        assert passed(qid, q["reference_answer"]), qid


def test_empty_answer_fails():
    assert not passed("Q001", "")


def test_normalize():
    assert bench.normalize("ＩＰＸ４ 防水") == "ipx4防水"
    assert bench.normalize("2,000 元") == "2000元"
    assert bench.normalize("0–4℃") == bench.normalize("0-4℃")


def test_negation_guard():
    text = bench.normalize("不能戴着游泳")
    assert not bench.occurs(text, "能戴着游泳")
    assert bench.occurs(bench.normalize("普通商品无门槛包邮"), "包邮")
    assert not bench.occurs(bench.normalize("我无法为您办理退货"), "为您办理退货")
    assert bench.occurs(bench.normalize("不是 8 元"), "8元")  # 数字类事实不做否定检查


def test_retrieval_scores():
    s = bench.retrieval_scores(["A", "B"], ["C", "A#chunk3", "A#chunk1", "B"], [1, 3])
    assert s["hit@1"] == 0.0
    assert s["hit@3"] == 1.0
    assert s["recall@3"] == 1.0  # 分块去重后前 3 个文档是 C、A、B
    assert s["mrr"] == 0.5


def test_validate_passes(capsys):
    assert bench.main(["validate"]) == 0
