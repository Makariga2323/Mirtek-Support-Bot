import json
import os
from typing import List, Dict, Any
from .models import MockData, Product, Warehouse, Store, StockItem
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

class DataManager:
    def __init__(self, data_path: str = "data/MOCK_DATA.json", chroma_path: str = "data/chroma"):
        with open(data_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        
        self.data = MockData(**raw_data)
        self.embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-base")
        self.chroma_path = chroma_path
        self.vectorstore = None
        self._build_aliases()

    def _build_aliases(self):
        self.aliases_map = {}
        for product in self.data.catalog["products"]:
            for alias in product.aliases:
                self.aliases_map[alias.lower()] = product.article
            self.aliases_map[product.name.lower()] = product.article

    def get_available_stock(self, article: str, store_id: str = None) -> List[Dict[str, Any]]:
        results = []
        for wh in self.data.warehouses["warehouses"]:
            if store_id and wh.store_id != store_id:
                continue
            for stock in wh.stock:
                if stock.article == article:
                    available = stock.quantity - stock.reserved
                    if available > 0:
                        results.append({
                            "store_id": wh.store_id,
                            "warehouse_id": wh.warehouse_id,
                            "available": available,
                            "unit": stock.unit
                        })
        return results

    def build_vector_index(self):
        documents = []
        for product in self.data.catalog["products"]:
            text = f"passage: {product.name}. Категория: {product.category}. Описание: {product.care_instructions}. Цена: {product.price_per_unit} руб."
            doc = Document(
                page_content=text,
                metadata={"article": product.article, "type": "product"}
            )
            documents.append(doc)

        if os.path.exists(self.chroma_path):
            import shutil
            shutil.rmtree(self.chroma_path)

        self.vectorstore = Chroma.from_documents(
            documents=documents,
            embedding=self.embeddings,
            persist_directory=self.chroma_path
        )
        return self.vectorstore

    def search_products(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        if not self.vectorstore:
            self.vectorstore = Chroma(persist_directory=self.chroma_path, embedding_function=self.embeddings)
        
        query_text = f"query: {query}"
        results = self.vectorstore.similarity_search_with_score(query_text, k=k)
        
        return [{"article": doc.metadata["article"], "score": score, "content": doc.page_content} for doc, score in results]