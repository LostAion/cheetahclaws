import libcst as cst
from libcst.matchers import matches, FunctionDef, ClassDef, Name

class NodeReplacer(cst.CSTTransformer):
    def __init__(self, target_type: str, target_name: str, new_source: str):
        self.target_type = target_type.lower()
        self.target_name = target_name
        self.new_source = new_source
        self.replaced = False

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.CSTNode:
        if self.target_type in ["function", "functiondef", "method"] and original_node.name.value == self.target_name:
            # Parse new source into a node
            new_node = cst.parse_statement(self.new_source)
            self.replaced = True
            return new_node
        return updated_node

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
        if self.target_type in ["class", "classdef"] and original_node.name.value == self.target_name:
            new_node = cst.parse_statement(self.new_source)
            self.replaced = True
            return new_node
        return updated_node

def mutate_ast(source_code: str, target_type: str, target_name: str, new_source: str) -> str:
    """Mutate AST by replacing a function or class definition entirely."""
    module = cst.parse_module(source_code)
    replacer = NodeReplacer(target_type, target_name, new_source)
    modified_module = module.visit(replacer)
    if not replacer.replaced:
        raise ValueError(f"Target {target_type} '{target_name}' not found in AST.")
    return modified_module.code
