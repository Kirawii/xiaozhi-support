"""商品服务 catalog（§3.3）。camelCase、裸 JSON、金额为元的字符串、时间 ISO +08:00。"""
from __future__ import annotations

from typing import Any

from xcsim.simkit.schema_base import CamelModel

ERROR_CODES = {
    "INVALID_CURSOR": "游标无效",
    "SPU_NOT_FOUND": "SPU 不存在",
    "SKU_NOT_FOUND": "SKU 不存在",
}


class ErrorBody(CamelModel):
    error_code: str
    message: str


class SpuSummary(CamelModel):
    spu_id: str
    name: str
    category_id: str
    brand: str
    kind: str
    is_bulky: bool
    is_fresh: bool
    is_digital: bool
    min_price: str  # "99.00"
    status: str  # on_sale / off_shelf


class SpuListResponse(CamelModel):
    items: list[SpuSummary]
    next_cursor: str | None = None


class SkuInfo(CamelModel):
    sku_id: str
    spu_id: str
    spec: dict[str, str]
    list_price: str
    current_price: str
    price_type: str  # regular/promo/flash_sale/group_buy/live_only/clearance/presale
    barcode: str
    weight_g: int
    status: str


class SpuDetail(CamelModel):
    spu_id: str
    name: str
    category_id: str
    brand: str
    kind: str
    is_bulky: bool
    is_fresh: bool
    is_digital: bool
    is_custom: bool
    is_intimate: bool
    is_refurbished: bool
    requires_activation: bool
    has_seal: bool
    warranty_months: int
    part_warranty: dict[str, Any]
    detail_aftersale_override: dict[str, Any] | None = None  # 详情页售后规则，优先于通用政策
    kb_doc_ids: list[str]
    ship_from: str
    status: str
    attributes: dict[str, str]
    skus: list[SkuInfo]


class SkuDetail(CamelModel):
    sku_id: str
    spu_id: str
    list_price: str
    current_price: str
    price_type: str
    status: str


class PriceHistoryItem(CamelModel):
    price: str
    price_type: str
    valid_from: str
    valid_to: str | None = None


class PriceHistoryResponse(CamelModel):
    sku_id: str
    items: list[PriceHistoryItem]


class WarehouseStock(CamelModel):
    warehouse_id: str
    warehouse_name: str
    province: str
    on_hand: int
    reserved: int
    available: int


class InventoryResponse(CamelModel):
    sku_id: str
    available: int
    by_warehouse: list[WarehouseStock]
