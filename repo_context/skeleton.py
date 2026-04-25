#!/usr/bin/env python3
"""
repo_context/skeleton.py — AST Skeleton generation using tree-sitter.
"""
import os
import tree_sitter
from tree_sitter import Language, Parser

def get_parser(extension: str):
    ext = extension.lower()
    try:
        if ext == ".py":
            from tree_sitter_python import language as ts_py
            lang = Language(ts_py())
        elif ext in [".js", ".jsx"]:
            from tree_sitter_javascript import language as ts_js
            lang = Language(ts_js())
        elif ext in [".ts", ".tsx"]:
            import tree_sitter_typescript as ts_ts
            if ext == ".tsx":
                lang = Language(ts_ts.language_tsx())
            else:
                lang = Language(ts_ts.language_typescript())
        elif ext in [".html", ".htm"]:
            from tree_sitter_html import language as ts_html
            lang = Language(ts_html())
        else:
            return None
            
        parser = Parser()
        parser.language = lang
        return parser
    except ImportError:
        return None

def generate_skeleton(source_code: str, file_path: str) -> str:
    """Generates a token-optimized Skeleton AST summary of the source code."""
    ext = os.path.splitext(file_path)[1]
    parser = get_parser(ext)
    
    if not parser:
        # Fallback to lines truncation
        lines = source_code.splitlines()
        if len(lines) <= 150:
            return source_code
        return "\n".join(lines[:100] + ["\n... [TRUNCATED] ...\n"] + lines[-50:])
        
    tree = parser.parse(source_code.encode("utf-8"))
    collapse_ranges = []
    
    def walk(node):
        # Python
        if node.type in ['function_definition', 'class_definition']:
            body = node.child_by_field_name('body')
            if body:
                # keep docstring if it exists
                first_stmt = body.children[0] if body.children else None
                if first_stmt and first_stmt.type == 'expression_statement':
                    # Has a docstring, collapse after it
                    collapse_ranges.append((first_stmt.end_byte, body.end_byte))
                else:
                    collapse_ranges.append((body.start_byte, body.end_byte))
        
        # JS / TS
        elif node.type in ['function_declaration', 'method_definition', 'class_declaration', 'arrow_function']:
            body = node.child_by_field_name('body')
            if body:
                if body.type == 'statement_block':
                    # keep the `{` and `}`
                    collapse_ranges.append((body.start_byte + 1, body.end_byte - 1))
                else:
                    collapse_ranges.append((body.start_byte, body.end_byte))

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    
    if not collapse_ranges:
        return source_code
        
    collapse_ranges.sort(key=lambda x: x[0])
    
    encoded = source_code.encode("utf-8")
    parts = []
    last_end = 0
    for start, end in collapse_ranges:
        if start < last_end:
             continue # Nested body, already collapsed
        parts.append(encoded[last_end:start].decode("utf-8", errors="replace"))
        parts.append("\n    ... [BODY EXCLUDED] ...\n")
        last_end = end
        
    parts.append(encoded[last_end:].decode("utf-8", errors="replace"))
    
    return "".join(parts)
