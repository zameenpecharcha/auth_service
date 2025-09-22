import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from entity.user_entity import Base
from utils.db_connection import get_db_engine

def init_db():
    engine = get_db_engine()
    Base.metadata.create_all(engine)
    print("All tables created (if not already present).")

if __name__ == "__main__":
    init_db() 