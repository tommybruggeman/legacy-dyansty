from dataclasses import dataclass


@dataclass
class Evidence:

    category: str

    importance: float

    statement: str

    source: str

    value: float | None = None
