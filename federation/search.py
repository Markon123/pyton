"""Portable Unicode search over role-scoped registry querysets.

SQLite's built-in LIKE does not case-fold Cyrillic. Normalize the searchable
text explicitly, then return a queryset so callers keep their permissions,
filters, ordering and pagination. Read scalar rows in batches to avoid N+1.
"""
import unicodedata


def normalized(value):
    return unicodedata.normalize('NFKC', str(value or '')).casefold()


def _search(queryset, query, fields):
    tokens = normalized(query).split()
    if not tokens:
        return queryset
    ids = set()
    for row in queryset.values_list('pk', *fields).iterator(chunk_size=1000):
        text = normalized(' '.join(str(value or '') for value in row[1:]))
        if all(token in text for token in tokens):
            ids.add(row[0])
    return queryset.filter(pk__in=ids)


def search_profiles(queryset, query):
    return _search(queryset, query, (
        'user__first_name', 'user__last_name', 'user__username',
        'municipality', 'organization__name',
    ))


def search_organizations(queryset, query):
    return _search(queryset, query, ('name', 'kind', 'municipality'))
