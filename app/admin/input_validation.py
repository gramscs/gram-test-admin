"""Validation shared by company master editing."""

import re
from flask import request

EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")


def payload():
    data = request.get_json(silent=True) if request.is_json else None
    if not isinstance(data, dict):
        raise ValueError("Send a JSON object.")
    return data


def clean_text(data, field, maximum, required=False, default=""):
    value = data.get(field, default)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{field.replace('_', ' ').title()} must be text.")
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValueError(f"{field.replace('_', ' ').title()} must contain {'1–' if required else 'at most '}{maximum} characters.")
    return value


def email_address(value, required=False):
    if (not value and required) or (value and (len(value) > 254 or not EMAIL.fullmatch(value) or '..' in value)):
        raise ValueError("Enter a valid email address.")
    return value


def pincode(value):
    if value and not re.fullmatch(r"[0-9]{6}", value):
        raise ValueError("Pincode must contain six digits or be blank.")
    return value


def positive_id(value):
    from app.orm import record_id
    return record_id(value)


def selected_ids(value, maximum=500):
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ValueError(f"Select between 1 and {maximum} records.")
    return list(dict.fromkeys(positive_id(item) for item in value))
