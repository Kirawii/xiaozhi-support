"""商品目录：基线 7 个商品（参数见 kb/products）加 13 个覆盖特殊规则的虚构商品。

字段说明：
- kind：品类细分，规则函数用它判断“已激活的电子产品”“大家电”等；
- tags：不支持无理由的品类标签（fresh/custom/intimate/digital/mattress）；
- line：运费类别 normal / bulky / fresh；
- price_type：当前售价类型，价保用（regular / promo / flash_sale ...）。
"""
from __future__ import annotations

# (spu_id, name, category, kind, tags, line, skus[(sku_id, spec, list_price, current_price, price_type)], extra)
_RAW = [
    ("XC-EAR-SAP", "星川 SoundAir Pro 降噪耳机", "数码", "earphone", [], "normal",
     [("XC-EAR-SAP-W", "白色", 699, 699, "regular")], {"doc_id": "PR01-soundair-pro"}),
    ("XC-ROBOT-X5", "星川 CleanBot X5 扫拖机器人", "小家电", "robot_vacuum", [], "normal",
     [("XC-ROBOT-X5-STD", "标准版", 2599, 2599, "regular")], {"doc_id": "PR02-cleanbot-x5"}),
    ("XC-KITCHEN-AF55", "星川 AF55 空气炸锅", "小家电", "air_fryer", [], "normal",
     [("XC-KITCHEN-AF55-STD", "5.5L", 399, 359, "promo")], {"doc_id": "PR03-airfryer-af55"}),
    ("XC-APP-POLAR", "星川 极地系列羽绒服", "服饰", "apparel", [], "normal",
     [("XC-APP-POLAR-M-BK", "黑色 M", 899, 899, "regular"),
      ("XC-APP-POLAR-L-BK", "黑色 L", 899, 899, "regular"),
      ("XC-APP-POLAR-M-BL", "雾蓝 M", 899, 899, "regular")], {"doc_id": "PR04-down-jacket", "exchange_category": "apparel"}),
    ("XC-HOME-YM", "星川 云眠独袋弹簧床垫", "家居", "mattress", ["mattress"], "bulky",
     [("XC-HOME-YM-150", "1.5m×2.0m", 2999, 2999, "regular"),
      ("XC-HOME-YM-180", "1.8m×2.0m", 3499, 3499, "regular")], {"doc_id": "PR05-mattress-yunmian"}),
    ("XC-PHONE-N12", "星川 Nova 12 手机", "数码", "phone", [], "normal",
     [("XC-PHONE-N12-128", "8GB+128GB 星夜黑", 3299, 3299, "regular"),
      ("XC-PHONE-N12-256", "12GB+256GB 晨雾白", 3699, 3699, "regular")], {"doc_id": "PR06-nova12-phone"}),
    ("XC-FRESH-CHERRY-JJ", "星川鲜果 智利车厘子 JJ 级", "生鲜", "fruit", ["fresh"], "fresh",
     [("XC-FRESH-CHERRY-JJ-2.5", "2.5kg/盒", 199, 199, "regular")], {"doc_id": "PR07-cherries-chile"}),
    # ---- 新增：覆盖特殊规则 ----
    ("XC-PAD-S11", "星川 Slate 11 平板", "数码", "tablet", [], "normal",
     [("XC-PAD-S11-128", "8GB+128GB", 2199, 2199, "regular")], {}),
    ("XC-CAM-V3", "星川 Vista 3 运动相机", "数码", "camera", [], "normal",
     [("XC-CAM-V3-STD", "标准套装", 3000, 3000, "regular")], {}),
    ("XC-FRIDGE-F450", "星川 冷域 F450 冰箱", "大家电", "fridge", [], "bulky",
     [("XC-FRIDGE-F450-STD", "450L 十字对开门", 4299, 4299, "regular")], {}),
    ("XC-UW-SOFT", "星川 云柔无痕内衣", "贴身衣物", "underwear", ["intimate"], "normal",
     [("XC-UW-SOFT-M", "M 码 肤色", 129, 129, "regular")], {}),
    ("XC-MUG-CUSTOM", "星川 定制刻字保温杯", "定制", "custom_cup", ["custom"], "normal",
     [("XC-MUG-CUSTOM-500", "500ml 刻字款", 149, 149, "regular")], {}),
    ("XC-CARD-GAME100", "星川 游戏点卡 100 元", "数字商品", "game_card", ["digital"], "normal",
     [("XC-CARD-GAME100-E", "电子卡密", 100, 100, "regular")], {}),
    ("XC-PILLOW-MEM", "星川 记忆棉护颈枕（清仓款）", "家居", "pillow", [], "normal",
     [("XC-PILLOW-MEM-STD", "标准款", 89, 69, "clearance")],
     {"detail_aftersale_override": "本商品为清仓特卖，不支持七天无理由退货，质量问题按平台规则处理。"}),
    ("XC-SHOE-RUN", "星川 疾风跑鞋", "鞋靴", "footwear", [], "normal",
     [("XC-SHOE-RUN-42", "42 码", 459, 459, "regular"),
      ("XC-SHOE-RUN-43", "43 码", 459, 459, "regular")], {"exchange_category": "footwear"}),
    ("XC-TEE-BASIC", "星川 基础款纯棉 T 恤", "服饰", "apparel", [], "normal",
     [("XC-TEE-BASIC-M", "白色 M", 79, 79, "regular"),
      ("XC-TEE-BASIC-L", "白色 L", 79, 79, "regular")], {"exchange_category": "apparel"}),
    ("XC-SNACK-NUT", "星川 每日坚果 30 袋", "食品", "snack", [], "normal",
     [("XC-SNACK-NUT-30", "30 袋装", 119, 99.99, "flash_sale")], {}),
    ("XC-CARE-SHAMPOO", "星川 草本洗发露", "美妆个护", "shampoo", [], "normal",
     [("XC-CARE-SHAMPOO-500", "500ml", 59, 59, "regular")], {}),
    ("XC-FRESH-MANGO", "星川鲜果 海南贵妃芒", "生鲜", "fruit", ["fresh"], "fresh",
     [("XC-FRESH-MANGO-5", "5 斤装", 89, 89, "regular")], {}),
    ("XC-EAR-LITE-R", "星川 SoundAir Lite 耳机（官翻）", "数码", "earphone", [], "normal",
     [("XC-EAR-LITE-R-STD", "官翻 白色", 199, 199, "regular")], {"refurbished": True}),
]

GIFT_SKU = {"sku_id": "XC-GIFT-POUCH", "spu_id": "XC-GIFT-POUCH", "name": "星川 收纳包（赠品）", "gift_value": 59}


def _fen(yuan: float) -> int:
    return int(round(yuan * 100))


def build_catalog() -> tuple[dict, dict]:
    """返回 (spus, skus) 两个字典，金额为整数分。"""
    spus: dict[str, dict] = {}
    skus: dict[str, dict] = {}
    for spu_id, name, cat, kind, tags, line, sku_rows, extra in _RAW:
        spus[spu_id] = {
            "spu_id": spu_id, "name": name, "category": cat, "kind": kind, "tags": list(tags),
            "line": line, "kb_doc_id": extra.get("doc_id"),
            "detail_aftersale_override": extra.get("detail_aftersale_override"),
            "exchange_category": extra.get("exchange_category"),
            "refurbished": extra.get("refurbished", False),
            "sku_ids": [s[0] for s in sku_rows],
        }
        for sku_id, spec, lp, cp, ptype in sku_rows:
            skus[sku_id] = {
                "sku_id": sku_id, "spu_id": spu_id, "name": f"{name} {spec}", "spec": spec,
                "list_price": _fen(lp), "current_price": _fen(cp), "price_type": ptype, "stock": 50,
            }
    return spus, skus
