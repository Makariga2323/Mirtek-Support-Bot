from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import date

class StockItem(BaseModel):
    article: str
    quantity: int
    unit: str
    reserved: int = 0

class Warehouse(BaseModel):
    warehouse_id: str
    store_id: Optional[str] = None
    city: str
    address: str
    type: str
    stock: List[StockItem] = []

class Store(BaseModel):
    store_id: str
    name: str
    city: str
    address: str
    working_hours: Dict[str, str]
    holidays: List[Dict[str, str]] = []
    special_schedule: List[Dict[str, str]] = []

class Product(BaseModel):
    article: str
    name: str
    aliases: List[str]
    category: str
    subcategory: str
    unit: str
    price_per_unit: float
    available_units: List[str] = []
    care_instructions: str

class Promotion(BaseModel):
    promotion_id: str
    name: str
    applicable_articles: List[str]
    applicable_stores: List[str]
    start_date: str
    end_date: str
    conditions: str

class MockData(BaseModel):
    meta: Dict[str, Any]
    catalog: Dict[str, List[Product]]
    warehouses: Dict[str, List[Warehouse]]
    stores: Dict[str, List[Store]]
    promotions: Dict[str, List[Promotion]]