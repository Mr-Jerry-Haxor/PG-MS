import re


def names_from_email(email: str) -> tuple[str, str]:
    """Derive a best-effort (first_name, last_name) from an email address.

    Examples:
    - john.doe@example.com -> ("John", "Doe")
    - jane_doe-smith@domain -> ("Jane", "Doe Smith")
    - user123@domain -> ("User123", "")
    """
    if not email or '@' not in email:
        return "", ""
    local = email.split('@', 1)[0]
    parts = [p for p in re.split(r"[._-]+", local) if p]
    if not parts:
        return "", ""
    first = parts[0].capitalize()
    last = " ".join(p.capitalize() for p in parts[1:]) if len(parts) > 1 else ""
    return first, last
