"""造一批像样的商品数据。

为什么要「像样」：演示时如果商品是「测试商品 1、测试商品 2」，面试官一眼看出是玩具；
用真实的品类、材质、卖点，生成出来的文案才有说服力，也方便讲「业务理解」。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db, session_scope  # noqa: E402
from app.models import Product  # noqa: E402

PRODUCTS = [
    dict(name="落肩双面呢短大衣", category="大衣", fabric="羊毛双面呢", highlights="落肩版型、手工锁边、不压肩、可拆腰带", price_band="3000-4000"),
    dict(name="精纺羊毛圆领针织衫", category="针织衫", fabric="精纺羊毛", highlights="12 针细腻、不扎皮肤、领口挺括、可机洗", price_band="800-1200"),
    dict(name="真丝混纺垂坠衬衫", category="衬衫", fabric="真丝混纺", highlights="哑光光泽、不透、法式袖口、微落肩", price_band="1200-1600"),
    dict(name="醋酸高腰直筒半裙", category="半裙", fabric="醋酸混纺", highlights="高腰收腹、垂坠不起静电、侧隐形拉链、及膝", price_band="900-1300"),
    dict(name="天丝收腰衬衫连衣裙", category="连衣裙", fabric="天丝", highlights="收腰显比例、可拆腰带、垂坠感、通勤可穿", price_band="1500-2000"),
    dict(name="廓形羊毛西装外套", category="西装", fabric="羊毛混纺", highlights="垫肩不夸张、双排扣、内里全衬、可通勤", price_band="2200-2800"),
    dict(name="Soft Rialto 软皮手提包", category="包袋", fabric="头层牛皮", highlights="自重轻、可放 13 寸电脑、磁扣开合、可调肩带", price_band="2500-3200"),
    dict(name="羊绒混纺围巾", category="配饰", fabric="羊绒混纺", highlights="双面提花、不扎脖、宽度可当披肩", price_band="600-900"),
    dict(name="复古水洗直筒牛仔裤", category="裤装", fabric="棉弹牛仔", highlights="高腰、不卡裆、水洗做旧、九分长度", price_band="700-1000"),
    dict(name="羊毛针织开衫", category="针织衫", fabric="美丽诺羊毛", highlights="V 领显颈线、扣子可全解、内搭外穿都行", price_band="1000-1400"),
    dict(name="缎面吊带连衣裙", category="连衣裙", fabric="醋酸缎", highlights="斜裁、垂坠、可叠穿、肩带可调", price_band="1600-2200"),
    dict(name="结构感腋下包", category="包袋", fabric="小牛皮", highlights="硬挺有型、可放手机口红钥匙、金属扣", price_band="1800-2400"),
]


def main() -> None:
    init_db()
    with session_scope() as db:
        existing = {p.name for p in db.query(Product).all()}
        added = 0
        for item in PRODUCTS:
            if item["name"] in existing:
                continue
            db.add(Product(**item))
            added += 1
    print(f"[seed] 新增 {added} 个商品，当前共 {len(PRODUCTS)} 个")


if __name__ == "__main__":
    main()
