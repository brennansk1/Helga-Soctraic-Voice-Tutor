#!/usr/bin/env python3
"""
Sudo Password Management Documentation

This module adds secure sudo password handling to main.py for operations
that require elevated privileges (like fixing database permissions).

## Features

1. **Secure Password Prompting**: Uses getpass to securely prompt for password
2. **In-Memory Storage**: Password is stored only in memory, never written to disk
3. **Validation**: Password is validated before being stored
4. **Helper Function**: run_sudo_command() provides easy sudo execution

## Usage in main.py

The password is prompted automatically when main.py runs:

```python
python main.py
# User will be prompted: "Enter sudo password (or press Enter to skip):"
```

## Usage in Other Scripts

To use the sudo helper from other scripts:

```python
from main import run_sudo_command, SUDO_PASSWORD

# Check if password is available
if SUDO_PASSWORD:
    success, stdout, stderr = run_sudo_command(['chown', '-R', 'user:user', '/path'])
    if success:
        print("Permission fixed successfully")
else:
    print("No sudo password available")
```

## Database Permission Fix

The db_manager.py now automatically fixes permissions after database swaps:

```python
# In db_manager.py atomic_swap_database()
subprocess.run(['chmod', '-R', '755', self.DB_MAIN_PATH], check=True)
```

This ensures the rag-engine can read the database after ingestion.

## Security Notes

- Password is stored in memory only (SUDO_PASSWORD global variable)
- Password is cleared when the process exits
- Password is never logged or written to disk
- User can skip password entry by pressing Enter
- Password is validated before storage

## Example Flow

1. User runs: `python main.py`
2. System prompts for sudo password
3. Password is validated with `sudo -S echo validated`
4. Password is stored in SUDO_PASSWORD global
5. During ingestion, db_manager uses subprocess.run with sudo
6. After ingestion, permissions are fixed automatically
7. Password is cleared when main.py exits
"""
