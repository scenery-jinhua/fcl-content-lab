"""质量层：硬规则引擎 + 软打分（LLM-as-Judge）。

为什么是「两层」，而不是直接让大模型打个分就完事：
- 规则引擎零成本、确定性、可解释，广告法这类硬红线必须靠它兜底；
- 大模型能判断「调性对不对」这种模糊标准，但它会幻觉、会放水、同一段话两次打分不一样；
- 人工终审兜最后一道。

三层各司其职：规则拦住「绝对不能发」，模型筛掉「不值得看」，人决定「发不发」。
这套话术面试官很吃，因为它体现了你真的想过线上会怎么出事。
"""

from __future__ import annotations

import re

from ..config import settings
from ..providers.base import extract_json
from .prompts import DIM_LABELS, DIM_WEIGHTS, build_judge_messages

# 词表来源：广告法绝对化用语 + 小红书 2026 常见违禁词清单 + 站外导流话术。
# 真实项目会做成可配置的词库（放数据库 + 后台可编辑），这里为了可读性写死。
BANNED_WORDS: dict[str, str] = {
    # 绝对化用语
    "最好": "换成「我很喜欢」这类主观表述",
    "最强": "删掉比较级，改为描述具体体验",
    "最佳": "改为「适合…」",
    "最优": "改为「比较推荐」",
    "最显瘦": "改为「视觉上更利落」",
    "最显气质": "改为「气质比较稳」",
    "最便宜": "改为「价格带在…」",
    "第一": "删掉排名类表述",
    "顶级": "改为「质感不错」",
    "极致": "改为「做得比较细」",
    "绝对": "删掉绝对化承诺",
    "100%": "删掉百分比承诺",
    "百分百": "删掉百分比承诺",
    "唯一": "删掉排他表述",
    "独一无二": "删掉排他表述",
    "永久": "改为「比较耐穿」",
    "根治": "服装不涉及功效，直接删",
    "国家级": "删掉资质类表述",
    "全网": "删掉全网类表述",
    "顶级享受": "改为「体验不错」",
    # 站外导流
    "微信": "不要导流站外",
    "vx": "不要导流站外",
    "加我": "不要导流站外",
    "私信发链接": "不要导流站外",
    "评论区链接": "不要导流站外",
}

# 诱导互动（小红书明确不鼓励）
INDUCE_WORDS = ["点赞过百", "转发抽奖", "关注后私信", "扣1", "评论区扣"]

TITLE_MAX = 20
BODY_MIN, BODY_MAX = 120, 600
TAG_MIN, TAG_MAX = 3, 8


def check_rules(title: str, body: str, tags: list[str]) -> dict:
    """确定性规则检查。返回命中明细，rule_score 满分 100。"""
    violations: list[dict] = []
    text = f"{title}\n{body}"

    for word, suggest in BANNED_WORDS.items():
        if word in text:
            violations.append({"type": "banned_word", "word": word, "suggest": suggest})

    for word in INDUCE_WORDS:
        if word in text:
            violations.append({"type": "induce_interaction", "word": word, "suggest": "去掉诱导互动话术"})

    if len(title) > TITLE_MAX:
        violations.append({"type": "title_too_long", "word": f"{len(title)}字", "suggest": f"标题压缩到 {TITLE_MAX} 字内"})
    if len(body) < BODY_MIN:
        violations.append({"type": "body_too_short", "word": f"{len(body)}字", "suggest": "补充材质/搭配/场景细节"})
    if len(body) > BODY_MAX:
        violations.append({"type": "body_too_long", "word": f"{len(body)}字", "suggest": "删减重复表述"})
    if not (TAG_MIN <= len(tags) <= TAG_MAX):
        violations.append({"type": "tag_count", "word": f"{len(tags)}个", "suggest": f"标签保持 {TAG_MIN}-{TAG_MAX} 个"})

    # 去重：同一个词在标题和正文都出现只报一次
    seen: set[tuple] = set()
    unique: list[dict] = []
    for v in violations:
        key = (v["type"], v["word"])
        if key not in seen:
            seen.add(key)
            unique.append(v)

    penalty = min(100, len(unique) * 12)
    return {
        "violations": unique,
        "rule_score": 100 - penalty,
        "passed": not unique,
    }


async def judge(provider, title: str, body: str, tags: list[str]) -> dict:
    """LLM-as-Judge：四维打分。失败时退化为规则分，保证链路不中断。"""
    messages = build_judge_messages(title, body, tags)
    try:
        result = await provider.chat(messages, temperature=0.0, json_mode=True)
        data = extract_json(result.text)
        scores = {k: float(data.get("scores", {}).get(k, 0)) for k in DIM_WEIGHTS}
        reasons = {k: str(data.get("reasons", {}).get(k, "")) for k in DIM_WEIGHTS}
        total = sum(scores[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS)
        return {
            "scores": scores,
            "reasons": reasons,
            "total": round(total, 1),
            "verdict": "pass" if total >= settings.pass_threshold else "rewrite",
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "judge_model": result.model,
            "fallback": False,
        }
    except Exception as exc:  # noqa: BLE001
        # 判分挂了不能让整条内容失败：用规则结果给一个保守分，并标记 fallback，方便看板统计
        return {
            "scores": {"brand_tone": 60.0, "platform_fit": 60.0, "info_density": 60.0, "compliance": 60.0},
            "reasons": {k: f"评委不可用，降级为保守分（{type(exc).__name__}）" for k in DIM_WEIGHTS},
            "total": 60.0,
            "verdict": "rewrite",
            "tokens_in": 0,
            "tokens_out": 0,
            "judge_model": "fallback",
            "fallback": True,
        }


def merge_scores(rule: dict, judged: dict) -> dict:
    """规则分和模型分合并：合规维度取两者最小值（宁可误杀，不可漏放）。"""
    scores = dict(judged["scores"])
    compliance = min(scores.get("compliance", 100), rule["rule_score"])
    scores["compliance"] = compliance
    total = sum(scores[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS)
    return {
        "scores": scores,
        "reasons": judged["reasons"],
        "total": round(total, 1),
        "verdict": "pass" if total >= settings.pass_threshold and rule["passed"] else "rewrite",
        "fallback": judged.get("fallback", False),
    }


def violations_to_feedback(violations: list[dict]) -> str:
    """把违规明细转成给模型的自然语言反馈——这是「自修复重写」的关键一步。

    只说「不及格」模型不知道怎么改；说清「'最显瘦'违规，改成'视觉上更利落'」它才会改。
    """
    if not violations:
        return ""
    bullets = "；".join(f"「{v['word']}」→ {v['suggest']}" for v in violations[:6])
    return f"以下表述不合规或不合格，请改写：{bullets}。"


def describe_dimensions(scores: dict) -> list[dict]:
    return [
        {"dim": k, "label": DIM_LABELS[k], "score": round(scores.get(k, 0), 1), "weight": DIM_WEIGHTS[k]}
        for k in DIM_WEIGHTS
    ]


_TAG_RE = re.compile(r"^#[^\s#]{1,20}$")


def normalize_tags(raw) -> list[str]:
    """模型返回的标签千奇百怪：没有 #、带空格、重复、超过 8 个。统一清洗。"""
    if isinstance(raw, str):
        raw = re.split(r"[,，\s]+", raw)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw:
        t = str(t).strip().replace(" ", "")
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        if len(t) <= 21 and _TAG_RE.match(t) and t not in out:
            out.append(t)
    return out[:TAG_MAX]
