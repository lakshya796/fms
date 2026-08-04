from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Default paging with an opt-in larger page for board and dashboard views.

    Console screens such as the orders board and the compliance watchlist render a
    whole working set at once rather than page through it, so they ask for a bigger
    page explicitly with `?page_size=`. The cap keeps a stray request from pulling
    the entire table.
    """
    page_size_query_param = "page_size"
    max_page_size = 500
