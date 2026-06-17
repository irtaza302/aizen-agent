import re
from aizen.config import DANGEROUS_PATTERNS
from aizen.main import inject_file_context
print(inject_file_context('@cmd:"rm -rf /"'))
