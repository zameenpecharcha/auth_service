from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os



def get_db_engine():
    try:
        load_dotenv()
        DB_USER = os.getenv("DB_USER")
        DB_PASSWORD = os.getenv("DB_PASSWORD")
        DB_HOST = os.getenv("DB_HOST")
        DB_PORT = int(os.getenv("DB_PORT", "5434"))
        DB_NAME = os.getenv("DB_NAME", "postgres")  # Changed default to postgres
        
        # Print configuration (without password)
        print(f"Attempting to connect to database with:")
        print(f"User: {DB_USER}")
        print(f"Host: {DB_HOST}")
        print(f"Port: {DB_PORT}")
        print(f"Database: {DB_NAME}")
        
        if not all([DB_USER, DB_PASSWORD, DB_HOST, DB_NAME]):
            raise ValueError("Missing database configuration. Please check your .env file.")
            
        DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        engine = create_engine(DATABASE_URL)
        
        # Test the connection with proper SQLAlchemy query
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
            
        print("Connected to PostgreSQL database successfully!")
        return engine
    except Exception as e:
        print(f"Database connection error: {str(e)}")
        raise

