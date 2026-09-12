from pydantic import BaseModel


class Page[T](BaseModel):
    """The list envelope every collection endpoint returns (spec §3.5).

    One shape, defined once, so the frontend's pagination components target it
    directly instead of learning a new response per endpoint.

    PEP 695 type-parameter syntax rather than `Generic[T]`: the project is
    pinned to 3.12 and ruff's target-version is py312, so UP046 rejects the
    older spelling and `ruff check .` — a CI gate — fails on it.
    """

    data: list[T]
    total: int
    page: int
    limit: int
    has_more: bool

    @classmethod
    def of(cls, data: list[T], total: int, page: int, limit: int) -> "Page[T]":
        return cls(
            data=data,
            total=total,
            page=page,
            limit=limit,
            has_more=(page * limit) < total,
        )
