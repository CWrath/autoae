import importlib
import os
import sys

def load_tools():
    """Load all tool modules from the tools directory"""
    app_root = os.path.dirname(os.path.abspath(__file__))
    tool_dir = os.path.join(app_root, "tools")
    tool_instances = []

    # Ensure tools directory exists
    if not os.path.exists(tool_dir):
        os.makedirs(tool_dir)
        if sys.stdout is not None:
            print(f"Created {tool_dir} directory")
        return tool_instances

    # Use the app root for imports, even when the app is launched from elsewhere.
    if app_root not in sys.path:
        sys.path.insert(0, app_root)

    # Load each tool module
    for file in sorted(os.listdir(tool_dir)):
        if file.endswith(".py") and not file.startswith("__"):
            module_name = file[:-3]
            module_path = f"tools.{module_name}"
            
            try:
                # Import the module
                mod = importlib.import_module(module_path)
                
                # Look for Tool class in the module
                if hasattr(mod, 'Tool'):
                    tool_class = getattr(mod, 'Tool')
                    # Create instance of the tool
                    instance = tool_class()
                    tool_instances.append(instance)
                    if sys.stdout is not None:
                        print(f"Loaded tool: {instance.name}")
                else:
                    if sys.stderr is not None:
                        print(f"Warning: {module_name} does not have a Tool class", file=sys.stderr)
                    
            except Exception as e:
                if sys.stderr is not None:
                    print(f"Error loading {module_name}: {e}", file=sys.stderr)
                    import traceback
                    traceback.print_exc()
    
    return tool_instances