"""Create an order for a model from its page, and (re)send an order's invoice."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import need, Principal
from ..clients.base import ApiError
from ..db import get_db
from ..models import Model, Order
from ..services import manual_orders
from .farm import _actor

router = APIRouter(prefix="/api")


@router.get("/models/{model_id}/order-price")
def order_price(model_id: int, quantity: int = 1, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    return manual_orders.suggest(db, m, quantity)


class Customer(BaseModel):
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)


class Address(BaseModel):
    address_1: str = ""
    address_2: str = ""
    city: str = ""
    state: str = ""
    postcode: str = ""
    country: str = ""


class OrderIn(BaseModel):
    customer: Customer
    address: Address | None = None
    quantity: int = 1
    total: float
    fulfilment: str = "pickup"          # pickup | ship
    shipping_cost: float = 0
    due_date: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    send_invoice: bool = True
    print_now: bool = False


@router.post("/models/{model_id}/order")
def create_order(model_id: int, body: OrderIn, db: Session = Depends(get_db), who: Principal = Depends(need("store"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    try:
        out = manual_orders.create(db, m, body.model_dump(), _actor(who))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ApiError as e:
        raise HTTPException(502, f"WooCommerce said: {getattr(e, 'message', None) or e}")
    db.commit()
    return out


@router.post("/orders/{order_id}/send-invoice")
def resend_invoice(order_id: int, db: Session = Depends(get_db), who: Principal = Depends(need("store"))):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    try:
        out = manual_orders.send_invoice(db, o, _actor(who))
    except ApiError as e:
        raise HTTPException(502, f"WooCommerce said: {getattr(e, 'message', None) or e}")
    db.commit()
    return out
