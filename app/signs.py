"""Russian road-sign catalog and movement restrictions for the schematic simulator.

Sign numbering/names follow the Russian traffic rules and GOST R 52290-2024
catalogue. This is a simulation aid, not a legal compliance certification.
"""
SIGN_CATALOG = {
    "2.1": ("Главная дорога", "priority"),
    "2.4": ("Уступите дорогу", "priority"),
    "2.5": ("Движение без остановки запрещено", "priority"),
    "2.6": ("Преимущество встречного движения", "priority"),
    "2.7": ("Преимущество перед встречным движением", "priority"),
    "3.1": ("Въезд запрещён", "prohibitory"),
    "3.2": ("Движение запрещено", "prohibitory"),
    "3.18.1": ("Поворот направо запрещён", "prohibitory"),
    "3.18.2": ("Поворот налево запрещён", "prohibitory"),
    "3.19": ("Разворот запрещён", "prohibitory"),
    "4.1.1": ("Движение прямо", "mandatory"),
    "4.1.2": ("Движение направо", "mandatory"),
    "4.1.3": ("Движение налево", "mandatory"),
    "4.1.4": ("Прямо или направо", "mandatory"),
    "4.1.5": ("Прямо или налево", "mandatory"),
    "4.1.6": ("Направо или налево", "mandatory"),
}

MANDATORY_TURNS = {
    "4.1.1": {"Прямо"},
    "4.1.2": {"Направо"},
    "4.1.3": {"Налево"},
    "4.1.4": {"Прямо", "Направо"},
    "4.1.5": {"Прямо", "Налево"},
    "4.1.6": {"Направо", "Налево"},
}


def movement_allowed_by_signs(approach, turn, signs):
    """Apply approach-mounted prohibitory/mandatory signs to one vehicle movement."""
    applicable = [sign for sign in (signs or []) if sign.get("approach") == approach]
    for sign in applicable:
        code = sign.get("code", "")
        if code in ("3.1", "3.2"):
            return False
        if code == "3.18.1" and turn == "Направо":
            return False
        if code == "3.18.2" and turn == "Налево":
            return False
        if code == "3.19" and turn == "Разворот":
            return False
        allowed = MANDATORY_TURNS.get(code)
        if allowed is not None and turn not in allowed:
            return False
    return True



def approach_priority(approach, signs):
    """Lower values are served first when the schematic conflict resolver arbitrates."""
    codes = {sign.get("code") for sign in (signs or [])
             if sign.get("approach") == approach}
    if "2.1" in codes:
        return 0
    if codes.intersection({"2.4", "2.5"}):
        return 2
    return 1
