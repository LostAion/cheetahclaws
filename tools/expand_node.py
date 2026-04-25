import os
from repo_context.skeleton import get_parser

def _find_node_by_name(node, target_name):
    # Depending on language, 'name' might be the field name.
    # In tree-sitter, we can look for child_by_field_name('name')
    name_node = node.child_by_field_name('name')
    if name_node:
        if name_node.text.decode('utf-8') == target_name:
            return node
            
    for child in node.children:
        found = _find_node_by_name(child, target_name)
        if found:
            return found
    return None

def expand_node(params: dict, config: dict) -> str:
    """Retrieves the full implementation of a specified parsed node."""
    file_path = params.get("file_path")
    node_name = params.get("node_name")
    
    if not file_path or not node_name:
        return "Error: missing required parameters (file_path, node_name)."
        
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
        
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()
        
    ext = os.path.splitext(file_path)[1]
    parser = get_parser(ext)
    
    if not parser:
        return "Error: no parser available for this file type. Cannot expand node."
        
    tree = parser.parse(source_code.encode("utf-8"))
    
    # We look for the node_name. For e.g. "my_function" or "MyClass"
    target_node = _find_node_by_name(tree.root_node, node_name)
    if target_node:
        start = target_node.start_byte
        end = target_node.end_byte
        return source_code.encode("utf-8")[start:end].decode("utf-8", errors="replace")
        
    return f"Node `{node_name}` not found in {file_path}."
