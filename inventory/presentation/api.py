from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import engine, Base
from presentation.routes import health, categories, items


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE inventory_categories
                ADD COLUMN IF NOT EXISTS parent_id INTEGER NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_inventory_categories_parent_id
                ON inventory_categories(parent_id)
                """
            )
        )
        result = await conn.execute(text("SELECT COUNT(*) FROM inventory_categories"))
        count = result.scalar_one() or 0
        if count == 0:
            base_categories = [
                (1, "Рабочие станции и ноутбуки", None, 10, None),
                (2, "Ноутбуки", None, 11, 1),
                (3, "Настольные ПК", None, 12, 1),
                (4, "Моноблоки", None, 13, 1),
                (5, "Мониторы", None, 20, None),
                (6, "Периферия", None, 30, None),
                (7, "Клавиатуры", None, 31, 6),
                (8, "Мыши", None, 32, 6),
                (9, "Наушники и гарнитуры", None, 33, 6),
                (10, "Принтеры и МФУ", None, 34, 6),
                (11, "Сетевое оборудование", None, 40, None),
                (12, "Маршрутизаторы", None, 41, 11),
                (13, "Коммутаторы", None, 42, 11),
                (14, "Точки доступа Wi‑Fi", None, 43, 11),
                (15, "Мобильные устройства", None, 50, None),
                (16, "Смартфоны", None, 51, 15),
                (17, "Планшеты", None, 52, 15),
                (18, "Серверы и СХД", None, 60, None),
                (19, "Прочее оборудование", None, 90, None),
            ]
            for cid, name, description, sort_order, parent_id in base_categories:
                await conn.execute(
                    text(
                        """
                        INSERT INTO inventory_categories (
                            id,
                            name,
                            description,
                            sort_order,
                            parent_id,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            :id,
                            :name,
                            :description,
                            :sort_order,
                            :parent_id,
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": cid,
                        "name": name,
                        "description": description,
                        "sort_order": sort_order,
                        "parent_id": parent_id,
                    },
                )
            await conn.execute(
                text(
                    """
                    SELECT setval(
                        pg_get_serial_sequence('inventory_categories', 'id'),
                        (SELECT COALESCE(MAX(id), 1) FROM inventory_categories)
                    )
                    """
                )
            )
    yield


app = FastAPI(title="Kosta Inventory", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
app.include_router(categories.router)
app.include_router(items.router)

