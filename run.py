"""
run.py
------
Production-like launcher: starts gunicorn with the Django WSGI app.
Usage: python run.py
"""
import os
import subprocess
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'edtech.settings')

if __name__ == '__main__':
    cmd = [
        sys.executable, '-m', 'gunicorn',
        'edtech.wsgi:application',
        '--bind', '0.0.0.0:8000',
        '--workers', '1',
        '--threads', '4',
        '--timeout', '120',
        '--access-logfile', '-',
    ]
    subprocess.run(cmd)
