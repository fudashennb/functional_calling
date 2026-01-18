import inspect
import json
import functools
from typing import Callable, Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class ToolRegistry:
    """
    Global registry for tools.
    Allows registering functions as tools and generating JSON schemas for LLMs.
    """
    _tools: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register(cls, name: str, description: str):
        """
        Decorator to register a function as a tool.
        """
        def decorator(func: Callable):
            schema = cls._generate_schema(func, name, description)
            cls._tools[name] = {
                "func": func,
                "schema": schema,
                "name": name,
                "description": description
            }
            
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)
            return wrapper
        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Optional[Callable]:
        entry = cls._tools.get(name)
        return entry["func"] if entry else None

    @classmethod
    def get_schema(cls, name: str) -> Optional[Dict[str, Any]]:
        entry = cls._tools.get(name)
        return entry["schema"] if entry else None

    @classmethod
    def get_all_schemas(cls) -> List[Dict[str, Any]]:
        return [entry["schema"] for entry in cls._tools.values()]

    @classmethod
    def get_schemas_by_names(cls, names: List[str]) -> List[Dict[str, Any]]:
        schemas = []
        for name in names:
            entry = cls._tools.get(name)
            if entry:
                schemas.append(entry["schema"])
            else:
                logger.warning(f"Tool {name} not found in registry")
        return schemas

    @classmethod
    async def execute(cls, name: str, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """
        Execute a tool by name with arguments.
        Returns the result as a string.
        """
        tool_func = cls.get_tool(name)
        if not tool_func:
            return f"Error: Tool '{name}' not found."

        if context:
            # Inject context if the function accepts it
            sig = inspect.signature(tool_func)
            for param_name in context:
                if param_name in sig.parameters:
                    arguments[param_name] = context[param_name]

        try:
            # Check if the function is a coroutine
            if inspect.iscoroutinefunction(tool_func):
                result = await tool_func(**arguments)
            else:
                result = tool_func(**arguments)
            
            return str(result)
        except Exception as e:
            logger.error(f"Error executing tool {name}: {e}", exc_info=True)
            return f"Error executing tool '{name}': {str(e)}"

    @staticmethod
    def _generate_schema(func: Callable, name: str, description: str) -> Dict[str, Any]:
        """
        Generate OpenAI function schema from function signature.
        """
        sig = inspect.signature(func)
        parameters = {
            "type": "object",
            "properties": {},
            "required": []
        }

        ignored_params = {"self", "emit", "stop_event", "context"}

        for param_name, param in sig.parameters.items():
            if param_name in ignored_params or param_name.startswith("_"):
                continue
            
            # Default type is string if not annotated
            param_type = "string"
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == bool:
                param_type = "boolean"
            elif param.annotation == float:
                param_type = "number"
            elif param.annotation == dict:
                param_type = "object"
            elif param.annotation == list:
                param_type = "array"

            param_desc = f"参数 {param_name}"
            if param.default != inspect.Parameter.empty:
                param_desc += f" (默认值: {param.default})"
            
            parameters["properties"][param_name] = {
                "type": param_type,
                "description": param_desc
            }
            
            if param.default == inspect.Parameter.empty:
                parameters["required"].append(param_name)

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters
            }
        }

