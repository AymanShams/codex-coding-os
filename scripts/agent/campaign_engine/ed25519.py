"""Strict verification-only Ed25519 using Python's standard library.

The point decoder and group operations follow RFC 8032 sections 5.1.3,
5.1.4, and 5.1.7.  Verification is deliberately stricter than the RFC's
cofactored equation: both encoded points must be canonical, non-small-order
members of the prime-order subgroup, and the uncofactored equation must hold.
"""

from __future__ import annotations

from hashlib import sha512


__all__ = ["verify"]


_FIELD_PRIME = 2**255 - 19
_GROUP_ORDER = 2**252 + 27742317777372353535851937790883648493
_COFACTOR = 8
_CURVE_D = (-121665 * pow(121666, _FIELD_PRIME - 2, _FIELD_PRIME)) % _FIELD_PRIME
_SQRT_MINUS_ONE = pow(2, (_FIELD_PRIME - 1) // 4, _FIELD_PRIME)
_Y_MASK = (1 << 255) - 1

_Point = tuple[int, int, int, int]
_IDENTITY: _Point = (0, 1, 1, 0)


def _point_add(left: _Point, right: _Point) -> _Point:
    """Add extended-coordinate points with the complete RFC 8032 formula."""

    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    a = ((y1 - x1) * (y2 - x2)) % _FIELD_PRIME
    b = ((y1 + x1) * (y2 + x2)) % _FIELD_PRIME
    c = (2 * _CURVE_D * t1 * t2) % _FIELD_PRIME
    d = (2 * z1 * z2) % _FIELD_PRIME
    e = (b - a) % _FIELD_PRIME
    f = (d - c) % _FIELD_PRIME
    g = (d + c) % _FIELD_PRIME
    h = (b + a) % _FIELD_PRIME
    return (
        (e * f) % _FIELD_PRIME,
        (g * h) % _FIELD_PRIME,
        (f * g) % _FIELD_PRIME,
        (e * h) % _FIELD_PRIME,
    )


def _point_double(point: _Point) -> _Point:
    """Double an extended-coordinate point with the RFC 8032 formula."""

    x, y, z, _ = point
    a = (x * x) % _FIELD_PRIME
    b = (y * y) % _FIELD_PRIME
    c = (2 * z * z) % _FIELD_PRIME
    h = (a + b) % _FIELD_PRIME
    e = (h - (x + y) * (x + y)) % _FIELD_PRIME
    g = (a - b) % _FIELD_PRIME
    f = (c + g) % _FIELD_PRIME
    return (
        (e * f) % _FIELD_PRIME,
        (g * h) % _FIELD_PRIME,
        (f * g) % _FIELD_PRIME,
        (e * h) % _FIELD_PRIME,
    )


def _scalar_multiply(point: _Point, scalar: int) -> _Point:
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_double(addend)
        scalar >>= 1
    return result


def _points_equal(left: _Point, right: _Point) -> bool:
    return (
        (left[0] * right[2] - right[0] * left[2]) % _FIELD_PRIME == 0
        and (left[1] * right[2] - right[1] * left[2]) % _FIELD_PRIME == 0
    )


def _is_identity(point: _Point) -> bool:
    return (
        point[0] % _FIELD_PRIME == 0
        and (point[1] - point[2]) % _FIELD_PRIME == 0
    )


def _is_on_curve(x: int, y: int) -> bool:
    x_squared = (x * x) % _FIELD_PRIME
    y_squared = (y * y) % _FIELD_PRIME
    return (
        y_squared
        - x_squared
        - 1
        - _CURVE_D * x_squared * y_squared
    ) % _FIELD_PRIME == 0


def _decode_point(encoded: bytes) -> _Point | None:
    if len(encoded) != 32:
        return None

    value = int.from_bytes(encoded, "little")
    x_sign = value >> 255
    y = value & _Y_MASK
    if y >= _FIELD_PRIME:
        return None

    y_squared = (y * y) % _FIELD_PRIME
    u = (y_squared - 1) % _FIELD_PRIME
    v = (_CURVE_D * y_squared + 1) % _FIELD_PRIME
    if v == 0:
        return None

    v_cubed = (v * v * v) % _FIELD_PRIME
    v_seventh = (v_cubed * v_cubed * v) % _FIELD_PRIME
    x = (
        u
        * v_cubed
        * pow((u * v_seventh) % _FIELD_PRIME, (_FIELD_PRIME - 5) // 8, _FIELD_PRIME)
    ) % _FIELD_PRIME
    root_check = (v * x * x) % _FIELD_PRIME
    if root_check == (-u) % _FIELD_PRIME:
        x = (x * _SQRT_MINUS_ONE) % _FIELD_PRIME
    elif root_check != u:
        return None

    if x == 0 and x_sign:
        return None
    if (x & 1) != x_sign:
        x = _FIELD_PRIME - x
    if not _is_on_curve(x, y):
        return None
    return (x, y, 1, (x * y) % _FIELD_PRIME)


def _is_strict_subgroup_point(point: _Point) -> bool:
    # Reject the identity and every other point whose order divides the cofactor.
    if _is_identity(_scalar_multiply(point, _COFACTOR)):
        return False
    # Reject points with any torsion component, not only the eight small points.
    return _is_identity(_scalar_multiply(point, _GROUP_ORDER))


_BASE_POINT = _decode_point(bytes.fromhex("58" + "66" * 31))
if _BASE_POINT is None:  # pragma: no cover - trusted constant integrity check
    raise RuntimeError("invalid built-in Ed25519 base point")


def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """Return whether ``signature`` is a strict Ed25519 signature of ``message``."""

    if (
        not isinstance(public_key, bytes)
        or not isinstance(signature, bytes)
        or not isinstance(message, bytes)
        or len(public_key) != 32
        or len(signature) != 64
    ):
        return False

    encoded_r = signature[:32]
    scalar_s = int.from_bytes(signature[32:], "little")
    if scalar_s >= _GROUP_ORDER:
        return False

    public_point = _decode_point(public_key)
    signature_point = _decode_point(encoded_r)
    if public_point is None or signature_point is None:
        return False
    if not _is_strict_subgroup_point(public_point):
        return False
    if not _is_strict_subgroup_point(signature_point):
        return False

    challenge = int.from_bytes(
        sha512(encoded_r + public_key + message).digest(), "little"
    ) % _GROUP_ORDER
    left = _scalar_multiply(_BASE_POINT, scalar_s)
    right = _point_add(
        signature_point,
        _scalar_multiply(public_point, challenge),
    )
    return _points_equal(left, right)
