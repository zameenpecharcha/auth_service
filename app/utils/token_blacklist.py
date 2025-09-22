import hashlib
import jwt
from auth_service.app.utils.redis_utils import get_redis_client

# Set up Redis connection (assuming it's already configured)
redis_client = get_redis_client()
# TTLs
ACCESS_TOKEN_TTL = 180 * 60          # 180 minutes
REFRESH_TOKEN_TTL = 7 * 24 * 60 * 60  # 7 days

# Memory fallback store (only used if Redis fails)
class TokenBlacklistMemory:
    def __init__(self):
        self.blacklisted_sessions = set()
        self.blacklisted_refreshes = set()

    def add_session_id(self, session_id):
        session_hash = hashlib.sha256(session_id.encode()).hexdigest()
        self.blacklisted_sessions.add(session_hash)

    def add_refresh_jti(self, jti):
        jti_hash = hashlib.sha256(jti.encode()).hexdigest()
        self.blacklisted_refreshes.add(jti_hash)

    def is_session_blacklisted(self, session_id):
        session_hash = hashlib.sha256(session_id.encode()).hexdigest()
        return session_hash in self.blacklisted_sessions

    def is_refresh_blacklisted(self, jti):
        jti_hash = hashlib.sha256(jti.encode()).hexdigest()
        return jti_hash in self.blacklisted_refreshes

# Singleton fallback memory store
memory_store = TokenBlacklistMemory()

# === Redis-backed blacklist ===

def store_blacklisted_session_id(session_id: str):
    try:
        hashed = hashlib.sha256(session_id.encode()).hexdigest()
        redis_client.setex(f"blacklisted_session:{hashed}", ACCESS_TOKEN_TTL, "1")
    except Exception as e:
        print(f"[Redis Error] Failed to blacklist session ID: {e}")
        memory_store.add_session_id(session_id)

def store_blacklisted_refresh_jti(jti: str):
    try:
        hashed = hashlib.sha256(jti.encode()).hexdigest()
        redis_client.setex(f"blacklisted_refresh:{hashed}", REFRESH_TOKEN_TTL, "1")
    except Exception as e:
        print(f"[Redis Error] Failed to blacklist refresh JTI: {e}")
        memory_store.add_refresh_jti(jti)

def is_session_id_blacklisted(session_id: str) -> bool:
    try:
        hashed = hashlib.sha256(session_id.encode()).hexdigest()
        return redis_client.exists(f"blacklisted_session:{hashed}") == 1
    except Exception as e:
        print(f"[Redis Error] Session ID fallback check: {e}")
        return memory_store.is_session_blacklisted(session_id)

def is_refresh_jti_blacklisted(jti: str) -> bool:
    try:
        hashed = hashlib.sha256(jti.encode()).hexdigest()
        return redis_client.exists(f"blacklisted_refresh:{hashed}") == 1
    except Exception as e:
        print(f"[Redis Error] Refresh JTI fallback check: {e}")
        return memory_store.is_refresh_blacklisted(jti)

def is_token_blacklisted(token: str) -> bool:
    """
    Check if the token is blacklisted based on session_id or jti.
    """
    try:
        # Decode token without verifying the signature to extract payload
        unverified_payload = jwt.decode(token, options={"verify_signature": False})

        session_id = unverified_payload.get("session_id")
        jti = unverified_payload.get("jti")

        if session_id and is_session_id_blacklisted(session_id):
            return True

        if jti and is_refresh_jti_blacklisted(jti):
            return True

        return False
    except Exception as e:
        print(f"[Token Blacklist Error] Failed to check blacklist: {e}")
        return True  # Fail-safe: treat token as blacklisted if parsing fails
