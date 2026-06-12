from __future__ import annotations

import random
from typing import Any


_PUNCTUATION_OR_SPACE = set(".,!?;:，。！？；：、 ")


async def human_type(page: Any, text: str, cfg: Any) -> None:
    base_delay = 1.0 / max(float(cfg.type_cps_mean), 1.0)
    jitter = max(float(cfg.type_cps_jitter), 0.0)

    for character in text:
        await page.keyboard.type(character)
        delay = max(0.01, random.gauss(base_delay, base_delay * jitter))
        if character in _PUNCTUATION_OR_SPACE and random.random() < 0.65:
            delay += random.uniform(0.05, 0.2)
        await page.wait_for_timeout(max(1, int(delay * 1000)))
