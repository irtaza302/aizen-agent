import os
from typing import Optional

try:
    import tree_sitter
except ImportError:
    tree_sitter = None

def get_parser(language_str: str) -> Optional['tree_sitter.Parser']:
    if tree_sitter is None:
        return None
        
    try:
        if language_str == "python":
            import tree_sitter_python
            lang = tree_sitter.Language(tree_sitter_python.language())
        elif language_str in ("javascript", "js", "jsx"):
            import tree_sitter_javascript
            lang = tree_sitter.Language(tree_sitter_javascript.language())
        elif language_str in ("typescript", "ts", "tsx"):
            import tree_sitter_typescript
            if language_str == "tsx":
                lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
            else:
                lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        else:
            return None
            
        parser = tree_sitter.Parser(lang)
        return parser
    except ImportError:
        return None

def extract_outline(filepath: str, content: str) -> Optional[str]:
    ext = os.path.splitext(filepath)[1].lower()
    
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx"
    }
    
    lang_str = lang_map.get(ext)
    if not lang_str:
        return None
        
    parser = get_parser(lang_str)
    if not parser:
        return None
        
    tree = parser.parse(content.encode("utf-8"))
    
    outline = [f"File: {filepath}\n"]
    
    def traverse(node, depth=0):
        indent = "    " * depth
        if lang_str == "python":
            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    outline.append(f"{indent}class {name_node.text.decode('utf-8')}:")
                    for child in node.children:
                        if child.type == "block":
                            traverse(child, depth + 1)
            elif node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    outline.append(f"{indent}def {name_node.text.decode('utf-8')}(...):")
            else:
                for child in node.children:
                    traverse(child, depth)
        elif lang_str in ("javascript", "typescript", "tsx", "jsx"):
            if node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    outline.append(f"{indent}class {name_node.text.decode('utf-8')} {{")
                    for child in node.children:
                        if child.type == "class_body":
                            traverse(child, depth + 1)
            elif node.type in ("function_declaration", "method_definition", "generator_function_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    prefix = "async " if node.type == "generator_function_declaration" else ""
                    outline.append(f"{indent}{prefix}function {name_node.text.decode('utf-8')}(...)")
            elif node.type in ("lexical_declaration", "variable_declaration", "export_statement"):
                for child in node.children:
                    if child.type == "variable_declarator":
                        name_node = child.child_by_field_name("name")
                        value_node = child.child_by_field_name("value")
                        if name_node and value_node and value_node.type == "arrow_function":
                            outline.append(f"{indent}const {name_node.text.decode('utf-8')} = (...) => {{...}}")
                    traverse(child, depth)
            else:
                for child in node.children:
                    traverse(child, depth)
                    
    traverse(tree.root_node)
    
    if len(outline) == 1:
        return outline[0] + "\nNo classes or functions found."
    return "\n".join(outline)

def find_function_lines(filepath: str, content: str, function_name: str) -> Optional[tuple[int, int]]:
    ext = os.path.splitext(filepath)[1].lower()
    
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx"
    }
    
    lang_str = lang_map.get(ext)
    if not lang_str:
        return None
        
    parser = get_parser(lang_str)
    if not parser:
        return None
        
    tree = parser.parse(content.encode("utf-8"))
    
    found_node = None
    
    def traverse(node):
        nonlocal found_node
        if found_node:
            return
            
        if lang_str == "python":
            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == function_name:
                    found_node = node
                    return
        elif lang_str in ("javascript", "typescript", "tsx", "jsx"):
            if node.type in ("function_declaration", "method_definition", "generator_function_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == function_name:
                    found_node = node
                    return
            elif node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node and name_node.text.decode("utf-8") == function_name and value_node and value_node.type == "arrow_function":
                    found_node = node.parent  # Return the whole lexical declaration
                    return
                    
        for child in node.children:
            traverse(child)
            
    traverse(tree.root_node)
    
    if found_node:
        # tree-sitter lines are 0-indexed. We want 1-indexed.
        start_line = found_node.start_point[0] + 1
        end_line = found_node.end_point[0] + 1
        return (start_line, end_line)
        
    return None
