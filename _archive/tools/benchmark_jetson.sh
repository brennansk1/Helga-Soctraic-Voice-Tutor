#!/bin/bash
echo "Starting Jetson Benchmark..."
tegrastats --interval 1000 --logfile hardware_log.txt &
PID=$!
python3 tests/test_hardware_limits.py
kill $PID
echo "Benchmark complete."
