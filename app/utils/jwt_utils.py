import jwt
import uuid
from datetime import datetime, timedelta

from auth_service.app.utils.token_blacklist import is_token_blacklisted

# Load your private key once at module level
with open("config/private.pem", "r") as f:
    PRIVATE_KEY = f.read()
with open("config/public.pem", "r") as f:
    PUBLIC_KEY = f.read()


def generate_tokens(user):
    now = datetime.utcnow()

    session_id = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())

    access_payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "session_id": session_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=180),
        "iss": "ZPC",
        "aud": "graphql-api"
    }

    refresh_payload = {
        "sub": str(user.id),
        "email": user.email,
        "jti": refresh_jti,
        "iat": now,
        "exp": now + timedelta(days=7),
        "iss": "ZPC",
        "aud": "graphql-api"
    }

    access_token = jwt.encode(access_payload, PRIVATE_KEY, algorithm="RS256")
    refresh_token = jwt.encode(refresh_payload, PRIVATE_KEY, algorithm="RS256")

    return access_token, refresh_token


def verify_jwt_token(token):
    """Helper function to verify JWT token and return user info"""
    try:
        # Check if token is blacklisted
        if is_token_blacklisted(token):
            return None, "Token is blacklisted"
        # Decode token
        payload = decode_token(token)
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, "Token has expired"
    except jwt.ImmatureSignatureError:
        return None, "Token not valid yet (nbf)"
    except jwt.InvalidTokenError as e:
        return None, f"Invalid token: {str(e)}"
    except Exception as e:
        return None, f"Token verification error: {str(e)}"



def decode_token(token: str):
    return jwt.decode(
        token,
        PUBLIC_KEY,
        algorithms=["RS256"],
        audience="graphql-api",
        issuer="ZPC"
    )