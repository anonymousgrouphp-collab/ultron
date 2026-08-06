import re
import os

with open(r'e:\ultronmain\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Extract TOOL_DECLARATIONS
match = re.search(r'TOOL_DECLARATIONS\s*=\s*\[.*?^\]\n', content, re.DOTALL | re.MULTILINE)
if match:
    tool_decls = match.group(0)
    os.makedirs(r'e:\ultronmain\core', exist_ok=True)
    with open(r'e:\ultronmain\core\tool_declarations.py', 'w', encoding='utf-8') as out:
        out.write(tool_decls)
    content = content.replace(tool_decls, 'from core.tool_declarations import TOOL_DECLARATIONS\n')

with open(r'e:\ultronmain\main_temp.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Extraction done.")
