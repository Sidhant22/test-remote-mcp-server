# Sample Server
from fastmcp import FastMCP
import random
import json

# FastMCP Server instance
mcp = FastMCP("Simple Calculator Server")

# Tool: Add two numbers
@mcp.tool
def add_numbers(a: float, b: float) -> float:
    """Add two numbers.
    Args:
        a: First number
        b: Second number

    Returns:
        The sum of a and b
    """
    return a + b

# Tool-2: To generate a random number between a given range
@mcp.tool
def generate_random_number(min_value: int = 1, max_value: int = 100) -> int:
    """Generate a random number between a given range.
    Args:
        min_value: Minimum value of the range
        max_value: Maximum value of the range

    Returns:
        A random number between min_value and max_value
    """
    return random.randint(min_value, max_value)

# Resource: That gives the server information
@mcp.resource("info://server")
def server_info() -> dict:
    """Get server information.
    Returns:
        A dictionary containing server information
    """
    info = {
        "name": "Simple Calculator Server",
        "version": "1.0",
        "description": "A basic MCP server with simple math tools",
        "tools": ["add_numbers", "generate_random_number"],
        "author": "Sidhant Dorge"
    }
    return json.dumps(info, indent=2)

# Start the server:
if __name__ == "__main__":
    # Here is a subtle difference that we see when using a remote mcp server over local mcp server
    # When we just specify: mcp.run(): Then FastMCP assumes the transport: stdio
    mcp.run(transport="http", host="0.0.0.0", port= 8080)