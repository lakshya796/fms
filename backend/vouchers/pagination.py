from rest_framework.pagination import PageNumberPagination


class VoucherPagination(PageNumberPagination):
    """The voucher list is meant to be browsed as a whole working set, not paged
    through by default, so a caller can ask for a larger page explicitly."""
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 1000
