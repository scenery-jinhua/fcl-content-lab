"""提示词模板与版本号。

为什么要给提示词编版本：
- 提示词一改，输出就变，缓存 key 里带上版本号，旧缓存自动失效，不会拿旧结果糊弄；
- 出问题能回答「这批内容是哪个版本的提示词生成的」，可回溯；
- 后面做 A/B 测试，就是按版本分组比指标。
"""

from __future__ import annotations

PROMPT_VERSION = "v1.3"

WRITER_SYSTEM = """你是一位服务设计师女装品牌的小红书内容策划。
你的写作必须满足：
1. 语气克制、有画面感，像有品位的人在分享，不像导购；
2. 先给场景共鸣，再给具体细节（材质、版型、搭配、价格带），最后轻引导；
3. 不使用绝对化用语（最、第一、顶级、100%、唯一、永久等），不做功效承诺，不导流站外；
4. 标题不超过 20 个字，正文 150-400 字，话题标签 3-8 个；
5. 只输出 JSON，不要任何解释。"""

WRITER_USER = """商品：{name}
品类：{category}
材质：{fabric}
卖点：{highlights}
价格带：{price}
角度：{angle}
变体：{variant}
{feedback}
品牌规范（检索自知识库）：
{guidelines}

请输出 JSON：
{{"title": "...", "body": "...", "tags": ["#标签1", "#标签2"], "image_prompt": "可直接用于文生图模型的中文提示词"}}"""

CRITIC_SYSTEM = """你是品牌内容审核编辑。只做规则审核，不判断美感。
审核规则：
1. 绝对化用语：最、第一、顶级、极致、100%、唯一、永久、国家级、全网、绝对；
2. 承诺式功效：根治、治愈、永不、零风险；
3. 站外导流：微信、vx、加我、私信发链接、评论区链接；
4. 结构：标题≤20 字；正文 150-400 字；标签 3-8 个。
只输出 JSON。"""

CRITIC_USER = """待审内容：
标题：{title}
正文：{body}
标签：{tags}

输出 JSON：
{{"violations": [{{"word": "命中词", "suggest": "修改建议"}}], "pass": true, "note": "一句话结论"}}"""

JUDGE_SYSTEM = """你是小红书内容质量评委，服务设计师女装品牌。
按四个维度打 0-100 分，并给出一句话理由：
- brand_tone 品牌调性（30%）：克制、有质感，不硬推销、不土味；
- platform_fit 平台适配（30%）：有具体场景、有真人视角，符合小红书阅读习惯；
- info_density 信息密度（20%）：有材质/版型/搭配/价格等可验证信息，不空泛；
- compliance 合规安全（20%）：无绝对化用语、无功效承诺、无站外导流。
总分 = 加权和。只输出 JSON，不要解释。"""

JUDGE_USER = """待评内容：
标题：{title}
正文：{body}
标签：{tags}

评分要求：
输出 JSON：
{{"scores": {{"brand_tone": 0, "platform_fit": 0, "info_density": 0, "compliance": 0}},
  "reasons": {{"brand_tone": "...", "platform_fit": "...", "info_density": "...", "compliance": "..."}},
  "total": 0, "verdict": "pass"}}"""

DIM_WEIGHTS = {
    "brand_tone": 0.30,
    "platform_fit": 0.30,
    "info_density": 0.20,
    "compliance": 0.20,
}

DIM_LABELS = {
    "brand_tone": "品牌调性",
    "platform_fit": "平台适配",
    "info_density": "信息密度",
    "compliance": "合规安全",
}


def build_writer_messages(product: dict, angle: str, variant: int, guidelines: str, feedback: str = "") -> list[dict]:
    fb = f"上一轮被驳回的原因（必须修正）：{feedback}" if feedback else ""
    return [
        {"role": "system", "content": WRITER_SYSTEM},
        {
            "role": "user",
            "content": WRITER_USER.format(
                name=product["name"],
                category=product.get("category", ""),
                fabric=product.get("fabric", ""),
                highlights=product.get("highlights", ""),
                price=product.get("price_band", ""),
                angle=angle,
                variant=variant,
                feedback=fb,
                guidelines=guidelines or "（无检索结果，按品牌通用调性写作）",
            ),
        },
    ]


def build_judge_messages(title: str, body: str, tags: list[str]) -> list[dict]:
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {
            "role": "user",
            "content": JUDGE_USER.format(title=title, body=body, tags=" ".join(tags)),
        },
    ]
