from fastmcp import Client

async def test_mcp_servers():
    client = Client({
        "enhanced_mcp": {
            "url": "http://a7a09ec61615e46a7892d050e514c11e-1977986439.us-east-2.elb.amazonaws.com/mcp",
            "transport": "streamable-http"
            }
        })
        
    async with client:
        fastmcp_tools = await client.list_tools()
        tool_information_nice_print = ""
        for tool in fastmcp_tools:
            tool_information_nice_print += f"Tool Name: {tool.name}\n"
            tool_information_nice_print += f"Description: {tool.description}\n"
            tool_information_nice_print += f"Input Schema: {tool.inputSchema}\n"
            tool_information_nice_print += f"Output Schema: {tool.outputSchema}\n"
            tool_information_nice_print += "\n"
        print(tool_information_nice_print)

        # tools = [convert_fastmcp_tool_to_openai_format(tool) for tool in fastmcp_tools]
        # tool_name_mapping = {
        #     sanitize_tool_name(tool.name): tool.name 
        #         for tool in fastmcp_tools
        #     }

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_mcp_servers())
    