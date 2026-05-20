"""
app/repositories/base.py
Generic async repository with common CRUD operations.
"""
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get_by_id(self, record_id: int) -> ModelT | None:
        result = await self._session.execute(select(self._model).where(self._model.id == record_id))
        return result.scalar_one_or_none()

    async def save(self, instance: ModelT) -> ModelT:
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)
        await self._session.flush()

    async def list_all(self) -> list[ModelT]:
        result = await self._session.execute(select(self._model))
        return list(result.scalars().all())
