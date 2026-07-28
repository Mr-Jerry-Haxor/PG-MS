from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template


register = template.Library()


def format_indian_number(value, decimal_places=2):
    """Format a numeric value with Indian digit grouping."""
    try:
        places = max(0, min(int(decimal_places), 4))
    except (TypeError, ValueError):
        places = 2

    try:
        number = Decimal(str(value if value not in (None, '') else 0))
    except (InvalidOperation, TypeError, ValueError):
        return value

    quantum = Decimal(1).scaleb(-places)
    number = number.quantize(quantum, rounding=ROUND_HALF_UP)
    sign = '-' if number < 0 else ''
    fixed = f"{abs(number):.{places}f}"
    integer, separator, fraction = fixed.partition('.')

    if len(integer) > 3:
        last_three = integer[-3:]
        leading = integer[:-3]
        pairs = []
        while leading:
            pairs.append(leading[-2:])
            leading = leading[:-2]
        integer = ','.join(reversed(pairs)) + ',' + last_three

    return f"{sign}{integer}{separator}{fraction}" if places else f"{sign}{integer}"


@register.filter(name='indian_currency')
def indian_currency(value, decimal_places=2):
    return format_indian_number(value, decimal_places)
