"""catalog 接口桩：返回符合 schema 的示例数据，响应头 X-Stub: true。WP3 会替换为真实实现。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from xcsim.simkit.auth import require
from xcsim.simkit.stub import mark_stub

from . import schemas as s

router = APIRouter(
    prefix="/catalog/v1",
    tags=["catalog"],
    dependencies=[Depends(mark_stub), Depends(require("catalog", "catalog:read"))],
    responses={401: {"model": s.ErrorBody}, 403: {"model": s.ErrorBody}, 404: {"model": s.ErrorBody}},
)

_SKU = s.SkuInfo(
    sku_id="SKU-N12-128-BLK", spu_id="XC-PHONE-N12", spec={"容量": "128GB", "颜色": "黑色"},
    list_price="3299.00", current_price="3199.00", price_type="promo", barcode="6900000000017",
    weight_g=210, status="on_sale",
)
_SUMMARY = s.SpuSummary(
    spu_id="XC-PHONE-N12", name="星川 N12 智能手机", category_id="C-DIGITAL", brand="星川优品", kind="phone",
    is_bulky=False, is_fresh=False, is_digital=True, min_price="3199.00", status="on_sale",
)


@router.get("/spus", response_model=s.SpuListResponse, summary="商品列表（游标分页）")
def list_spus(
    q: str | None = Query(None, description="关键词"),
    category_id: str | None = Query(None, alias="categoryId"),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> s.SpuListResponse:
    return s.SpuListResponse(items=[_SUMMARY], next_cursor=None)


@router.get("/spus/{spu_id}", response_model=s.SpuDetail, summary="商品详情")
def get_spu(spu_id: str) -> s.SpuDetail:
    return s.SpuDetail(
        spu_id=spu_id, name="星川 N12 智能手机", category_id="C-DIGITAL", brand="星川优品", kind="phone",
        is_bulky=False, is_fresh=False, is_digital=True, is_custom=False, is_intimate=False,
        is_refurbished=False, requires_activation=True, has_seal=True, warranty_months=12,
        part_warranty={"battery_months": 6}, detail_aftersale_override=None, kb_doc_ids=["PR01-phone-n12"],
        ship_from="W-GZ", status="on_sale", attributes={"屏幕": "6.5 英寸", "电池": "5000mAh"}, skus=[_SKU],
    )


@router.get("/skus/{sku_id}", response_model=s.SkuDetail, summary="SKU 详情")
def get_sku(sku_id: str) -> s.SkuDetail:
    return s.SkuDetail(
        sku_id=sku_id, spu_id="XC-PHONE-N12", list_price="3299.00", current_price="3199.00",
        price_type="promo", status="on_sale",
    )


@router.get("/skus/{sku_id}/price-history", response_model=s.PriceHistoryResponse, summary="价格历史（价保依赖）")
def price_history(
    sku_id: str,
    from_: str | None = Query(None, alias="from", description="起始日期 YYYY-MM-DD"),
    to: str | None = Query(None),
) -> s.PriceHistoryResponse:
    return s.PriceHistoryResponse(
        sku_id=sku_id,
        items=[
            s.PriceHistoryItem(price="3299.00", price_type="regular", valid_from="2026-09-01T00:00:00+08:00", valid_to="2026-10-20T00:00:00+08:00"),
            s.PriceHistoryItem(price="3199.00", price_type="promo", valid_from="2026-10-20T00:00:00+08:00", valid_to=None),
        ],
    )


@router.get("/skus/{sku_id}/inventory", response_model=s.InventoryResponse, summary="库存")
def inventory(sku_id: str, province: str | None = Query(None)) -> s.InventoryResponse:
    return s.InventoryResponse(
        sku_id=sku_id, available=38,
        by_warehouse=[s.WarehouseStock(warehouse_id="W-GZ", warehouse_name="广州仓", province="广东", on_hand=40, reserved=2, available=38)],
    )
