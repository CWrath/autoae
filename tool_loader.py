import ast
import importlib
import os
import sys
import tokenize
import traceback


class LazyTool:
    """Launcher metadata with a tool instance created only when it is opened."""

    def __init__(self, module_path, metadata):
        self.module_path = module_path
        self.name = metadata["name"]
        self.description = metadata["description"]
        self.category = metadata.get("category")
        self.icon = metadata.get("icon")
        self._instance = None

    def create_window(self, parent):
        if self._instance is None:
            module = importlib.import_module(self.module_path)
            self._instance = module.Tool()
        return self._instance.create_window(parent)

    def close_window(self):
        if self._instance is not None:
            close = getattr(self._instance, "close_window", None)
            if close:
                close()

    def close_app(self):
        if self._instance is not None:
            close = getattr(self._instance, "close_app", None)
            if close:
                close()


def _launcher_metadata(path):
    """Read literal launcher attributes from a simple Tool constructor."""
    with tokenize.open(path) as source:
        tree = ast.parse(source.read(), filename=path)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Tool"]
    if len(classes) != 1:
        return None
    constructors = [
        node for node in classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__"
    ]
    if len(constructors) != 1 or isinstance(constructors[0], ast.AsyncFunctionDef):
        return None
    constructor = constructors[0]
    fields = {"name", "description", "category", "icon"}
    metadata = {}
    for node in ast.walk(constructor):
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Store):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id != "self" or node.attr not in fields:
            continue
        assignments = [
            statement for statement in constructor.body
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1
            and statement.targets[0] is node
        ]
        if not assignments or node.attr in metadata:
            return None
        value = assignments[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, (str, type(None))):
            return None
        metadata[node.attr] = value.value
    if not isinstance(metadata.get("name"), str) or not isinstance(metadata.get("description"), str):
        return None
    return metadata


def load_tools():
    """Discover tools without importing those with static launcher metadata."""
    app_root = os.path.dirname(os.path.abspath(__file__))
    tool_dir = os.path.join(app_root, "tools")
    tool_instances = []

    if not os.path.exists(tool_dir):
        os.makedirs(tool_dir)
        if sys.stdout is not None:
            print(f"Created {tool_dir} directory")
        return tool_instances

    if app_root not in sys.path:
        sys.path.insert(0, app_root)

    for file in sorted(os.listdir(tool_dir)):
        if not file.endswith(".py") or file.startswith("__"):
            continue
        module_name = file[:-3]
        module_path = f"tools.{module_name}"
        try:
            metadata = _launcher_metadata(os.path.join(tool_dir, file))
            if metadata is not None:
                tool_instances.append(LazyTool(module_path, metadata))
                continue
            module = importlib.import_module(module_path)
            if hasattr(module, "Tool"):
                tool_instances.append(module.Tool())
            elif sys.stderr is not None:
                print(f"Warning: {module_name} does not have a Tool class", file=sys.stderr)
        except Exception as error:
            if sys.stderr is not None:
                print(f"Error loading {module_name}: {error}", file=sys.stderr)
                traceback.print_exc()

    return tool_instances
