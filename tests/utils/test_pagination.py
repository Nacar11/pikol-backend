from src.utils.pagination import Page


def test_has_more_is_true_when_further_pages_exist() -> None:
    page = Page.of(data=[1, 2, 3], total=10, page=1, limit=3)

    assert page.has_more is True


def test_has_more_is_false_on_the_last_partial_page() -> None:
    page = Page.of(data=[10], total=10, page=4, limit=3)

    assert page.has_more is False


def test_has_more_is_false_when_the_page_exactly_ends_the_set() -> None:
    """The off-by-one that makes a UI render an empty final page."""
    page = Page.of(data=[1, 2, 3], total=3, page=1, limit=3)

    assert page.has_more is False


def test_empty_result_set() -> None:
    page = Page.of(data=[], total=0, page=1, limit=20)

    assert page.has_more is False
    assert page.total == 0
