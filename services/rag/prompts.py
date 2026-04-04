"""
Stub/redirect for RAG prompts - imports from common library.

This file is kept for backward compatibility. All prompt functions
are now centralized in services/common/prompts.py
"""

# Import all prompts from common library
from sys import path as sys_path
import os as os_module

# Add common directory to path for import
common_path = os_module.path.join(os_module.path.dirname(__file__), '..', 'common')
if common_path not in sys_path:
    sys_path.insert(0, common_path)

from prompts import (
    get_architect_prompt,
    get_examiner_question_prompt,
    get_examiner_grade_prompt,
    get_socratic_tutor_prompt,
    get_micro_lecture_prompt
)

__all__ = [
    'get_architect_prompt',
    'get_examiner_question_prompt',
    'get_examiner_grade_prompt',
    'get_socratic_tutor_prompt',
    'get_micro_lecture_prompt'
]
