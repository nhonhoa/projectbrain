"""Vietnamese number formatting: 1.234.567,89 (period = thousands, comma = decimal)."""


def format_number_vn(value, decimal_places=0):
    """Format a number the Vietnamese way: 1.234.567,890.

    - Thousands separator: .
    - Decimal separator: ,
    """
    if value is None:
        return "0" if decimal_places == 0 else "0," + ("0" * decimal_places)

    try:
        # Standard Python format: 1,234,567.890
        pattern = "{:,." + str(decimal_places) + "f}"
        formatted = pattern.format(float(value))

        # Swap the separators: 1,234,567.890 -> 1.234.567@890 -> 1.234.567,890
        return formatted.replace(",", "@").replace(".", ",").replace("@", ".")
    except (ValueError, TypeError):
        return str(value)


def format_vnd(value):
    """Currency without decimals. Example: 1.500.000"""
    return format_number_vn(value, decimal_places=0)
