from __future__ import annotations

import random
from email.utils import parseaddr


_ALLOWED_GMAIL_DOMAINS = {'gmail.com', 'googlemail.com'}


def normalize_gmail_address(address: str) -> str:
    _, parsed = parseaddr(address)
    if not parsed or '@' not in parsed:
        raise ValueError('Invalid Gmail address')

    local_part, domain = parsed.split('@', 1)
    domain = domain.lower().strip()
    if domain not in _ALLOWED_GMAIL_DOMAINS:
        raise ValueError('Only personal Gmail addresses are supported')
    if '+' in local_part:
        raise ValueError('Base Gmail address must not contain plus addressing')

    normalized_local_part = local_part.replace('.', '').lower().strip()
    if not normalized_local_part or not normalized_local_part.isalnum():
        raise ValueError('Invalid Gmail local part')

    return f'{normalized_local_part}@gmail.com'


def generate_random_gmail_alias(base_address: str, rng: random.Random | None = None) -> str:
    generator = rng or random.SystemRandom()
    canonical_address = normalize_gmail_address(base_address)
    local_part, _ = canonical_address.split('@', 1)

    randomized_local_part: list[str] = []
    for index, char in enumerate(local_part):
        randomized_local_part.append(char.upper() if generator.choice([True, False]) else char)
        if index < len(local_part) - 1 and generator.choice([True, False]):
            randomized_local_part.append('.')

    domain = generator.choice(['gmail.com', 'googlemail.com'])
    return f"{''.join(randomized_local_part)}@{domain}"
