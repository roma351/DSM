import secrets
import base64


if __name__ == "__main__":
    key_bytes = secrets.token_bytes(64)

    key_base64 = base64.b64encode(key_bytes).decode('utf-8')
    key_hex = key_bytes.hex()

    print("Token")
    print(f"Hex: {key_hex}")
    print(f"Base64: {key_base64}")
    print(f"Length: {len(key_bytes)} bytes ({len(key_bytes) * 8} bit)")
