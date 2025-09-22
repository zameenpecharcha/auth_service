from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, TIMESTAMP, Boolean, Float, BigInteger, text
from app.utils.db_connection import get_db_engine

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)  # Changed from user_id to id
    first_name = Column(String(50))
    last_name = Column(String(50))
    email = Column(String(100), unique=True, nullable=False)
    phone = Column(String(20))
    profile_photo = Column(String(255))
    role = Column(String(50))
    address = Column(String(255))
    latitude = Column(Float)
    longitude = Column(Float)
    bio = Column(String(500))
    password = Column(String(100), nullable=False)
    isactive = Column(Boolean, server_default=text('true'))
    email_verified = Column(Boolean, server_default=text('false'))
    phone_verified = Column(Boolean, server_default=text('false'))
    last_login_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

class UserRating(Base):
    __tablename__ = "user_ratings"

    id = Column(BigInteger, primary_key=True)
    rated_user_id = Column(BigInteger, nullable=False)
    rated_by_user_id = Column(BigInteger, nullable=False)
    rating_value = Column(Integer)
    review = Column(String)
    rating_type = Column(String(50))
    created_at = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

class UserFollower(Base):
    __tablename__ = "user_followers"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    following_id = Column(BigInteger, nullable=False)
    status = Column(String(20), server_default='active')
    followed_at = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

# Initialize the database engine
engine = get_db_engine()

# Only create tables if they don't exist
Base.metadata.create_all(engine)

print("Database tables verified!")

# Initialize the database session
SessionLocal = sessionmaker(bind=engine)

