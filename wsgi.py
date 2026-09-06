"""
wsgi.py
WSGI entry point for deploying the Flask backend on aaPanel / Gunicorn.
"""

from app import app

if __name__ == "__main__":
    app.run()
