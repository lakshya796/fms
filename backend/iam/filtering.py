"""Shared query-string filtering for the API.

Every list endpoint accepts `?field=value` filters, a `?search=` term and, where
it makes sense, a `?from=`/`?to=` date window. Keeping it in one place means a
fix like boolean coercion lands everywhere at once.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework.exceptions import ValidationError

BOOLEAN_STRINGS = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}


def coerce(value):
    """Query strings arrive as text, and Django's BooleanField rejects a lowercase 'true'."""
    return BOOLEAN_STRINGS.get(str(value).lower(), value)


def apply_filters(queryset, params, filter_fields=(), search_fields=(), date_field=None):
    try:
        for field in filter_fields:
            value = params.get(field)
            if value not in (None, ""):
                queryset = queryset.filter(**{field: coerce(value)})
        if date_field:
            for name, lookup in (("from", "gte"), ("to", "lte")):
                value = params.get(name)
                if value:
                    queryset = queryset.filter(**{f"{date_field}__{lookup}": value})
        term = params.get("search")
        if term and search_fields:
            match = Q()
            for field in search_fields:
                match |= Q(**{f"{field}__icontains": term})
            queryset = queryset.filter(match)
        # Force evaluation of the lookups so a bad value fails here as a 400
        # rather than blowing up later during rendering.
        queryset.query.get_compiler(queryset.db).as_sql()
        return queryset
    except (DjangoValidationError, ValueError, TypeError) as error:
        raise ValidationError(f"Invalid filter value: {error}")
