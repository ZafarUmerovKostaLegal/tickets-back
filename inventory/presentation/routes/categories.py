from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from application.use_cases import (
    CreateCategoryUseCase,
    ListCategoriesUseCase,
    GetCategoryUseCase,
    UpdateCategoryUseCase,
    DeleteCategoryUseCase,
)
from application.ports import CategoryRepositoryPort
from infrastructure.database import get_session
from infrastructure.repositories import CategoryRepository
from presentation.schemas import CategoryResponse, CategoryCreate, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["categories"])


def get_repo(session: AsyncSession = Depends(get_session)) -> CategoryRepositoryPort:
    return CategoryRepository(session)


def _to_response(c):
    return CategoryResponse(
        id=c.id,
        name=c.name,
        description=c.description,
        parent_id=c.parent_id,
        sort_order=c.sort_order,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get("", response_model=list[CategoryResponse])
async def list_categories(repo: CategoryRepositoryPort = Depends(get_repo)):
    uc = ListCategoriesUseCase(repo)
    items = await uc.execute()
    return [_to_response(c) for c in items]


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(category_id: int, repo: CategoryRepositoryPort = Depends(get_repo)):
    uc = GetCategoryUseCase(repo)
    c = await uc.execute(category_id)
    if not c:
        raise HTTPException(status_code=404, detail="Category not found")
    return _to_response(c)


@router.post("", response_model=CategoryResponse, status_code=201)
async def create_category(
    body: CategoryCreate,
    repo: CategoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = CreateCategoryUseCase(repo)
    c = await uc.execute(
        name=body.name,
        description=body.description,
        sort_order=body.sort_order,
        parent_id=body.parent_id,
    )
    await session.commit()
    return _to_response(c)


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    repo: CategoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = UpdateCategoryUseCase(repo)
    c = await uc.execute(
        category_id,
        name=body.name,
        description=body.description,
        sort_order=body.sort_order,
        parent_id=body.parent_id,
    )
    if not c:
        raise HTTPException(status_code=404, detail="Category not found")
    await session.commit()
    return _to_response(c)


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: int,
    repo: CategoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = DeleteCategoryUseCase(repo)
    ok = await uc.execute(category_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Category not found")
    await session.commit()
