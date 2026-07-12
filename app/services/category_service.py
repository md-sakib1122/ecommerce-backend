"""OOP business logic for the category tree.

`CategoryService` owns the self-referential `categories` adjacency list:

- CRUD with tree-integrity guards — the parent must exist, a category can't be
  moved under itself or a descendant (cycle guard), and a non-empty category
  (children or products) can't be deleted.
- A **DFS** traversal that renders the whole tree and caches it in Redis,
  invalidated on any category write.
- DFS-backed product discovery: branch listing (`GET /categories/{id}/products`)
  and related-product recommendations (`GET /products/{id}/recommendations`),
  both of which collect a subtree of category ids with one indexed query.
"""
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.cache.redis_client import (
    cache_delete_pattern,
    cache_get_json,
    cache_set_json,
)
from app.core.config import settings
from app.models.category import Category
from app.models.product import Product, ProductStatus
from app.schemas.category import CategoryCreate, CategoryTreeNode, CategoryUpdate

_CACHE_KEY_PREFIX = "category:tree"


class CategoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- lookups -------------------------------------------------------------
    async def get_by_id(self, category_id: int) -> Category | None:
        return await self.session.get(Category, category_id)

    async def get_category_or_404(self, category_id: int) -> Category:
        category = await self.get_by_id(category_id)
        if category is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {category_id} not found.",
            )
        return category

    async def list_categories(self, skip: int = 0, limit: int = 100) -> list[Category]:
        result = await self.session.execute(
            select(Category).order_by(Category.id).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    # --- in-memory index + DFS ----------------------------------------------
    async def _build_index(
        self,
    ) -> tuple[dict[int, Category], dict[int | None, list[Category]]]:
        """Load every category in ONE query and build adjacency maps.

        Returns ``(by_id, children_by_parent)`` where ``children_by_parent[None]``
        holds the root categories. Children are ordered by id so the DFS is
        deterministic. This avoids the N+1 that lazy-loading `Category.children`
        one level at a time would cause.
        """
        result = await self.session.execute(select(Category).order_by(Category.id))
        by_id: dict[int, Category] = {}
        children_by_parent: dict[int | None, list[Category]] = {}
        for category in result.scalars().all():
            by_id[category.id] = category
            children_by_parent.setdefault(category.parent_id, []).append(category)
        return by_id, children_by_parent

    def _dfs_tree(
        self,
        parent_id: int | None,
        children_by_parent: dict[int | None, list[Category]],
        visited: set[int] | None = None,
    ) -> list[CategoryTreeNode]:
        """Depth-first build of the nested tree under `parent_id`.

        `visited` guards against pathological cycles in the data (writes are
        cycle-checked, so this is belt-and-suspenders).
        """
        if visited is None:
            visited = set()
        nodes: list[CategoryTreeNode] = []
        for category in children_by_parent.get(parent_id, []):
            if category.id in visited:
                continue
            visited.add(category.id)
            nodes.append(
                CategoryTreeNode(
                    id=category.id,
                    name=category.name,
                    parent_id=category.parent_id,
                    children=self._dfs_tree(category.id, children_by_parent, visited),
                )
            )
        return nodes

    def _dfs_ids(
        self,
        root_id: int,
        children_by_parent: dict[int | None, list[Category]],
    ) -> list[int]:
        """Iterative depth-first collect of `root_id` + all descendant ids."""
        ids: list[int] = []
        visited: set[int] = set()
        stack: list[int] = [root_id]
        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            ids.append(node_id)
            # Reversed so ascending-id children are popped left-to-right.
            for child in reversed(children_by_parent.get(node_id, [])):
                if child.id not in visited:
                    stack.append(child.id)
        return ids

    # --- cached tree endpoint ------------------------------------------------
    async def get_tree(self, root_id: int | None = None) -> list[CategoryTreeNode]:
        """DFS-render the category tree, cached in Redis.

        With no `root_id`, returns the forest of every root category; with a
        `root_id`, returns that single subtree as a one-element list. Cached
        under ``category:tree:{all|root_id}`` and invalidated on any write.
        """
        cache_key = f"{_CACHE_KEY_PREFIX}:{'all' if root_id is None else root_id}"
        cached = await cache_get_json(cache_key)
        if cached is not None:
            return [CategoryTreeNode.model_validate(node) for node in cached]

        by_id, children_by_parent = await self._build_index()
        if root_id is None:
            tree = self._dfs_tree(None, children_by_parent)
        else:
            if root_id not in by_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category {root_id} not found.",
                )
            root = by_id[root_id]
            tree = [
                CategoryTreeNode(
                    id=root.id,
                    name=root.name,
                    parent_id=root.parent_id,
                    children=self._dfs_tree(
                        root.id, children_by_parent, visited={root.id}
                    ),
                )
            ]

        await cache_set_json(
            cache_key,
            [node.model_dump() for node in tree],
            settings.CATEGORY_CACHE_TTL_SECONDS,
        )
        return tree

    # --- DFS-backed product discovery ---------------------------------------
    async def list_products_in_branch(
        self, category_id: int, skip: int = 0, limit: int = 100
    ) -> list[Product]:
        """Active products in `category_id` and every descendant category."""
        await self.get_category_or_404(category_id)
        _, children_by_parent = await self._build_index()
        ids = self._dfs_ids(category_id, children_by_parent)
        result = await self.session.execute(
            select(Product)
            .where(
                Product.category_id.in_(ids),
                Product.status == ProductStatus.active,
            )
            .order_by(Product.id)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def recommend_for_product(
        self, product_id: int, limit: int = 10
    ) -> list[Product]:
        """Related products for a product, via DFS over its category branch.

        Climb to the product's parent category and DFS that whole subtree
        (siblings + cousins + descendants); fall back to the product's own
        category subtree when it sits at a root. The product itself is excluded.
        Returns ``[]`` when the product has no category.
        """
        product = await self.session.get(Product, product_id)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found.",
            )
        if product.category_id is None:
            return []

        by_id, children_by_parent = await self._build_index()
        category = by_id.get(product.category_id)
        if category is not None and category.parent_id is not None:
            scope_root = category.parent_id
        else:
            scope_root = product.category_id

        ids = self._dfs_ids(scope_root, children_by_parent)
        result = await self.session.execute(
            select(Product)
            .where(
                Product.category_id.in_(ids),
                Product.status == ProductStatus.active,
                Product.id != product_id,
            )
            .order_by(Product.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- writes (admin) ------------------------------------------------------
    async def create(self, data: CategoryCreate) -> Category:
        if data.parent_id is not None:
            await self._require_parent_exists(data.parent_id)
        category = Category(name=data.name, parent_id=data.parent_id)
        self.session.add(category)
        await self.session.commit()
        await self.session.refresh(category)
        await self._invalidate_cache()
        return category

    async def update(self, category_id: int, data: CategoryUpdate) -> Category:
        category = await self.get_category_or_404(category_id)
        fields = data.model_dump(exclude_unset=True)

        if "parent_id" in fields:
            await self._validate_new_parent(category_id, fields["parent_id"])
            category.parent_id = fields["parent_id"]
        if fields.get("name") is not None:
            category.name = fields["name"]

        self.session.add(category)
        await self.session.commit()
        await self.session.refresh(category)
        await self._invalidate_cache()
        return category

    async def delete(self, category_id: int) -> None:
        """Delete a category. Blocked (409) if it still has children or products."""
        category = await self.get_category_or_404(category_id)

        if await self._count(Category, Category.parent_id == category_id) > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Category has subcategories; reassign or delete them first."
                ),
            )
        if await self._count(Product, Product.category_id == category_id) > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Category has products; reassign them first.",
            )

        await self.session.delete(category)
        await self.session.commit()
        await self._invalidate_cache()

    # --- internals -----------------------------------------------------------
    async def _count(self, model: type, condition) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(model).where(condition)
        )
        return int(result.scalar_one())

    async def _require_parent_exists(self, parent_id: int) -> None:
        if await self.get_by_id(parent_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Parent category {parent_id} does not exist.",
            )

    async def _validate_new_parent(
        self, category_id: int, new_parent_id: int | None
    ) -> None:
        if new_parent_id is None:  # re-parenting to a root is always fine
            return
        if new_parent_id == category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A category cannot be its own parent.",
            )
        await self._require_parent_exists(new_parent_id)
        _, children_by_parent = await self._build_index()
        if new_parent_id in set(self._dfs_ids(category_id, children_by_parent)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot move category {category_id} under its own descendant "
                    f"{new_parent_id} (would create a cycle)."
                ),
            )

    async def _invalidate_cache(self) -> None:
        await cache_delete_pattern(f"{_CACHE_KEY_PREFIX}*")
