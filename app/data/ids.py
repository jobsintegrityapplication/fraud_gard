from collections import defaultdict


class IdFactory:
    def __init__(self):
        self._seq = defaultdict(int)

    def next(self, prefix: str) -> str:
        self._seq[prefix] += 1
        return f'{prefix}{self._seq[prefix]:08d}'


def luhn_check_digit(body: str) -> str:
    total = 0
    alt = True
    for ch in reversed(body):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return str((10 - (total % 10)) % 10)


def make_imei(rng, tac8: str) -> str:
    serial = ''.join(str(int(rng.integers(0, 10))) for _ in range(6))
    body = (tac8 + serial)[:14]
    return body + luhn_check_digit(body)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    from math import radians, sin, cos, sqrt, atan2
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlmb = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(max(0.0, 1 - a)))
