import bleach

ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'u', 's',
    'table', 'thead', 'tbody', 'tr', 'td', 'th',
    'img', 'span', 'div',
    'svg', 'path', 'rect', 'circle', 'line', 'polyline', 'polygon',
    'g', 'defs', 'symbol', 'use',
    'math', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'mfrac', 'msqrt', 'mroot', 'mtable', 'mtr', 'mtd',
]

ALLOWED_ATTRIBUTES = {
    '*': ['dir', 'style', 'class'],
    'img': ['src', 'alt', 'width', 'height'],
    'svg': ['xmlns', 'width', 'height', 'viewBox'],
    'path': ['d', 'fill', 'stroke', 'stroke-width'],
    'rect': ['x', 'y', 'width', 'height', 'fill', 'stroke', 'rx', 'ry'],
    'circle': ['cx', 'cy', 'r', 'fill', 'stroke'],
    'line': ['x1', 'y1', 'x2', 'y2', 'stroke', 'stroke-width'],
    'use': ['href', 'xlink:href'],
}

def clean_html(raw_html: str) -> str:
    """Sanitize HTML to prevent XSS."""
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
        protocols=['http', 'https', 'data'],
    )