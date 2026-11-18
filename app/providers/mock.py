"""离线假模型（Mock Provider）。

它存在的意义不是「糊弄」，而是：
1. 让任何人 clone 下来不花一分钱、不配密钥就能跑通全链路、验证队列/缓存/评测逻辑；
2. 让测试稳定——真实模型输出有随机性，单测断言会飘；
3. 面试时能讲清楚「什么叫可替换的依赖」：换真模型只要改一个环境变量。

它也「演」了一点真实模型的坏毛病：偶尔超时、偶尔包 ```json 围栏、偶尔写出违禁词。
这样重试、JSON 修复、规则拦截这些代码才不是摆设，演示时能看到它们真的在工作。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re

from .base import LLMProvider, LLMResult, approx_tokens

ANGLES = {
    "通勤场景": {
        "hook": ["早八人真的会谢", "打工人衣柜的救命单品", "地铁里被问了三次链接"],
        "scene": "七点五十的闹钟、八点二十的地铁、九点整的晨会，留给穿搭的时间只有五分钟。",
        "benefit": ["套上就能出门，不用想搭配", "坐一天工位也不起皱", "室内室外都不闷"],
    },
    "约会场景": {
        "hook": ["他问我今天是不是特意打扮了", "第一次约会别穿太用力", "氛围感是穿出来的"],
        "scene": "傍晚六点的餐厅，灯光偏暖，坐下来那一刻衣服的质感会被看得很清楚。",
        "benefit": ["光下有细腻的光泽", "抬手举杯时线条很顺", "不抢戏但很有存在感"],
    },
    "松弛感": {
        "hook": ["松弛感不是穿得随便", "周末出门只想套一件", "衣柜里那件越穿越软的衣服"],
        "scene": "周末的咖啡店、楼下的超市、临时决定的一场散步。",
        "benefit": ["版型松但不塌", "越穿越贴合自己的身形", "随便一搭就很好看"],
    },
    "旅行度假": {
        "hook": ["行李箱里只带这一件", "海边风很大也不狼狈", "拍照不用修图的那种好看"],
        "scene": "高铁、机场、海边的风，一天换三个场景，衣服要跟得上。",
        "benefit": ["抗皱，塞箱子也不怕", "轻薄但挡风", "上镜不挑背景"],
    },
    "职场面试": {
        "hook": ["面试别穿得像卖保险", "拿到 offer 那天我穿的这件", "HR 说看着就很靠谱"],
        "scene": "提前二十分钟到楼下，整理领口，深呼吸，走进会议室。",
        "benefit": ["正式但不死板", "坐着不勒肚子", "细节经得起近距离看"],
    },
}

CATEGORY_FABRIC = {
    "针织衫": "精纺羊毛",
    "大衣": "双面呢",
    "衬衫": "真丝混纺",
    "半裙": "醋酸",
    "连衣裙": "天丝",
    "西装": "羊毛混纺",
    "包袋": "头层牛皮",
}

PROMPT_BAD_WORDS = ["最", "第一", "绝对", "100%", "顶级", "唯一", "永久", "根治", "国家级", "全网"]


def _stable_rng(*parts: object) -> random.Random:
    """用内容做种子：同样的输入永远得到同样的「随机」结果，方便复现和测试。"""
    digest = hashlib.md5("|".join(map(str, parts)).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:8], 16))


def _pseudo_embedding(text: str, dim: int = 128) -> list[float]:
    """字符二元组的 hash 投影，得到一个「够用」的向量。

    不是真 embedding，但能体现向量检索的形态：字面越接近，余弦相似度越高。
    简历里要老实写「离线环境用 hash 向量兜底，生产接真实 embedding 模型」。
    """
    vec = [0.0] * dim
    for i in range(len(text) - 1):
        gram = text[i : i + 2]
        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, fail_rate: float = 0.02, fence_rate: float = 0.15, seed: int = 7):
        self.fail_rate = fail_rate
        self.fence_rate = fence_rate
        self._rng = random.Random(seed)

    async def chat(self, messages: list[dict], *, temperature: float = 0.7, json_mode: bool = False) -> LLMResult:
        await asyncio.sleep(0.05)  # 模拟网络耗时，让 UI 的进度条真的会动
        if self._rng.random() < self.fail_rate:
            raise TimeoutError("mock 模拟上游超时")

        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        user = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        prompt = system + "\n" + user

        # 顺序很重要：判断「角色」要看 system 提示词的身份，而不是全文关键词，
        # 否则 Writer 的 system 里也出现「评分要求」就会被误判成评委（真实踩过的坑）。
        if "内容审核编辑" in system or "critic" in system.lower():
            payload = self._critic(user)
        elif "质量评委" in system or "judge" in system.lower():
            payload = self._judge(user)
        else:
            payload = self._write(user)

        text = json.dumps(payload, ensure_ascii=False)
        if json_mode and self._rng.random() < self.fence_rate:
            # 故意包一层围栏，验证 extract_json 的兜底能力
            text = "好的，以下是结果：\n```json\n" + text + "\n```"

        return LLMResult(
            text=text,
            tokens_in=approx_tokens(prompt),
            tokens_out=approx_tokens(text),
            model="mock-writer-v1",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_pseudo_embedding(t) for t in texts]

    # ---------------- 三个「角色」的模拟实现 ----------------

    def _write(self, prompt: str) -> dict:
        product = _pick(prompt, r"商品[:：]\s*(.+?)\n") or "设计师联名单品"
        category = _pick(prompt, r"品类[:：]\s*(.+?)\n") or "女装"
        fabric = _pick(prompt, r"材质[:：]\s*(.+?)\n") or CATEGORY_FABRIC.get(category, "羊毛混纺")
        highlights = _pick(prompt, r"卖点[:：]\s*(.+?)\n") or "版型利落、垂坠感好"
        price = _pick(prompt, r"价格带[:：]\s*(.+?)\n") or "中高价位"
        angle = _pick(prompt, r"角度[:：]\s*(.+?)\n") or "通勤场景"
        variant = _pick(prompt, r"变体[:：]\s*(.+?)\n") or "1"
        feedback = _pick(prompt, r"反馈[:：]\s*(.+?)\n") or ""

        cfg = ANGLES.get(angle, ANGLES["通勤场景"])
        rng = _stable_rng(product, angle, variant, feedback)
        hook = rng.choice(cfg["hook"])
        benefit = rng.sample(cfg["benefit"], k=min(2, len(cfg["benefit"])))
        tag = rng.choice(["通勤穿搭", "设计感", "高级感", "材质控", "秋冬穿搭", "显瘦穿搭"])
        detail = highlights.strip().replace("\n", "、")[:40] or "剪裁干净、面料有筋骨"
        v = int(variant) if str(variant).isdigit() else 0
        title_candidates = [
            f"{hook}｜{category}里的体面感",
            f"{hook}，这件{category}我穿了两周",
            f"把{angle}穿明白，一件{category}就够了",
        ]
        title = rng.choice(title_candidates)

        # 三个变体走三种写法：场景切入 / 使用两周后回访 / 材质细节特写。
        # 只换标题不换正文，演示时一眼就被看穿是模板，所以正文结构也要变。
        if v % 3 == 1:
            body = (
                f"这件{product}我差不多穿了两周，说几个真实感受。\n\n"
                f"先说面料：{fabric}，{detail}。上身有分量，但不是那种压肩的沉。\n"
                f"再说场景：{cfg['scene']}\n\n"
                f"① {benefit[0]}\n② {benefit[1]}\n\n"
                f"价格在{price}，属于「少买两件快时尚就能换一件」的账。\n"
                f"我一般配直筒裤和乐福鞋，不用想太多。"
            )
        elif v % 3 == 2:
            body = (
                f"挑{category}这件事，我最近只盯三个细节。\n\n"
                f"一是面料，{fabric}，{detail}，光线下能看出纹理；\n"
                f"二是版型，肩线和袖长对了，整个人就立住了；\n"
                f"三是场景，{cfg['scene']}它都能接住。\n\n"
                f"这件{product}三点都占，价格在{price}。\n"
                f"{benefit[0]}，这是我最在意的一点。"
            )
        else:
            body = (
                f"{cfg['scene']}\n\n"
                f"这件{product}是我最近出门率最高的：{fabric}上身是有分量的，但不压肩，"
                f"光线好的时候能看出织物的纹理。\n\n"
                f"① {detail}\n"
                f"② {benefit[0]}\n"
                f"③ {benefit[1]}\n\n"
                f"价格在{price}，不算便宜，但它是那种「穿三年还在穿」的类型。\n"
                f"配色我选的是低饱和的，配牛仔裤、半裙都稳。"
            )
        if not feedback:
            # 4% 概率「手滑」写出违禁词，用来触发规则拦截 + 重写闭环
            if rng.random() < 0.04:
                body += f"\n\n（说真的，这是今年最显气质的{category}，没有之一。）"

        tags = rng.sample(
            ["#设计师品牌", "#通勤穿搭", "#OOTD", "#高级感穿搭", "#秋冬穿搭", "#小红书穿搭", "#质感生活", "#Fabrique"],
            k=rng.randint(3, 6),
        )
        tags = list(dict.fromkeys([f"#{tag}", *tags]))[:8]

        image_prompt = (
            f"{product}，{fabric}材质，{angle}，自然光，浅米色背景，"
            f"35mm 胶片质感，低饱和，留白构图，突出{fabric}的纹理与版型，4:5"
        )
        return {"title": title, "body": body, "tags": tags, "image_prompt": image_prompt}

    def _judge(self, prompt: str) -> dict:
        """从 prompt 里把待评内容抠出来，按四维打分。

        真实场景是模型读文本打分；这里用可解释的代理指标模拟：
        违禁词扣合规分、有细节扣信息密度、有场景词扣平台适配、标签数量影响适配。
        """
        content = _pick(prompt, r"待评内容[:：]\s*(.+?)\n\s*评分要求", flags=re.S) or prompt
        content = content.strip()[:1500]
        words = [w for w in PROMPT_BAD_WORDS if w in content]
        has_scene = any(k in content for k in ["早八", "地铁", "咖啡", "周末", "海边", "会议室", "餐厅", "工位"])
        has_detail = sum(1 for k in ["面料", "版型", "剪裁", "垂坠", "纹理", "走线", "光泽"] if k in content)
        tags = re.findall(r"#\S+", content)
        length_ok = 120 <= len(content) <= 900

        tone = 84 - 9 * len(words) + (3 if has_detail >= 2 else 0)
        fit = 80 + (5 if has_scene else -8) + (3 if 3 <= len(tags) <= 8 else -12)
        density = 74 + 6 * has_detail + (4 if length_ok else -10)
        safe = 95 - 22 * len(words)

        scores = {
            "brand_tone": _clamp(tone),
            "platform_fit": _clamp(fit),
            "info_density": _clamp(density),
            "compliance": _clamp(safe),
        }
        reasons = {
            "brand_tone": "有材质与剪裁的具体描述，语气克制，没有硬推销。" if tone >= 78 else "出现绝对化表述，削弱了品牌克制的调性。",
            "platform_fit": "场景切入具体，符合小红书「先共鸣后种草」的阅读习惯。" if fit >= 78 else "缺少可代入的使用场景，更像详情页文案。",
            "info_density": "给了材质、搭配、价格三类有效信息。" if density >= 72 else "信息偏空，形容词多、事实少。",
            "compliance": "未发现绝对化用语与导流风险。" if not words else f"命中风险词：{'、'.join(words)}。",
        }
        total = (
            scores["brand_tone"] * 0.3
            + scores["platform_fit"] * 0.3
            + scores["info_density"] * 0.2
            + scores["compliance"] * 0.2
        )
        return {
            "scores": scores,
            "reasons": reasons,
            "total": round(total, 1),
            "verdict": "pass" if total >= 78 and not words else "rewrite",
        }

    def _critic(self, prompt: str) -> dict:
        content = _pick(prompt, r"待审内容[:：]\s*(.+?)\n\s*审核规则", flags=re.S) or prompt
        hits = [
            {"word": w, "suggest": "换个不带绝对化含义的表述"}
            for w in PROMPT_BAD_WORDS
            if w in content
        ]
        return {"violations": hits, "pass": not hits, "note": "规则审核完成" if not hits else f"命中 {len(hits)} 处风险"}


def _pick(prompt: str, pattern: str, flags: int = 0) -> str:
    m = re.search(pattern, prompt, flags)
    return m.group(1).strip() if m else ""


def _clamp(v: float, lo: int = 40, hi: int = 98) -> int:
    return int(max(lo, min(hi, v)))
