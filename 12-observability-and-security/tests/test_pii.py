"""Seam 4: which personal data may be stored or sent to the model.

Specification:
  - emails, cards, phone numbers and DNIs are masked before they reach the
    model and before they touch disk (`[email:oculto]` and friends);
  - what is not PII (a price, an order number, a quantity) is left intact: if
    we mask too much, the agent's reply becomes useless;
  - `detect` says which types it found, so they can be alerted on and measured;
  - masking twice does not break the text (it is idempotent).
"""

import pii


def test_the_common_pii_is_masked():
    text = (
        "Soy Ana, mi mail es ana.garcia@aurora.com, llamame al +54 11 5555 1234 "
        "y la tarjeta 4111 1111 1111 1111 (DNI 30123456Z)"
    )

    masked = pii.redact(text)

    assert "ana.garcia@aurora.com" not in masked
    assert "4111 1111 1111 1111" not in masked
    assert "5555 1234" not in masked
    assert "30123456Z" not in masked
    assert "[email:oculto]" in masked
    assert "[tarjeta:oculto]" in masked
    assert "[telefono:oculto]" in masked
    assert "[dni:oculto]" in masked
    # The rest of the text stays readable: the agent needs to understand it.
    assert "Soy Ana" in masked


def test_a_card_is_masked_before_it_can_be_read_as_a_phone_number():
    """Pattern order matters: a card must not end up masked as a phone number."""
    masked = pii.redact("tarjeta 4111111111111111 ok")

    assert "[tarjeta:oculto]" in masked
    assert "telefono" not in masked


def test_business_data_is_left_alone():
    """A price, an order or a quantity are not personal data."""
    text = "El pedido A-1042 cuesta $1.299,00 y son 2 unidades, stock 15"

    assert pii.redact(text) == text
    assert pii.detect(text) == []


def test_detect_says_which_kinds_were_found():
    found = pii.detect("escribime a ana@aurora.com o llamame al 011 5555 1234")

    assert sorted(found) == ["email", "telefono"]


def test_masking_twice_does_not_double_mask():
    once = pii.redact("mi mail es ana@aurora.com")
    twice = pii.redact(once)

    assert twice == once
    assert twice == "mi mail es [email:oculto]"


def test_empty_and_odd_text_does_not_crash():
    assert pii.redact("") == ""
    assert pii.redact(None) is None
    assert pii.redact("🇦🇷 hola 👋 こんにちは مرحبا") == "🇦🇷 hola 👋 こんにちは مرحبا"
    assert pii.detect(None) == []
