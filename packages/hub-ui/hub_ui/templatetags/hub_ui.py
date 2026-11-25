from django import template

from hub_ui.formatting import format_number_vn, format_vnd

register = template.Library()

# `{{ value|format_vnd }}` -> 1.000.000
register.filter("format_vnd", format_vnd)


@register.filter
def vn_number(value, decimal_places=0):
    """Vietnamese number format, e.g. 1.234,56."""
    try:
        places = int(decimal_places)
    except (TypeError, ValueError):
        places = 0
    return format_number_vn(value, decimal_places=places)


@register.simple_tag(takes_context=True)
def query_replace(context, param, value):
    """Current query string with ``param`` set to ``value`` (other filters kept).

    For pagination links: ``?{% query_replace "page" 2 %}``.
    """
    query = context["request"].GET.copy()
    query[param] = value
    return query.urlencode()
