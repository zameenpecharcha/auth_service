import os
import sys

# Ensure the current directory (auth_service/) is on the path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from app.service.auth_service import serve

if __name__ == "__main__":
    serve() 