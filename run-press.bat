@echo off
rem inferdiag 一键压测：4 并发 x 16 请求 x 500 token（默认连本机 8000）
cd /d "%~dp0"
python scripts\pressure_test.py --url http://localhost:8000/v1/chat/completions --model /home/liyou/qwen3b-awq --workers 4 --requests 16 --max-tokens 500
pause
