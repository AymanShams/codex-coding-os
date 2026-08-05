"""Independent standard-library Ed25519 signing helpers for tests only."""

from __future__ import annotations

from hashlib import sha512


_FIELD_PRIME = 2**255 - 19
_GROUP_ORDER = 2**252 + 27742317777372353535851937790883648493
_CURVE_D = (-121665 * pow(121666, _FIELD_PRIME - 2, _FIELD_PRIME)) % _FIELD_PRIME
_SQRT_MINUS_ONE = pow(2, (_FIELD_PRIME - 1) // 4, _FIELD_PRIME)
_IDENTITY = (0, 1)
_ORDER_TWO = (0, _FIELD_PRIME - 1)


def _recover_x(y: int, sign: int) -> int:
    y_squared = y * y % _FIELD_PRIME
    ratio = (y_squared - 1) * pow(
        (_CURVE_D * y_squared + 1) % _FIELD_PRIME,
        _FIELD_PRIME - 2,
        _FIELD_PRIME,
    ) % _FIELD_PRIME
    x = pow(ratio, (_FIELD_PRIME + 3) // 8, _FIELD_PRIME)
    if x * x % _FIELD_PRIME != ratio:
        x = x * _SQRT_MINUS_ONE % _FIELD_PRIME
    if x * x % _FIELD_PRIME != ratio:
        raise ValueError("point is not on Ed25519")
    if x == 0 and sign:
        raise ValueError("non-canonical Ed25519 point")
    if (x & 1) != sign:
        x = _FIELD_PRIME - x
    return x


def _decode_point(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise ValueError("Ed25519 points are 32 bytes")
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _FIELD_PRIME:
        raise ValueError("non-canonical Ed25519 point")
    return _recover_x(y, sign), y


def _encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    encoded = bytearray((y % _FIELD_PRIME).to_bytes(32, "little"))
    encoded[31] |= (x & 1) << 7
    return bytes(encoded)


def _point_add(
    left: tuple[int, int], right: tuple[int, int]
) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    product = _CURVE_D * x1 * x2 * y1 * y2 % _FIELD_PRIME
    x = (x1 * y2 + y1 * x2) * pow(
        (1 + product) % _FIELD_PRIME, _FIELD_PRIME - 2, _FIELD_PRIME
    ) % _FIELD_PRIME
    y = (y1 * y2 + x1 * x2) * pow(
        (1 - product) % _FIELD_PRIME, _FIELD_PRIME - 2, _FIELD_PRIME
    ) % _FIELD_PRIME
    return x, y


def _scalar_multiply(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


_BASE_POINT = _decode_point(bytes.fromhex("58" + "66" * 31))


def _secret_scalar_and_prefix(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519 seeds are 32 bytes")
    hashed = sha512(seed).digest()
    pruned = bytearray(hashed[:32])
    pruned[0] &= 248
    pruned[31] &= 63
    pruned[31] |= 64
    return int.from_bytes(pruned, "little"), hashed[32:]


def public_key_from_seed(seed: bytes) -> bytes:
    scalar, _ = _secret_scalar_and_prefix(seed)
    return _encode_point(_scalar_multiply(_BASE_POINT, scalar))


def sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _secret_scalar_and_prefix(seed)
    public_key = _encode_point(_scalar_multiply(_BASE_POINT, scalar))
    nonce = int.from_bytes(sha512(prefix + message).digest(), "little") % _GROUP_ORDER
    encoded_r = _encode_point(_scalar_multiply(_BASE_POINT, nonce))
    challenge = int.from_bytes(
        sha512(encoded_r + public_key + message).digest(), "little"
    ) % _GROUP_ORDER
    scalar_s = (nonce + challenge * scalar) % _GROUP_ORDER
    return encoded_r + scalar_s.to_bytes(32, "little")


def forge_mixed_order_signature(seed: bytes, message: bytes) -> tuple[bytes, bytes]:
    """Create an exact-equation signature for a key with order-two torsion."""

    scalar, prefix = _secret_scalar_and_prefix(seed)
    prime_public = _scalar_multiply(_BASE_POINT, scalar)
    mixed_public = _point_add(prime_public, _ORDER_TWO)
    encoded_public = _encode_point(mixed_public)
    for counter in range(256):
        nonce = int.from_bytes(
            sha512(prefix + message + counter.to_bytes(2, "little")).digest(),
            "little",
        ) % _GROUP_ORDER
        prime_r = _scalar_multiply(_BASE_POINT, nonce)
        for has_torsion in (0, 1):
            point_r = (
                _point_add(prime_r, _ORDER_TWO) if has_torsion else prime_r
            )
            encoded_r = _encode_point(point_r)
            challenge = int.from_bytes(
                sha512(encoded_r + encoded_public + message).digest(), "little"
            ) % _GROUP_ORDER
            if (has_torsion + challenge) & 1:
                continue
            scalar_s = (nonce + challenge * scalar) % _GROUP_ORDER
            return encoded_public, encoded_r + scalar_s.to_bytes(32, "little")
    raise AssertionError("could not construct deterministic mixed-order test vector")


def verifies_without_subgroup_checks(
    public_key: bytes, signature: bytes, message: bytes
) -> bool:
    """Evaluate the exact equation without production subgroup restrictions."""

    public_point = _decode_point(public_key)
    signature_point = _decode_point(signature[:32])
    scalar_s = int.from_bytes(signature[32:], "little")
    challenge = int.from_bytes(
        sha512(signature[:32] + public_key + message).digest(), "little"
    ) % _GROUP_ORDER
    left = _scalar_multiply(_BASE_POINT, scalar_s)
    right = _point_add(
        signature_point, _scalar_multiply(public_point, challenge)
    )
    return left == right
