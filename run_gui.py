import sys
import os

# Set search path to ensure we can load server correctly
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui"))
from gui import server

if __name__ == "__main__":
    try:
        server.main()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
